"""Shared CLI utility functions."""
from __future__ import annotations


def fmt_ms(millis: int | None) -> str:
    """Format a millisecond timestamp as HH:MM:SS, or '??:??:??' if None."""
    if millis is None:
        return "  ??:??:??"
    hours, remainder = divmod(millis // 1000, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
