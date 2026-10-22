from functools import lru_cache

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", env_ignore_empty=True, extra="ignore"
    )

    # Secrets
    discord_token: SecretStr = SecretStr("")
    deadlock_api_key: SecretStr | None = None
    steam_api_key: SecretStr | None = None
    database_url: str = "postgresql+asyncpg://deadbot:deadbot@localhost:5432/deadbot"
    redis_url: str = "redis://localhost:6379/0"

    # Discord
    dev_guild_id: int | None = Field(
        default=None, description="Sync slash commands to this guild only (instant updates)."
    )
    enable_presence_intent: bool = False

    # Deadlock API
    deadlock_api_base_url: str = "https://api.deadlock-api.com"
    http_timeout_s: float = 30.0
    http_max_retries: int = 5
    http_max_backoff_s: float = 60.0
    bulk_metadata_per_minute: int = 25  # API allows 30/min per IP; leave headroom
    bulk_metadata_chunk: int = 250

    # Cache TTLs (seconds)
    ttl_match_history: int = 300
    ttl_mate_stats: int = 1800
    ttl_rank: int = 1800
    ttl_hero_stats: int = 3600
    ttl_steam_profiles: int = 6 * 3600
    ttl_assets: int = 24 * 3600
    ttl_reports: int = 600

    # Sync
    sync_interval_minutes: int = 30
    sync_batch_size: int = 10
    sync_detail_mates: int = Field(
        default=25, description="Top-N mates whose shared matches get per-match hero detail."
    )
    session_gap_minutes: int = 90

    # Stats
    teammates_min_games: int = 3
    profile_min_games: int = 10
    synergy_prior_strength: float = Field(
        default=20.0, description="Pseudo-games of shrinkage toward expected win rate."
    )
    hero_pair_min_games: int = 5
    recent_window_days: int = 90

    # Verification
    verify_code_ttl_minutes: int = 30

    # Matching: weights of each fit component (normalized to sum to 1 at use)
    match_w_synergy: float = 0.35
    match_w_role: float = 0.25
    match_w_availability: float = 0.25
    match_w_rank: float = 0.15
    synergy_scale: float = Field(
        default=0.10, description="Synergy (win-rate points) that maps to a full score."
    )
    rank_gap_scale: float = Field(
        default=12.0, description="Subrank gap that zeroes the rank score (6 subranks = 1 tier)."
    )

    # LFG
    lfg_default_expiry_minutes: int = 60
    lfg_suggestions: int = 3
    lfg_max_pings: int = 3
    lfg_ping_cooldown_minutes: int = 180

    log_level: str = "INFO"

    @field_validator("database_url")
    @classmethod
    def _asyncpg_scheme(cls, v: str) -> str:
        # Fly/Railway hand out postgres:// URLs; SQLAlchemy needs an explicit async driver.
        for prefix in ("postgres://", "postgresql://"):
            if v.startswith(prefix):
                return "postgresql+asyncpg://" + v[len(prefix) :]
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()
