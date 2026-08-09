from pathlib import Path
import shutil
import sys


BASE_PATH = Path(__file__).resolve().parents[1] / "templates" / "base.html"
BACKUP_PATH = BASE_PATH.with_name("base_before_global_report_button_guard.html")
BEGIN = "// BEGIN GLOBAL_REPORT_BUTTON_GUARD"
END = "// END GLOBAL_REPORT_BUTTON_GUARD"


OLD_BLOCK = '''            const technicianCode = data.technicien_code || "-";\n\n            document.getElementById("drawerBody").innerHTML = `\n                <p><strong>Date :</strong> ${data.scheduled_date} ${data.scheduled_time || ""}</p>\n                <p><strong>Durée :</strong> ${durationText}</p>\n                <p><strong>Technicien :</strong> ${technicianCode}</p>\n                <p><strong>Équipement :</strong> ${data.equipement_nom}</p>\n                <p><strong>Description :</strong> ${data.description || "-"}</p>\n\n                ${canWriteReport ? `\n                <div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:16px;">\n                    <a class="stock-btn-light" href="/stock/intervention/${data.id}">📦 Gérer les pièces</a>\n                    <button class="btn-success"\n                        onclick="openReportForm(${data.id}, '${data.scheduled_time || ''}')">\n                        Rédiger le rapport\n                    </button>\n                </div>` : ''}\n            `;'''


NEW_BLOCK = '''            const technicianCode = data.technicien_code || "-";\n\n            // BEGIN GLOBAL_REPORT_BUTTON_GUARD\n            const interventionStatus = String(data.status || "").toLowerCase();\n            const reportAlreadySubmitted = interventionStatus === "completed";\n\n            let interventionActions = "";\n            if (canWriteReport) {\n                if (reportAlreadySubmitted) {\n                    interventionActions = `\n                        <div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:16px;align-items:center;">\n                            <a class="stock-btn-light" href="/stock/intervention/${data.id}">📦 Gérer les pièces</a>\n                            <span class="badge status-completed">✓ Rapport déjà soumis</span>\n                            <a class="btn-secondary" href="/rapports" style="text-decoration:none;">Voir les rapports</a>\n                        </div>`;\n                } else {\n                    interventionActions = `\n                        <div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:16px;">\n                            <a class="stock-btn-light" href="/stock/intervention/${data.id}">📦 Gérer les pièces</a>\n                            <button class="btn-success"\n                                onclick="openReportForm(${data.id}, '${data.scheduled_time || ''}')">\n                                Rédiger le rapport\n                            </button>\n                        </div>`;\n                }\n            }\n            // END GLOBAL_REPORT_BUTTON_GUARD\n\n            document.getElementById("drawerBody").innerHTML = `\n                <p><strong>Date :</strong> ${data.scheduled_date} ${data.scheduled_time || ""}</p>\n                <p><strong>Durée :</strong> ${durationText}</p>\n                <p><strong>Technicien :</strong> ${technicianCode}</p>\n                <p><strong>Équipement :</strong> ${data.equipement_nom}</p>\n                <p><strong>Description :</strong> ${data.description || "-"}</p>\n                ${interventionActions}\n            `;'''


def main() -> int:
    if not BASE_PATH.exists():
        print(f"ERREUR : {BASE_PATH} introuvable")
        return 1

    text = BASE_PATH.read_text(encoding="utf-8")

    if BEGIN in text and END in text:
        print("La protection globale du bouton rapport est déjà installée.")
        return 0

    if OLD_BLOCK not in text:
        print("ERREUR : bloc openIntervention attendu introuvable dans templates/base.html")
        print("Aucune modification n'a été effectuée.")
        return 1

    if not BACKUP_PATH.exists():
        shutil.copy2(BASE_PATH, BACKUP_PATH)
        print(f"Sauvegarde créée : {BACKUP_PATH.name}")

    text = text.replace(OLD_BLOCK, NEW_BLOCK, 1)
    BASE_PATH.write_text(text, encoding="utf-8")

    print("Protection globale du bouton rapport installée dans templates/base.html.")
    print("Une intervention completed affiche désormais 'Rapport déjà soumis'.")
    print("Le bouton 'Rédiger le rapport' n'est plus affiché pour une intervention terminée.")
    print("Aucune migration PostgreSQL n'est nécessaire.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"ERREUR : {exc}")
        sys.exit(1)
