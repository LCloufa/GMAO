from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
PDF_PATH = ROOT / "intervention_report_pdf.py"
BACKUP_PATH = ROOT / "intervention_report_pdf_before_signature_compact.py"

OLD = """        ], styles, widths=(45 * mm, 127 * mm)),\n        PageBreak(),\n    ])\n\n    signature_name"""
NEW = """        ], styles, widths=(45 * mm, 127 * mm)),\n    ])\n\n    signature_name"""


def main() -> int:
    if not PDF_PATH.exists():
        print(f"ERREUR : {PDF_PATH.name} introuvable")
        return 1

    text = PDF_PATH.read_text(encoding="utf-8")
    if OLD not in text:
        if NEW in text:
            print("La signature est déjà placée à la suite de la feuille de temps.")
            return 0
        print("ERREUR : bloc de signature attendu introuvable.")
        return 1

    if not BACKUP_PATH.exists():
        shutil.copy2(PDF_PATH, BACKUP_PATH)
        print(f"Sauvegarde créée : {BACKUP_PATH.name}")

    PDF_PATH.write_text(text.replace(OLD, NEW, 1), encoding="utf-8")
    print("Signature remontée à la page précédente lorsque l'espace disponible le permet.")
    print("Le saut de page forcé avant la signature a été supprimé.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
