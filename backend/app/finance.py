"""Compatibility alias; use app.core.finance."""
import sys
from .core import finance as _module
sys.modules[__name__] = _module
