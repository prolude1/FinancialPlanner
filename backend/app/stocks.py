"""Compatibility alias; use app.core.stocks."""
import sys
from .core import stocks as _module
sys.modules[__name__] = _module
