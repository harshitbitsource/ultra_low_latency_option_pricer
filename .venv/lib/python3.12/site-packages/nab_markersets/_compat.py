"""Runtime fallbacks for supported Python interpreters."""

from __future__ import annotations

import sys
import typing
from typing import TYPE_CHECKING

__all__ = ["get_int_max_str_digits", "override"]

get_int_max_str_digits = getattr(sys, "get_int_max_str_digits", lambda: 0)

if TYPE_CHECKING:
    from typing_extensions import override
else:
    override = getattr(typing, "override", lambda method: method)
