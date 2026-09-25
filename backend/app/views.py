"""Compatibility alias; use app.core.views."""
import sys
from .core import views as _module
sys.modules[__name__] = _module
