"""Storage backend abstraction: Postgres (Neon) when DATABASE_URL is set, else local
SQLite. One SQL dialect is written in db.py using '?' positional and ':name' named
placeholders plus a '{pk}' autoincrement token; this layer translates for each driver.

Why: cloud routines are stateless — the DB must live externally (Neon) so dedup and
tracking survive between daily runs. Locally, SQLite keeps zero-ops dev working.
"""
from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path

# :name  ->  %(name)s   — but NOT the ':' in a '::type' cast (negative lookbehind).
_NAMED = re.compile(r"(?<!:):(\w+)")

# Exceptions that mean "unique/constraint violation" across both drivers.
INTEGRITY_ERRORS: tuple = (sqlite3.IntegrityError,)
try:  # psycopg only present when the pg extra is installed
    import psycopg.errors as _pgerr
    INTEGRITY_ERRORS = INTEGRITY_ERRORS + (_pgerr.IntegrityError,)
except Exception:  # pragma: no cover
    pass


class Store:
    def __init__(self, url: str | None = None, sqlite_path: str = "data/jobhunter.db"):
        url = url if url is not None else os.environ.get("DATABASE_URL")
        if url:
            import psycopg
            from psycopg.rows import dict_row
            self.kind = "pg"
            self.conn = psycopg.connect(url, autocommit=True, row_factory=dict_row)
        else:
            self.kind = "sqlite"
            Path(sqlite_path).parent.mkdir(parents=True, exist_ok=True)
            self.conn = sqlite3.connect(sqlite_path)
            self.conn.row_factory = sqlite3.Row

    # -- SQL translation --------------------------------------------------
    def _sql(self, sql: str) -> str:
        if self.kind == "sqlite":
            return sql.replace("{pk}", "INTEGER PRIMARY KEY AUTOINCREMENT")
        sql = sql.replace("{pk}", "SERIAL PRIMARY KEY")
        sql = _NAMED.sub(r"%(\1)s", sql)   # :name -> %(name)s
        sql = sql.replace("?", "%s")        # positional
        return sql

    # -- operations -------------------------------------------------------
    def executescript(self, script: str):
        if self.kind == "sqlite":
            self.conn.executescript(script.replace("{pk}", "INTEGER PRIMARY KEY AUTOINCREMENT"))
            self.conn.commit()
            return
        cur = self.conn.cursor()
        for stmt in script.split(";"):
            s = stmt.strip()
            if s:
                cur.execute(self._sql(s))

    def execute(self, sql: str, params=()):
        cur = self.conn.cursor()
        cur.execute(self._sql(sql), params)
        if self.kind == "sqlite":
            self.conn.commit()
        return cur

    def query(self, sql: str, params=()):
        return self.execute(sql, params).fetchall()

    def queryone(self, sql: str, params=()):
        return self.execute(sql, params).fetchone()

    def copy_insert(self, table: str, columns: list, rows):
        """Bulk INSERT of conflict-free rows. Uses Postgres COPY (one stream, fast even
        through PgBouncer); falls back to executemany on SQLite."""
        rows = list(rows)
        if not rows:
            return
        if self.kind == "pg":
            cols = ", ".join(columns)
            with self.conn.cursor() as cur:
                with cur.copy(f"COPY {table} ({cols}) FROM STDIN") as cp:
                    for r in rows:
                        cp.write_row(r)
        else:
            ph = ", ".join("?" * len(columns))
            self.executemany(f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({ph})", rows)

    def executemany(self, sql: str, seq_of_params):
        """Bulk write in ONE transaction — critical for Neon throughput."""
        seq = list(seq_of_params)
        if not seq:
            return
        tsql = self._sql(sql)
        cur = self.conn.cursor()
        if self.kind == "pg":
            # one round trip, one transaction (autocommit is on; wrap explicitly)
            with self.conn.transaction():
                cur.executemany(tsql, seq)
        else:
            cur.executemany(tsql, seq)
            self.conn.commit()

    def insert(self, sql: str, params=()):
        """Run an INSERT and return the new integer id (tables use `id {pk}`)."""
        if self.kind == "pg":
            cur = self.conn.cursor()
            cur.execute(self._sql(sql + " RETURNING id"), params)
            row = cur.fetchone()
            return row["id"] if row else None
        cur = self.conn.cursor()
        cur.execute(self._sql(sql), params)
        self.conn.commit()
        return cur.lastrowid

    def close(self):
        self.conn.close()
