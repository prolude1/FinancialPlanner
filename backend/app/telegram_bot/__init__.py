"""Private Telegram bot package.

Handlers are independent from the polling transport so they can be tested
without Telegram or the API server running.
"""

from .handlers import authorized, handle, telegram_commands

__all__ = ["authorized", "handle", "telegram_commands"]
