"""Compatibility re-export of the shared vision runtime.

The implementation moved to :mod:`src.utils.vision_runtime` so that the public
utility layer owns it.  ``map_trade`` keeps importing ``Vision`` and the shared
text helpers from this module path; the direction ``tasks -> utils`` stays
allowed and the domain package is no longer imported by the shared layer.
"""

from __future__ import annotations

from src.utils.vision_runtime import (
    Vision,
    normalize_text,
    parse_used_limit,
)

__all__ = [
    "Vision",
    "normalize_text",
    "parse_used_limit",
]
