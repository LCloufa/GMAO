"""Compatibilité SQLite -> PostgreSQL pour l'ancien ``app.py`` de la GMAO.

Le code historique importe ``sqlite3`` et appelle ``sqlite3.connect('database.db')``
à de nombreux endroits. Sur Render, ce module local masque volontairement le
module sqlite3 de la bibliothèque standard et redirige ces connexions vers la
base PostgreSQL définie par ``DATABASE_URL``.

Le schéma est créé de façon idempotente à partir de ``models.py`` lors de la
première connexion d'un processus. Les requêtes métier restent ensuite prises
en charge par ``database_compat.py`` (placeholders ``?``, ``Row``,
``lastrowid``, fonctions date/heure SQLite, etc.).
"""

from __future__ import annotations

import os
import threading
from typing import Any, Iterable, Optional

import psycopg


# L'ancien app.py utilise ``conn.row_factory = sqlite3.Row``. La couche
# database_compat interprète ``dict`` comme une demande de lignes dictionnaire.
Row = dict

# Exceptions DB-API fréquemment exposées par sqlite3. Cela garde une surface de
# compatibilité correcte si une route historique les intercepte explicitement.
Error = psycopg.Error
DatabaseError = psycopg.DatabaseError
IntegrityError = psycopg.IntegrityError
OperationalError = psycopg.OperationalError
ProgrammingError = psycopg.ProgrammingError

_schema_lock = threading.Lock()
_schema_ready = False


def _sqlalchemy_database_url() -> str:
    """Retourne DATABASE_URL dans un format SQLAlchemy + psycopg 3."""
    url = (os.getenv("DATABASE_URL") or "").strip()
    if not url:
        raise RuntimeError(
            "DATABASE_URL est absente. Ajoute l'Internal Database URL de "
            "PostgreSQL dans les variables d'environnement Render."
        )

    if url.startswith("postgresql+psycopg://"):
        return url
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://") :]
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://") :]

    raise RuntimeError(
        "DATABASE_URL doit pointer vers PostgreSQL (postgresql://...)."
    )


def _ensure_schema() -> None:
    """Crée les tables absentes à partir des modèles SQLAlchemy.

    La base Render est neuve lors du premier déploiement. ``create_all`` est
    idempotent : il crée uniquement les tables manquantes et ne supprime pas les
    données existantes. Les évolutions futures du schéma pourront ensuite être
    gérées avec Flask-Migrate/Alembic.
    """
    global _schema_ready

    if _schema_ready:
        return

    with _schema_lock:
        if _schema_ready:
            return

        # Imports différés : ce fichier masque le module stdlib ``sqlite3``.
        # On laisse donc son initialisation se terminer avant de charger
        # SQLAlchemy/Flask-SQLAlchemy, afin d'éviter toute boucle d'import.
        from sqlalchemy import create_engine
        from models import db

        engine = create_engine(_sqlalchemy_database_url(), pool_pre_ping=True)
        try:
            db.metadata.create_all(bind=engine)
        finally:
            engine.dispose()

        _schema_ready = True


def _pragma_table_name(sql: str) -> Optional[str]:
    """Extrait le nom de table d'un ancien ``PRAGMA table_info(...)``."""
    compact = " ".join(sql.strip().split())
    prefix = "PRAGMA table_info("
    if not compact.lower().startswith(prefix.lower()) or not compact.endswith(")"):
        return None

    table = compact[len(prefix) : -1].strip().strip('"').strip("'")
    if not table or not table.replace("_", "").isalnum():
        return None
    return table


class Cursor:
    """Proxy qui neutralise uniquement le bootstrap SQLite historique."""

    def __init__(self, cursor: Any):
        self._cursor = cursor

    def execute(self, query: str, params: Optional[Iterable[Any]] = None) -> "Cursor":
        normalized = " ".join(query.strip().split()).upper()

        # ``app.init_db()`` contient encore les CREATE TABLE SQLite avec
        # AUTOINCREMENT. Le vrai schéma PostgreSQL vient déjà de models.py.
        if normalized.startswith("CREATE TABLE IF NOT EXISTS "):
            return self

        # Support générique de PRAGMA table_info utilisé par l'ancien bootstrap.
        table_name = _pragma_table_name(query)
        if table_name:
            self._cursor.execute(
                """
                SELECT ordinal_position, column_name
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = %s
                ORDER BY ordinal_position
                """,
                (table_name,),
            )
            return self

        if params is None:
            self._cursor.execute(query)
        else:
            self._cursor.execute(query, params)
        return self

    def executemany(self, query: str, params_seq: Iterable[Iterable[Any]]) -> "Cursor":
        self._cursor.executemany(query, params_seq)
        return self

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchmany(self, size: int = 0):
        return self._cursor.fetchmany(size) if size else self._cursor.fetchmany()

    def fetchall(self):
        return self._cursor.fetchall()

    @property
    def lastrowid(self):
        return self._cursor.lastrowid

    @property
    def rowcount(self):
        return self._cursor.rowcount

    @property
    def description(self):
        return self._cursor.description

    def close(self) -> None:
        self._cursor.close()

    def __iter__(self):
        return iter(self._cursor)

    def __enter__(self) -> "Cursor":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def __getattr__(self, name: str):
        return getattr(self._cursor, name)


class Connection:
    """Proxy de connexion exposant l'interface utilisée par app.py."""

    def __init__(self, connection: Any):
        self._connection = connection

    @property
    def row_factory(self):
        return self._connection.row_factory

    @row_factory.setter
    def row_factory(self, value) -> None:
        self._connection.row_factory = value

    def cursor(self, *args, **kwargs) -> Cursor:
        return Cursor(self._connection.cursor(*args, **kwargs))

    def commit(self) -> None:
        self._connection.commit()

    def rollback(self) -> None:
        self._connection.rollback()

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> "Connection":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is None:
            self.commit()
        else:
            self.rollback()
        self.close()

    def __getattr__(self, name: str):
        return getattr(self._connection, name)


def connect(*_args, **_kwargs) -> Connection:
    """Remplace ``sqlite3.connect`` par une connexion PostgreSQL Render."""
    _ensure_schema()
    from database_compat import get_db_connection

    return Connection(get_db_connection())
