import sys

from deadbot.bot.client import DeadBot
from deadbot.config import get_settings
from deadbot.logging import configure_logging


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    token = settings.discord_token.get_secret_value()
    if not token:
        sys.exit("DISCORD_TOKEN is not set (see .env.example)")
    DeadBot(settings).run(token, log_handler=None)


if __name__ == "__main__":
    main()
