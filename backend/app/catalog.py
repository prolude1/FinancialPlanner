"""Compatibility alias; use app.core.catalog."""
import sys
from .core import catalog as _module
sys.modules[__name__] = _module
