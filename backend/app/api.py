"""Compatibility alias for the former API module; use app.api_server.main."""

import sys

from .api_server import main as _module

sys.modules[__name__] = _module
