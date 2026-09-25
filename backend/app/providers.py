"""Compatibility alias; use app.core.providers."""
import sys
from .core import providers as _module
sys.modules[__name__] = _module
