"""Compatibility alias; use app.core.worker."""
import sys
from .core import worker as _module
sys.modules[__name__] = _module
