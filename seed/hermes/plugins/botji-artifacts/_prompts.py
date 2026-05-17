"""Prompt loader — reads AI instruction templates from prompts/*.md files."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_PROMPTS_DIR = Path(__file__).parent / "prompts"


@lru_cache(maxsize=16)
def load_prompt(name: str) -> str:
    """Return the contents of prompts/<name>.md, stripped of trailing whitespace.

    The file may contain {{PLACEHOLDER}} tokens for caller-side substitution.
    Raises FileNotFoundError if the prompt file is missing.
    """
    path = _PROMPTS_DIR / f"{name}.md"
    return path.read_text(encoding="utf-8").rstrip()
