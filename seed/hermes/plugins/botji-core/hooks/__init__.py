"""botji-core Hermes hooks. See ``stale_id_block`` for the rules."""
from __future__ import annotations

from .stale_id_block import pre_tool_call

__all__ = ["pre_tool_call"]
