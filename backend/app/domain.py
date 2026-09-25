"""Compatibility alias; use app.core.domain."""
import sys
from .core import domain as _module
sys.modules[__name__] = _module
