from pathlib import Path
import shutil
import sys


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"
BACKUP_PATH = APP_PATH.with_name("app_before_stock_supplier_fields.py")
BEGIN = "# BEGIN STOCK_SUPPLIER_FIELDS"
END = "# END STOCK_SUPPLIER_FIELDS"


def main() -> int:
    if not APP_PATH.exists():
        print(f"ERREUR : {APP_PATH} introuvable")
        return 1

    text = APP_PATH.read_text(encoding="utf-8")

    if BEGIN in text and END in text:
        print("Les champs fournisseur complets sont déjà installés dans app.py.")
        return 0

    if "# BEGIN STOCK_MODULE" not in text:
        print("ERREUR : le module stock doit être installé avant ce patch.")
        return 1

    old_query = (
        '"SELECT id, nom, contact, email, telephone, site_web, actif, notes '
        'FROM stock_suppliers ORDER BY nom ASC"'
    )
    new_query = (
        '"SELECT id, nom, adresse, siret, contact_nom, contact_prenom, telephone, '
        'email, site_web, notes, actif FROM stock_suppliers ORDER BY nom ASC"'
    )
    if old_query not in text:
        print("ERREUR : requête fournisseur attendue introuvable dans app.py.")
        return 1

    old_insert = '''        INSERT INTO stock_suppliers
        (nom, contact, email, telephone, site_web, actif, notes)
        VALUES (?, ?, ?, ?, ?, TRUE, ?)
        """,
        (
            nom,
            request.form.get("contact", "").strip() or None,
            request.form.get("email", "").strip() or None,
            request.form.get("telephone", "").strip() or None,
            request.form.get("site_web", "").strip() or None,
            request.form.get("notes", "").strip() or None,
        ),'''

    new_insert = '''        INSERT INTO stock_suppliers
        (nom, adresse, siret, contact_nom, contact_prenom,
         telephone, email, site_web, actif, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, TRUE, ?)
        """,
        (
            nom,
            request.form.get("adresse", "").strip() or None,
            request.form.get("siret", "").strip() or None,
            request.form.get("contact_nom", "").strip() or None,
            request.form.get("contact_prenom", "").strip() or None,
            request.form.get("telephone", "").strip() or None,
            request.form.get("email", "").strip() or None,
            request.form.get("site_web", "").strip() or None,
            request.form.get("notes", "").strip() or None,
        ),'''

    if old_insert not in text:
        print("ERREUR : INSERT fournisseur attendu introuvable dans app.py.")
        return 1

    if not BACKUP_PATH.exists():
        shutil.copy2(APP_PATH, BACKUP_PATH)
        print(f"Sauvegarde créée : {BACKUP_PATH.name}")

    text = text.replace(old_query, new_query, 1)
    text = text.replace(old_insert, new_insert, 1)

    marker = f"\n{BEGIN}\n# Champs fournisseur : adresse, SIRET, nom/prénom contact.\n{END}\n"
    stock_end = text.find("# END STOCK_MODULE")
    if stock_end != -1:
        stock_end += len("# END STOCK_MODULE")
        text = text[:stock_end] + marker + text[stock_end:]
    else:
        text += marker

    APP_PATH.write_text(text, encoding="utf-8")
    print("Champs fournisseur complets ajoutés à app.py.")
    print("Aucune donnée existante n'est supprimée par ce patch.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
