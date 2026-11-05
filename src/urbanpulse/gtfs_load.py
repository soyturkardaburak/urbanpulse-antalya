from __future__ import annotations

from pathlib import Path

import pandas as pd

REQUIRED_TABLES = (
    "agency.txt",
    "stops.txt",
    "routes.txt",
    "trips.txt",
    "stop_times.txt",
    "calendar.txt",
)

OPTIONAL_TABLES = ("shapes.txt",)


def read_gtfs_table(directory: Path, name: str) -> pd.DataFrame | None:
    path = directory / name
    if not path.exists():
        return None
    return pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")


def load_gtfs(directory: Path) -> dict[str, pd.DataFrame | None]:
    tables: dict[str, pd.DataFrame | None] = {}
    for name in (*REQUIRED_TABLES, *OPTIONAL_TABLES):
        tables[name] = read_gtfs_table(directory, name)
    return tables


def require_columns(frame: pd.DataFrame, columns: tuple[str, ...]) -> list[str]:
    return [column for column in columns if column not in frame.columns]
