"""Structure technique 3 niveaux et donnees achat composants

Revision ID: e14f6a7c2b90
Revises: c7d4e2a91f30
Create Date: 2026-08-30

Ajoute aux elements de structure machine un fournisseur, un delai d'obtention
et un prix. La hierarchie Ensemble > Sous-ensemble > Composant est imposee
par l'API et l'interface, sans modifier les elements deja existants.
"""
from alembic import op
import sqlalchemy as sa

revision = 'e14f6a7c2b90'
down_revision = 'c7d4e2a91f30'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('equipement_components', schema=None) as batch_op:
        batch_op.add_column(sa.Column('supplier_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('delai_obtention_jours', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('prix_unitaire', sa.Numeric(14, 2), nullable=True))
        batch_op.create_foreign_key(
            'fk_equipement_components_supplier_id',
            'stock_suppliers',
            ['supplier_id'],
            ['id'],
            ondelete='SET NULL',
        )
        batch_op.create_index('ix_equipement_components_supplier_id', ['supplier_id'], unique=False)


def downgrade():
    with op.batch_alter_table('equipement_components', schema=None) as batch_op:
        batch_op.drop_index('ix_equipement_components_supplier_id')
        batch_op.drop_constraint('fk_equipement_components_supplier_id', type_='foreignkey')
        batch_op.drop_column('prix_unitaire')
        batch_op.drop_column('delai_obtention_jours')
        batch_op.drop_column('supplier_id')
