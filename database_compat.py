import os
import re

import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row, tuple_row


load_dotenv()


def _database_url() -> str:
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError("DATABASE_URL est absent du fichier .env")

    # SQLAlchemy utilise postgresql+psycopg:// alors que psycopg attend postgresql://
    if url.startswith("postgresql+psycopg://"):
        url = "postgresql://" + url[len("postgresql+psycopg://"):]
    return url


def _translate_sql(sql: str) -> str:
    """Traduit le petit sous-ensemble SQLite encore utilisé par l'application."""
    sql = sql.replace("datetime('now')", "CURRENT_TIMESTAMP")
    sql = sql.replace("date('now')", "CURRENT_DATE")
    # Les requêtes historiques utilisent les paramètres SQLite '?'.
    sql = re.sub(r"\?", "%s", sql)
    return sql


class CompatCursor:
    def __init__(self, cursor):
        self._cursor = cursor
        self.lastrowid = None

    def execute(self, sql, params=None):
        sql = _translate_sql(sql)
        params = () if params is None else params

        # SQLite fournit cursor.lastrowid. Pour conserver le comportement actuel,
        # on ajoute RETURNING id aux INSERT simples et on mémorise l'identifiant.
        is_insert = bool(re.match(r"^\s*INSERT\s+INTO\s+", sql, flags=re.IGNORECASE))
        has_returning = bool(re.search(r"\bRETURNING\b", sql, flags=re.IGNORECASE))

        if is_insert and not has_returning:
            sql = sql.rstrip().rstrip(";") + " RETURNING id"
            self._cursor.execute(sql, params)
            row = self._cursor.fetchone()
            self.lastrowid = row[0] if row else None
        else:
            self._cursor.execute(sql, params)

        return self

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    @property
    def rowcount(self):
        return self._cursor.rowcount

    @property
    def description(self):
        return self._cursor.description

    def close(self):
        self._cursor.close()


class CompatConnection:
    def __init__(self):
        self._conn = psycopg.connect(_database_url())
        self.row_factory = None

    def cursor(self):
        factory = dict_row if self.row_factory else tuple_row
        return CompatCursor(self._conn.cursor(row_factory=factory))

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()


def get_db_connection():
    return CompatConnection()
