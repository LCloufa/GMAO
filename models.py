from datetime import datetime

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import CheckConstraint


db = SQLAlchemy()


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(255), unique=True, nullable=False)
    password = db.Column(db.Text, nullable=False)
    role = db.Column(db.String(50), nullable=False, default="user")


class Client(db.Model):
    __tablename__ = "clients"

    id = db.Column(db.Integer, primary_key=True)
    nom = db.Column(db.String(255), nullable=False)
    email = db.Column(db.String(255))
    telephone = db.Column(db.String(100))
    site_web = db.Column(db.String(500))
    rythme_horaire = db.Column(db.String(20), nullable=False, default="1x8")


class Technicien(db.Model):
    __tablename__ = "techniciens"

    id = db.Column(db.Integer, primary_key=True)
    nom = db.Column(db.String(255), nullable=False)
    prenom = db.Column(db.String(255), nullable=False)
    code = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(255))
    telephone = db.Column(db.String(100))
    specialite = db.Column(db.String(255))
    statut = db.Column(db.String(50), nullable=False, default="Actif")


class TechnicienUserLink(db.Model):
    __tablename__ = "technicien_user_links"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    technicien_id = db.Column(
        db.Integer,
        db.ForeignKey("techniciens.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )


class Equipement(db.Model):
    __tablename__ = "equipements"

    id = db.Column(db.Integer, primary_key=True)
    nom = db.Column(db.String(255), nullable=False)
    code = db.Column(db.String(100))
    type = db.Column(db.String(255))
    statut = db.Column(db.String(100), nullable=False, default="Opérationnel")
    emplacement = db.Column(db.String(255))
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"))
    fabricant = db.Column(db.String(255))
    modele = db.Column(db.String(255))
    numero_serie = db.Column(db.String(255))
    date_installation = db.Column(db.String(50))
    photo = db.Column(db.Text)


class EquipementDocument(db.Model):
    __tablename__ = "equipement_documents"

    id = db.Column(db.Integer, primary_key=True)
    equipement_id = db.Column(db.Integer, db.ForeignKey("equipements.id"))
    filename = db.Column(db.String(500))
    filepath = db.Column(db.Text)


class Intervention(db.Model):
    __tablename__ = "interventions"
    __table_args__ = (
        CheckConstraint(
            "type IN ('preventive','corrective','predictive','emergency')",
            name="ck_interventions_type",
        ),
        CheckConstraint(
            "priority IN ('low','medium','high','critical')",
            name="ck_interventions_priority",
        ),
        CheckConstraint(
            "status IN ('planned','in_progress','completed','cancelled','postponed')",
            name="ck_interventions_status",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.Text, nullable=False)
    equipment_id = db.Column(db.Integer, db.ForeignKey("equipements.id"), nullable=False)
    routine_id = db.Column(db.String(255))
    type = db.Column(db.String(30), nullable=False)
    priority = db.Column(db.String(30), nullable=False, default="medium")
    status = db.Column(db.String(30), nullable=False, default="planned")
    scheduled_date = db.Column(db.String(50), nullable=False)
    scheduled_time = db.Column(db.String(50))
    assigned_to = db.Column(db.Integer, db.ForeignKey("techniciens.id"))
    estimated_duration = db.Column(db.Integer)
    description = db.Column(db.Text)
    completion_date = db.Column(db.String(50))


class DeclarationPanne(db.Model):
    __tablename__ = "declarations_panne"
    __table_args__ = (
        CheckConstraint(
            "urgency IN ('low','medium','high','critical')",
            name="ck_declarations_panne_urgency",
        ),
        CheckConstraint(
            "status IN ('pending','in_progress','resolved','rejected')",
            name="ck_declarations_panne_status",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    equipment_id = db.Column(db.Integer, db.ForeignKey("equipements.id"), nullable=False)
    declared_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    declared_by_name = db.Column(db.String(255))
    title = db.Column(db.Text, nullable=False)
    description = db.Column(db.Text, nullable=False)
    urgency = db.Column(db.String(30), nullable=False, default="medium")
    location = db.Column(db.String(500))
    status = db.Column(db.String(30), nullable=False, default="pending")
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime)
    intervention_id = db.Column(db.Integer, db.ForeignKey("interventions.id"))


class DeclarationPhoto(db.Model):
    __tablename__ = "declaration_photos"

    id = db.Column(db.Integer, primary_key=True)
    declaration_id = db.Column(db.Integer, db.ForeignKey("declarations_panne.id"), nullable=False)
    filepath = db.Column(db.Text, nullable=False)


class RapportIntervention(db.Model):
    __tablename__ = "rapports_intervention"
    __table_args__ = (
        CheckConstraint(
            "etat IN ('Opérationnel','Nécessite un suivi','Toujours en panne')",
            name="ck_rapports_intervention_etat",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    intervention_id = db.Column(db.Integer, db.ForeignKey("interventions.id"), nullable=False)
    travaux = db.Column(db.Text, nullable=False)
    heure_debut = db.Column(db.String(50))
    heure_fin = db.Column(db.String(50), nullable=False)
    observations = db.Column(db.Text)
    etat = db.Column(db.String(100), nullable=False)
    recommandations = db.Column(db.Text)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime)
