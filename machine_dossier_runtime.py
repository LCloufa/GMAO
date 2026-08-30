"""Petits garde-fous runtime du Dossier Machine Numérique.

La couche database_compat peut retourner soit des tuples soit des dictionnaires
selon le row_factory actif. La détection Alembic doit accepter les deux formes.
"""

import machine_dossier


def patch_machine_dossier_runtime():
    def schema_ready(conn):
        cursor = conn.cursor()
        for table in machine_dossier.DOSSIER_TABLES:
            cursor.execute("SELECT to_regclass(?) AS relation", (f"public.{table}",))
            row = cursor.fetchone()
            if not row:
                return False
            if isinstance(row, dict):
                value = row.get("relation")
            else:
                value = row[0]
            if not value:
                return False
        return True

    machine_dossier._schema_ready = schema_ready
