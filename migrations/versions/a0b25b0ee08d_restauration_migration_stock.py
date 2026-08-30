"""Restauration migration stock

Revision ID: a0b25b0ee08d
Revises: 98ab2500f746
Create Date: 2026-08-09 00:52:01.954110

Cette révision avait été appliquée puis son contenu perdu localement. Elle est
reconstruite pour rendre l'historique reproductible. Une base déjà positionnée
au-delà de cette révision ne rejoue pas upgrade().
"""
from alembic import op
import sqlalchemy as sa

revision = 'a0b25b0ee08d'
down_revision = '98ab2500f746'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'stock_categories',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('nom', sa.String(length=255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('nom'),
    )
    op.create_table(
        'stock_locations',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('code', sa.String(length=100), nullable=True),
        sa.Column('nom', sa.String(length=255), nullable=False),
        sa.Column('parent_id', sa.Integer(), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['parent_id'], ['stock_locations.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('code'),
    )
    op.create_table(
        'stock_suppliers',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('nom', sa.String(length=255), nullable=False),
        sa.Column('contact', sa.String(length=255), nullable=True),
        sa.Column('telephone', sa.String(length=100), nullable=True),
        sa.Column('email', sa.String(length=255), nullable=True),
        sa.Column('site_web', sa.String(length=500), nullable=True),
        sa.Column('actif', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'stock_articles',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('reference', sa.String(length=150), nullable=False),
        sa.Column('designation', sa.String(length=500), nullable=False),
        sa.Column('reference_fabricant', sa.String(length=255), nullable=True),
        sa.Column('fabricant', sa.String(length=255), nullable=True),
        sa.Column('unite', sa.String(length=50), nullable=False, server_default='pièce'),
        sa.Column('categorie_id', sa.Integer(), nullable=True),
        sa.Column('emplacement_id', sa.Integer(), nullable=True),
        sa.Column('stock_min', sa.Numeric(14, 3), nullable=False, server_default='0'),
        sa.Column('stock_max', sa.Numeric(14, 3), nullable=True),
        sa.Column('prix_unitaire', sa.Numeric(14, 2), nullable=False, server_default='0'),
        sa.Column('actif', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('photo', sa.Text(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.ForeignKeyConstraint(['categorie_id'], ['stock_categories.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['emplacement_id'], ['stock_locations.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('reference'),
    )
    op.create_table(
        'stock_article_suppliers',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('article_id', sa.Integer(), nullable=False),
        sa.Column('supplier_id', sa.Integer(), nullable=False),
        sa.Column('reference_fournisseur', sa.String(length=255), nullable=True),
        sa.Column('prix', sa.Numeric(14, 2), nullable=True),
        sa.Column('delai_jours', sa.Integer(), nullable=True),
        sa.Column('prefere', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.ForeignKeyConstraint(['article_id'], ['stock_articles.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['supplier_id'], ['stock_suppliers.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('article_id', 'supplier_id', name='uq_stock_article_supplier'),
    )
    op.create_table(
        'stock_movements',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('article_id', sa.Integer(), nullable=False),
        sa.Column('type_mouvement', sa.String(length=30), nullable=False),
        sa.Column('quantite_delta', sa.Numeric(14, 3), nullable=False),
        sa.Column('prix_unitaire', sa.Numeric(14, 2), nullable=True),
        sa.Column('motif', sa.Text(), nullable=True),
        sa.Column('intervention_id', sa.Integer(), nullable=True),
        sa.Column('created_by_user_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.CheckConstraint("type_mouvement IN ('entree','sortie','correction','inventaire','consommation','retour')", name='ck_stock_movements_type'),
        sa.ForeignKeyConstraint(['article_id'], ['stock_articles.id']),
        sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['intervention_id'], ['interventions.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'stock_reservations',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('article_id', sa.Integer(), nullable=False),
        sa.Column('intervention_id', sa.Integer(), nullable=False),
        sa.Column('quantite', sa.Numeric(14, 3), nullable=False),
        sa.Column('quantite_consommee', sa.Numeric(14, 3), nullable=False, server_default='0'),
        sa.Column('statut', sa.String(length=30), nullable=False, server_default='reserved'),
        sa.Column('created_by_user_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.CheckConstraint("statut IN ('reserved','consumed','cancelled')", name='ck_stock_reservations_status'),
        sa.ForeignKeyConstraint(['article_id'], ['stock_articles.id']),
        sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['intervention_id'], ['interventions.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'intervention_stock_items',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('intervention_id', sa.Integer(), nullable=False),
        sa.Column('article_id', sa.Integer(), nullable=False),
        sa.Column('mouvement_id', sa.Integer(), nullable=True),
        sa.Column('quantite_utilisee', sa.Numeric(14, 3), nullable=False),
        sa.Column('prix_unitaire', sa.Numeric(14, 2), nullable=True),
        sa.Column('created_by_user_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.ForeignKeyConstraint(['article_id'], ['stock_articles.id']),
        sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['intervention_id'], ['interventions.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['mouvement_id'], ['stock_movements.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade():
    op.drop_table('intervention_stock_items')
    op.drop_table('stock_reservations')
    op.drop_table('stock_movements')
    op.drop_table('stock_article_suppliers')
    op.drop_table('stock_articles')
    op.drop_table('stock_suppliers')
    op.drop_table('stock_locations')
    op.drop_table('stock_categories')
