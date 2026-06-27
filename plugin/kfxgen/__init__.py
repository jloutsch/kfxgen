"""
kfxgen Core Library

Single source of truth for the package version. The Calibre plugin
wrapper at ``plugin/__init__.py`` reads ``version`` from here so the
two never drift apart. See CHANGELOG.md for release history.
"""

__author__ = "Justin Loutsch <justin.loutsch@gmail.com>"
__license__ = "GPL v3"
__copyright__ = "2025-2026, Justin Loutsch <justin.loutsch@gmail.com>"

#: Version tuple. Bump this for every release; the plugin wrapper and
#: converter log message read from here.
version = (5, 3, 16)
__version__ = ".".join(str(x) for x in version)

__all__ = [
    "NativeKFXGenerator",
]


def __getattr__(name):
    """Lazy import to avoid module loading issues."""
    if name == "NativeKFXGenerator":
        from .native_generator import NativeKFXGenerator

        return NativeKFXGenerator
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
