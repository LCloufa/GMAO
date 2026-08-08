"""Couche de compatibilité PostgreSQL pour l'ancien code DB-API de la GMAO.

Cette couche permet de conserver temporairement les appels historiques de type
SQLite (`?`, `row_factory = dict`, `cursor.lastrowid`) tout en utilisant
PostgreSQL via psycopg 3.

Elle ne crée, ne supprime et ne migre aucune table. Les changements de schéma
restent du ressort exclusif de SQLAlchemy + Flask-Migrate/Alembic.
"""

from __future__ import annotations

import os
import re
from typing import Any, Iterable, Optional

import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row, tuple_row


load_dotenv()


# Defaults applicatifs définis dans models.py mais qui ne sont pas forcément
# des DEFAULT serveur PostgreSQL. Les anciens INSERT SQL bruts ne passent pas
# par l'ORM SQLAlchemy : on complète donc uniquement les colonnes absentes.
#
# Cette compatibilité est non destructive : aucune table n'est créée/altérée
# ici. Une future modification du schéma doit toujours passer par Alembic.
_RAW_INSERT_DEFAULTS: dict[str, tuple[tuple[str, str], ...]] = {
    "users": (("role", "'user'"),),
    "clients": (("rythme_horaire", "'1x8'"),),
    "techniciens": (("statut", "'Actif'"),),
    "equipements": (("statut", "'Opérationnel'"),),
    "interventions": (
        ("priority", "'medium'"),
        ("status", "'planned'"),
    ),
    "declarations_panne": (
        ("urgency", "'medium'"),
        ("status", "'pending'"),
        ("created_at", "CURRENT_TIMESTAMP"),
    ),
    "rapports_intervention": (("created_at", "CURRENT_TIMESTAMP"),),
}


_INSERT_VALUES_RE = re.compile(
    r"^(?P<prefix>\s*INSERT\s+INTO\s+(?P<table>\"?[A-Za-z_][A-Za-z0-9_]*\"?)\s*)"
    r"\((?P<columns>[^)]*)\)"
    r"(?P<middle>\s*VALUES\s*)"
    r"\((?P<values>[^)]*)\)"
    r"(?P<suffix>\s*;?\s*)$",
    flags=re.IGNORECASE | re.DOTALL,
)


def _normalize_database_url(url: str) -> str:
    """Convertit une URL SQLAlchemy en DSN accepté directement par psycopg."""
    url = (url or "").strip()

    if url.startswith("postgresql+psycopg://"):
        return "postgresql://" + url[len("postgresql+psycopg://") :]

    if url.startswith("postgres://"):
        return "postgresql://" + url[len("postgres://") :]

    return url


def _replace_qmark_placeholders(sql: str) -> str:
    """Remplace les paramètres SQLite `?` par `%s`, hors chaînes SQL.

    Le parseur reste volontairement léger mais évite de remplacer les points
    d'interrogation présents dans les littéraux simples/doubles.
    """
    out: list[str] = []
    in_single = False
    in_double = False
    i = 0

    while i < len(sql):
        ch = sql[i]

        if ch == "'" and not in_double:
            # Deux apostrophes consécutives représentent une apostrophe SQL.
            if in_single and i + 1 < len(sql) and sql[i + 1] == "'":
                out.append("''")
                i += 2
                continue
            in_single = not in_single
            out.append(ch)
        elif ch == '"' and not in_single:
            in_double = not in_double
            out.append(ch)
        elif ch == "?" and not in_single and not in_double:
            out.append("%s")
        else:
            out.append(ch)

        i += 1

    return "".join(out)


def _apply_raw_insert_compatibility(sql: str) -> str:
    """Complète les INSERT DB-API historiques pour les modèles PostgreSQL.

    SQLAlchemy applique `default=` quand on passe par l'ORM, mais ces defaults
    Python ne sont pas garantis lors d'un `cursor.execute("INSERT ...")` brut.
    Cette fonction ajoute donc les defaults applicatifs manquants aux INSERT
    simples `INSERT INTO table (colonnes) VALUES (valeurs)` utilisés par app.py.

    Elle corrige également l'ancien nom de colonne `localisation` de la route
    rapide des équipements vers le nom actuel `emplacement`.
    """
    match = _INSERT_VALUES_RE.match(sql)
    if not match:
        return sql

    raw_table = match.group("table")
    table = raw_table.strip('"').lower()

    columns = [part.strip() for part in match.group("columns").split(",") if part.strip()]
    values = [part.strip() for part in match.group("values").split(",") if part.strip()]

    # Ne réécrit pas une requête atypique : on préfère laisser PostgreSQL
    # remonter une erreur explicite plutôt que de modifier une requête ambiguë.
    if len(columns) != len(values):
        return sql

    if table == "equipements":
        columns = [
            "emplacement" if column.strip('"').lower() == "localisation" else column
            for column in columns
        ]

    present_columns = {column.strip('"').lower() for column in columns}
    for column, sql_default in _RAW_INSERT_DEFAULTS.get(table, ()): 
        if column.lower() not in present_columns:
            columns.append(column)
            values.append(sql_default)
            present_columns.add(column.lower())

    return (
        f"{match.group('prefix')}"
        f"({', '.join(columns)})"
        f"{match.group('middle')}"
        f"({', '.join(values)})"
        f"{match.group('suffix')}"
    )


def _translate_sql(sql: str) -> str:
    """Traduit les constructions SQLite encore utilisées par app.py."""
    translated = _apply_raw_insert_compatibility(sql)
    translated = _replace_qmark_placeholders(translated)

    # SQLite : datetime('now') / date('now')
    translated = re.sub(
        r"datetime\s*\(\s*['\"]now['\"]\s*\)",
        "CURRENT_TIMESTAMP",
        translated,
        flags=re.IGNORECASE,
    )
    translated = re.sub(
        r"date\s*\(\s*['\"]now['\"]\s*\)",
        "CURRENT_DATE",
        translated,
        flags=re.IGNORECASE,
    )

    return translated


class CompatCursor:
    """Petit proxy de curseur psycopg avec les habitudes SQLite du projet."""

    def __init__(self, cursor: psycopg.Cursor[Any], connection: "CompatConnection"):
        self._cursor = cursor
        self._connection = connection
        self._lastrowid: Optional[int] = None

    def execute(self, query: str, params: Optional[Iterable[Any]] = None) -> "CompatCursor":
        sql = _translate_sql(query)

        if params is None:
            self._cursor.execute(sql)
        else:
            self._cursor.execute(sql, params)

        self._lastrowid = None

        # SQLite expose cursor.lastrowid. Pour les INSERT sur tables avec
        # séquence/IDENTITY PostgreSQL, LASTVAL() donne la dernière valeur de
        # séquence de cette même connexion.
        if query.lstrip().upper().startswith("INSERT"):
            try:
                with self._connection._conn.cursor(row_factory=tuple_row) as id_cursor:
                    id_cursor.execute("SELECT LASTVAL()")
                    row = id_cursor.fetchone()
                    if row:
                        self._lastrowid = int(row[0])
            except (psycopg.Error, TypeError, ValueError):
                self._lastrowid = None

        return self

    def executemany(self, query: str, params_seq: Iterable[Iterable[Any]]) -> "CompatCursor":
        self._cursor.executemany(_translate_sql(query), params_seq)
        return self

    @property
    def lastrowid(self) -> Optional[int]:
        return self._lastrowid

    @property
    def rowcount(self) -> int:
        return self._cursor.rowcount

    @property
    def description(self):
        return self._cursor.description

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchmany(self, size: int = 0):
        if size:
            return self._cursor.fetchmany(size)
        return self._cursor.fetchmany()

    def fetchall(self):
        return self._cursor.fetchall()

    def close(self) -> None:
        self._cursor.close()

    def __iter__(self):
        return iter(self._cursor)

    def __enter__(self) -> "CompatCursor":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def __getattr__(self, name: str):
        return getattr(self._cursor, name)


class CompatConnection:
    """Proxy de connexion psycopg compatible avec l'ancien usage du projet."""

    def __init__(self, connection: psycopg.Connection[Any]):
        self._conn = connection
        self._row_factory = tuple_row

    @property
    def row_factory(self):
        return dict if self._row_factory is dict_row else self._row_factory

    @row_factory.setter
    def row_factory(self, value) -> None:
        # L'ancien app.py utilise `conn.row_factory = dict`.
        if value is dict or value is dict_row:
            self._row_factory = dict_row
        elif value is None or value is tuple or value is tuple_row:
            self._row_factory = tuple_row
        else:
            self._row_factory = value

    def cursor(self, *args, **kwargs) -> CompatCursor:
        kwargs.setdefault("row_factory", self._row_factory)
        return CompatCursor(self._conn.cursor(*args, **kwargs), self)

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "CompatConnection":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is None:
            self.commit()
        else:
            self.rollback()
        self.close()

    def __getattr__(self, name: str):
        return getattr(self._conn, name)


def get_db_connection() -> CompatConnection:
    """Ouvre une connexion PostgreSQL sans aucune création de schéma."""
    database_url = _normalize_database_url(os.getenv("DATABASE_URL", ""))

    if not database_url:
        raise RuntimeError("DATABASE_URL est absent du fichier .env")

    if not database_url.startswith(("postgresql://", "postgres://")):
        raise RuntimeError(
            "DATABASE_URL doit pointer vers PostgreSQL. "
            "Aucun fallback SQLite n'est autorisé."
        )

    connection = psycopg.connect(database_url, row_factory=tuple_row)
    return CompatConnection(connection)
