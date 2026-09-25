"""Compatibility alias; use app.core.schemas."""
import sys
from .core import schemas as _module
sys.modules[__name__] = _module
