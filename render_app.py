"""Point d'entrée Render de la GMAO avec PostgreSQL.

Utiliser sur Render :
    python render_app.py

Le fichier ``app.py`` historique reste intact. Après son import, sa référence
globale ``sqlite3`` est remplacée par ``render_db``, ce qui fait utiliser
PostgreSQL à toutes les routes existantes sans réécrire l'application.
"""

import os

import app as legacy_app
import render_db


# Les fonctions de route de app.py résolvent la variable globale ``sqlite3`` au
# moment de leur exécution. On remplace donc uniquement cette variable dans le
# module de la GMAO ; le vrai module sqlite3 Python reste intact pour les autres
# bibliothèques.
legacy_app.sqlite3 = render_db

# Prépare les dossiers d'upload et crée, si nécessaire, les tables PostgreSQL
# décrites dans models.py. Ces opérations sont idempotentes.
legacy_app.ensure_upload_dirs()
legacy_app.init_db()

# Alias utilisable aussi par un serveur WSGI si besoin.
application = legacy_app.app
app = application


if __name__ == "__main__":
    application.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 10000)),
    )
