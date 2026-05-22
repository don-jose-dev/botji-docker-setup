"""botji-core Hermes hooks. See ``stale_id_block`` + ``delivery_check``."""
from __future__ import annotations

from .delivery_check import transform_llm_output
from .stale_id_block import pre_tool_call

__all__ = ["pre_tool_call", "transform_llm_output"]
