"""Compatibility alias for the former bot module.

New code should import handlers from :mod:`app.telegram_bot.handlers` and run
the polling process with ``python -m app.telegram_bot``.
"""

from .telegram_bot.client import BackendClient, main
from .telegram_bot.handlers import *  # noqa: F401,F403


if __name__ == "__main__":
    main()
