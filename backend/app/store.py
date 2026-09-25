"""Compatibility alias; use app.core.store."""
import sys
from .core import store as _module
sys.modules[__name__] = _module
