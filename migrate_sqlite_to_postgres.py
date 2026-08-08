import os
import sqlite3
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask
from sqlalchemy import inspect, text

from models import db


TABLES = [
    "users",
    "clients",
    "techniciens",
    "equipements",
    "equipement_documents",
    "interventions",
    "declarations_panne",
    "declaration_photos",
    "rapports_intervention",
]


def create_app():
    load_dotenv()

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError(
            "DATABASE_URL est absent. Crée un fichier .env à partir de .env.example."
        )

    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = database_url
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    db.init_app(app)
    return app


def source_tables(conn):
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {row[0] for row in rows}


def target_has_data():
    inspector = inspect(db.engine)
    existing = set(inspector.get_table_names())

    for table in TABLES:
        if table not in existing:
            continue
        count = db.session.execute(text(f'SELECT COUNT(*) FROM "{table}"')).scalar_one()
        if count:
            return True, table, count

    return False, None, 0


def migrate(sqlite_path: Path):
    if not sqlite_path.exists():
        raise FileNotFoundError(f"Base SQLite introuvable : {sqlite_path}")

    sqlite_conn = sqlite3.connect(sqlite_path)
    sqlite_conn.row_factory = sqlite3.Row

    available_source_tables = source_tables(sqlite_conn)

    print("Création/vérification du schéma PostgreSQL...")
    db.create_all()

    has_data, table, count = target_has_data()
    if has_data:
        raise RuntimeError(
            f"Migration arrêtée : PostgreSQL contient déjà {count} ligne(s) dans {table}. "
            "Aucune donnée n'a été écrasée."
        )

    try:
        for table in TABLES:
            if table not in available_source_tables:
                print(f"- {table}: absente de SQLite, ignorée")
                continue

            rows = sqlite_conn.execute(f'SELECT * FROM "{table}"').fetchall()
            if not rows:
                print(f"- {table}: 0 ligne")
                continue

            columns = rows[0].keys()
            quoted_columns = ", ".join(f'"{column}"' for column in columns)
            placeholders = ", ".join(f":{column}" for column in columns)
            statement = text(
                f'INSERT INTO "{table}" ({quoted_columns}) VALUES ({placeholders})'
            )

            db.session.execute(statement, [dict(row) for row in rows])
            print(f"- {table}: {len(rows)} ligne(s) copiée(s)")

        db.session.commit()

        # Recaler les séquences PostgreSQL après insertion d'IDs provenant de SQLite.
        for table in TABLES:
            if table not in inspect(db.engine).get_table_names():
                continue
            db.session.execute(
                text(
                    f"""
                    SELECT setval(
                        pg_get_serial_sequence('{table}', 'id'),
                        COALESCE((SELECT MAX(id) FROM \"{table}\"), 1),
                        (SELECT MAX(id) IS NOT NULL FROM \"{table}\")
                    )
                    """
                )
            )

        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    finally:
        sqlite_conn.close()

    print("Migration terminée avec succès.")


if __name__ == "__main__":
    app = create_app()
    with app.app_context():
        migrate(Path(os.getenv("SQLITE_PATH", "database.db")))
