from __future__ import annotations

import os

import psycopg

DEFAULT_DATABASE_URL = "postgresql://urbanpulse:urbanpulse@localhost:5432/urbanpulse"


def database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)


def connect():
    return psycopg.connect(database_url(), connect_timeout=5)


def apply_migration(connection, sql: str) -> None:
    statements = [part.strip() for part in sql.split(";") if part.strip()]
    with connection.cursor() as cursor:
        for statement in statements:
            cursor.execute(statement)
