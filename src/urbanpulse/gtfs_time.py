from __future__ import annotations


def gtfs_time_to_seconds(value: str | None) -> int | None:
    """Parse a GTFS clock time.

    Hours may exceed 23. Minutes and seconds must be in 0..59.
    Returns None for empty or malformed values.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    parts = text.split(":")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        return None
    hours, minutes, seconds = (int(part) for part in parts)
    if hours < 0 or minutes > 59 or seconds > 59:
        return None
    return hours * 3600 + minutes * 60 + seconds
