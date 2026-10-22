# deadbot

A Discord bot that helps Deadlock players find teammates. Link a Steam account and the bot
shows your playstyle and the people you play best with. It also runs LFG posts and suggests
which server members would fit your party best, with a reason for every suggestion.

Data comes from [deadlock-api.com](https://deadlock-api.com), a community API that is not
affiliated with Valve.

## Status

| Phase | Scope | State |
|---|---|---|
| 1 | Scaffold, DB, API client, `/link`, `/unlink`, `/profile`, `/teammates`, sync job | **done** |
| 2 | Steam link verification, `/card`, `/settings`, `/setup`, `/lfg`, `/fill`, `/synergy`, matching | **done** |
| 3 | Dump ingestion, hero role clustering, LightGBM and two-tower synergy models, eval | planned |
| 4 | Fly.io deploy, structured logging, usage metrics | planned |

## Commands

| Command | Visibility | What it does |
|---|---|---|
| `/link <account>` | ephemeral | Accepts a Steam profile URL, custom URL name, SteamID64, `[U:1:N]` or `STEAM_0:X:Y`. Checks that the account has Deadlock matches, links it (unverified), imports history in the background, and gives you a verification code with a **Verify** button. |
| `/verify` | ephemeral | Checks your Steam profile for the code and marks the link verified. |
| `/unlink` | ephemeral | Removes your link. |
| `/profile [user] [as_image]` | public | Rank, record, top heroes, hero-type mix, and typical play hours (shown in each viewer's local time). |
| `/card [user]` | public | PNG card: profile, top heroes, hero-type mix, top teammates with synergy. |
| `/teammates [user] [sort] [min_games]` | public | Teammates with ≥N games together: win rate together vs. your baseline, synergy, premade count, Steam friends, and best hero duo. Linked members of the server are highlighted. |
| `/lfg <size> [mode] [note] [expires_in]` | public post | Posts an LFG call with Join / Leave / Close buttons and the best-fitting members to invite. Pings up to 3 of them (respecting opt-outs and a 3-hour per-user cooldown) plus the server's ping role. Updates live; closes when full or on expiry. |
| `/fill [count] [mode] [share]` | ephemeral by default | Recommends who to add to your voice channel (or your active LFG party), each with a one-line reason. |
| `/synergy <player1> [..player5] [include_me]` | public | Party fit score for a group, with pairwise synergy, hero-type balance, schedule overlap and rank spread. |
| `/settings view·suggestions·pings·mode·region·availability` | ephemeral | Opt in/out of suggestions and pings; set preferred queue, region, and play hours. |
| `/setup [lfg_channel] [ping_role] [require_verified] …` | ephemeral, Manage Server | LFG channel, ping role, and whether only verified links are suggested (default: yes). |

Errors are always ephemeral.

## Verification
Linking is instant but unverified, because anyone can type anyone's Steam URL. To verify, put
the one-time code (e.g. `DL-7KQ2MX`, valid 30 minutes) in your Steam **profile name** or
**About me**, then press **Verify** or run `/verify`. The display name works even on private
profiles. Verifying moves an account away from whoever linked it first, so impersonators
can't squat on it. Servers suggest only verified members by default (`/setup
require_verified:false` to relax).

## Matching
A candidate's fit for a party is a weighted mean of four components, each 0–1 with 0.5
neutral (weights in config):

| Component | Weight | Signal |
|---|---|---|
| Synergy | 0.35 | Mean synergy with each party member (unknown pairs count as 0); ±10 pp maps to 0/1 |
| Role coverage | 0.25 | How much of the party's hero-type gap (ideal: an even split) the candidate's mix fills |
| Availability | 0.25 | Play-hour overlap with the party (60%) + online now via presence, else "usually on at this hour" (40%) |
| Rank | 0.15 | 1 − subrank gap / 12 (two tiers) |

Multi-slot suggestions are greedy: pick the best, add them to the party, re-score the rest.
Candidates are server members who linked, are opted in, verified (if required), and whose
preferred mode and region don't conflict. Each pick comes with a reason, e.g.
`covers Brawler · +8% synergy · usually on at this hour · similar rank`.

## How the data works

### Findings that shaped the design
- **There are no party IDs.** `match_player.party` is always 0 in both the live data and the
  dumps. Premades are inferred per match from two signals, stored as a bitmask in
  `match_party_links.sources`:
  1. `friends`: the API's `mate-stats?same_party=true` (same team and Steam friends at match start).
  2. `session`: teammates in back-to-back matches less than 90 minutes apart.

  The overall same-team ratio is *not* used. At high rank, friends often play against each
  other, and in one sample the ratio barely separated friends from strangers (median 0.51 vs 0.48).
- **There is no MMR.** All MMR endpoints are deprecated. Rank is the badge
  (`tier*10 + subrank`) from `/v1/players/{id}/rank`.
- **Roles are Valve's official `hero_type`** (brawler, marksman, mystic, assassin) for now.
  Phase 3 adds roles clustered from item builds.
- `won = match_result == player_team`. `player_match_outcome` is 0 for older matches.

### Synergy (Phase 1)
For a pair on the same team:

```
p_a, p_b  = each player's win rate excluding their shared games
expected  = sigmoid(logit p_a + logit p_b)        # individual edges add up
shrunk    = (wins_together + k·expected) / (games_together + k)    # k = 20 by default
synergy   = shrunk − expected
```

### Storage
Postgres only stores data about linked users. On average that's roughly 0.5 MB per user:
their own match rows plus per-match detail for their top 25 teammates. Redis is a bounded
LRU cache (256 MB in compose) for API responses and computed reports. Report cache keys
include `last_synced_at`, so they invalidate themselves after each sync. The 539 GB
`match_player` dump is never copied into Postgres. Phase 3 reads only the columns it needs
from the public Parquet lake with DuckDB and writes the model outputs back.

### Schema
`players`, `users`, `matches`, `match_players`, `match_party_links`, `pair_stats`,
`link_verifications`, `guild_config`, `lfg_posts`, `lfg_members`. See
[models.py](src/deadbot/data/db/models.py) and [migrations/](migrations/).

## Setup

### 1. Create the Discord application
1. Go to <https://discord.com/developers/applications>, click **New Application**, open
   **Bot**, and copy the token.
2. Under **Bot → Privileged Gateway Intents**, enable **Server Members Intent**. Enable
   **Presence Intent** only if you set `ENABLE_PRESENCE_INTENT=true` ("online now" in
   suggestions; without it the bot uses each player's usual hours).
3. Invite the bot with **OAuth2 → URL Generator**:
   - Scopes: `bot`, `applications.commands`
   - Bot permissions: View Channels, Send Messages, Embed Links, Attach Files, Read Message
     History, Use External Emojis. Add Mention Everyone only if your `/lfg` ping role isn't
     mentionable.

   Or fill in this URL (permissions = 379904):
   `https://discord.com/oauth2/authorize?client_id=<APP_ID>&scope=bot+applications.commands&permissions=379904`

Gateway intents used: `guilds`, `members` (privileged), `voice_states`, and optionally
`presences` (privileged). Message content is not needed.

### 2. Configure
```sh
cp .env.example .env   # set DISCORD_TOKEN; DEV_GUILD_ID makes slash commands appear instantly
```
`DEADLOCK_API_KEY` is optional and gives a bigger share of the API's rate limits.
`STEAM_API_KEY` is optional too. Without it, custom URLs are resolved through the public
profile XML.

### 3. Run with Docker
```sh
docker compose up --build
```
The bot container runs `alembic upgrade head` before starting.

### Run locally (for development)
Python 3.12 via [uv](https://docs.astral.sh/uv/) (it ships self-contained builds):
```sh
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -e '.[dev]'
docker compose up -d postgres redis
.venv/bin/alembic upgrade head
.venv/bin/python -m deadbot
```

### Tests and lint
```sh
.venv/bin/pytest            # DB integration tests run when local Postgres is up, else skip
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```

## Layout
```
src/deadbot/
  bot/        Discord client, embeds, cogs (one per command group)
  services/   business logic: linking + verification, sync, synergy, premade inference,
              profile, teammates, matching (pure scoring), features, recommend, lfg, card
  data/       Postgres models/repo, Deadlock API client, Steam ID resolution, Redis cache
  jobs/       APScheduler periodic sync
  ml/         Phase 3
```

## Known limitations
- Unverified links can still use `/profile` and `/teammates`. That data is public on
  deadlock-api.com anyway.
- Card names in scripts DejaVu doesn't cover (e.g. CJK) render as boxes.
- Per-match hero detail, which powers "best duo" and session-premade detection, only covers
  each user's top 25 teammates (`SYNC_DETAIL_MATES`). Game counts and synergy cover every teammate.
- "Busiest days" are computed in UTC.
