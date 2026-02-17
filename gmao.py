import tkinter as tk
from tkinter import ttk, messagebox
import json
import os

FICHIER_SAUVEGARDE = "equipements.json"
equipements = []

# ==========================
# Sauvegarde / Chargement
# ==========================

def sauvegarder():
    with open(FICHIER_SAUVEGARDE, "w") as f:
        json.dump(equipements, f)

def charger():
    global equipements
    if os.path.exists(FICHIER_SAUVEGARDE):
        with open(FICHIER_SAUVEGARDE, "r") as f:
            equipements = json.load(f)
            for eq in equipements:
                tree.insert("", tk.END, values=(eq["nom"], eq["serie"], eq["localisation"]))

# ==========================
# Fonctions
# ==========================

def ajouter_equipement():
    nom = entry_nom.get()
    serie = entry_serie.get()
    localisation = entry_localisation.get()

    if not nom or not serie:
        messagebox.showwarning("Erreur", "Nom et numéro de série obligatoires")
        return

    eq = {
        "nom": nom,
        "serie": serie,
        "localisation": localisation
    }

    equipements.append(eq)
    tree.insert("", tk.END, values=(nom, serie, localisation))
    sauvegarder()

    entry_nom.delete(0, tk.END)
    entry_serie.delete(0, tk.END)
    entry_localisation.delete(0, tk.END)

def supprimer_equipement():
    selected = tree.selection()
    if not selected:
        messagebox.showwarning("Erreur", "Sélectionne un équipement")
        return

    item = selected[0]
    index = tree.index(item)

    tree.delete(item)
    equipements.pop(index)
    sauvegarder()

# ==========================
# Interface
# ==========================

root = tk.Tk()
root.title("GMAO - Gestion des équipements")
root.geometry("900x500")
root.configure(bg="#f4f6f9")

style = ttk.Style()
style.theme_use("clam")

# Style tableau
style.configure("Treeview",
                background="white",
                foreground="black",
                rowheight=30,
                fieldbackground="white",
                font=("Segoe UI", 10))

style.configure("Treeview.Heading",
                font=("Segoe UI", 11, "bold"))

# ==========================
# Layout principal
# ==========================

# Sidebar
sidebar = tk.Frame(root, bg="#1f2a44", width=200)
sidebar.pack(side="left", fill="y")

tk.Label(sidebar, text="GMAO", bg="#1f2a44", fg="white",
         font=("Segoe UI", 16, "bold")).pack(pady=20)

tk.Button(sidebar, text="Équipements", bg="#273352",
          fg="white", relief="flat").pack(fill="x", padx=20, pady=5)

tk.Button(sidebar, text="Interventions", bg="#1f2a44",
          fg="white", relief="flat").pack(fill="x", padx=20, pady=5)

# Main content
main = tk.Frame(root, bg="#f4f6f9")
main.pack(fill="both", expand=True, padx=20, pady=20)

tk.Label(main, text="Gestion des équipements",
         bg="#f4f6f9",
         font=("Segoe UI", 18, "bold")).pack(anchor="w")

# Formulaire
form_frame = tk.Frame(main, bg="#f4f6f9")
form_frame.pack(fill="x", pady=10)

entry_nom = ttk.Entry(form_frame)
entry_serie = ttk.Entry(form_frame)
entry_localisation = ttk.Entry(form_frame)

ttk.Label(form_frame, text="Nom").grid(row=0, column=0, padx=5)
entry_nom.grid(row=1, column=0, padx=5)

ttk.Label(form_frame, text="N° Série").grid(row=0, column=1, padx=5)
entry_serie.grid(row=1, column=1, padx=5)

ttk.Label(form_frame, text="Localisation").grid(row=0, column=2, padx=5)
entry_localisation.grid(row=1, column=2, padx=5)

ttk.Button(form_frame, text="Ajouter", command=ajouter_equipement)\
    .grid(row=1, column=3, padx=10)

ttk.Button(form_frame, text="Supprimer", command=supprimer_equipement)\
    .grid(row=1, column=4, padx=10)

# Tableau
tree = ttk.Treeview(main, columns=("Nom", "Serie", "Localisation"), show="headings")

tree.heading("Nom", text="Nom")
tree.heading("Serie", text="N° Série")
tree.heading("Localisation", text="Localisation")

tree.column("Nom", width=200)
tree.column("Serie", width=150)
tree.column("Localisation", width=200)

tree.pack(fill="both", expand=True, pady=10)

# Charger données
charger()

root.mainloop()
