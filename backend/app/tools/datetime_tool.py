"""Current date/time tool. Uses zoneinfo for safety."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from langchain_core.tools import tool


@tool
def get_current_time(timezone: str = "UTC") -> str:
    """Return the current date and time in a given IANA timezone (default UTC).

    Args:
        timezone: IANA timezone name, e.g. 'UTC', 'Asia/Shanghai', 'America/New_York'.
    """
    try:
        tz = ZoneInfo(timezone)
    except ZoneInfoNotFoundError:
        return f"Unknown timezone: {timezone!r}. Use an IANA name like 'UTC' or 'Asia/Shanghai'."
    now = datetime.now(tz)
    return now.strftime("%Y-%m-%d %H:%M:%S %Z")


__all__ = ["get_current_time"]
