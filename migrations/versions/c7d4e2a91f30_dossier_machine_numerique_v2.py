"""Dossier Machine Numérique V2

Revision ID: c7d4e2a91f30
Revises: f3cbcd8eb091
Create Date: 2026-08-30

Ajoute les fondations du dossier machine : arborescence composants,
caractéristiques dynamiques, compteurs et relevés, pièces compatibles.
Migration strictement additive pour les données existantes.
"""
from alembic import op
import sqlalchemy as sa

revision = 'c7d4e2a91f30'
down_revision = 'f3cbcd8eb091'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'equipement_components',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('equipement_id', sa.Integer(), nullable=False),
        sa.Column('parent_id', sa.Integer(), nullable=True),
        sa.Column('code', sa.String(length=120), nullable=True),
        sa.Column('nom', sa.String(length=255), nullable=False),
        sa.Column('type_composant', sa.String(length=120), nullable=True),
        sa.Column('criticite', sa.String(length=30), nullable=False, server_default='medium'),
        sa.Column('fabricant', sa.String(length=255), nullable=True),
        sa.Column('modele', sa.String(length=255), nullable=True),
        sa.Column('numero_serie', sa.String(length=255), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('ordre', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('actif', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['equipement_id'], ['equipements.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['parent_id'], ['equipement_components.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_equipement_components_equipement_id', 'equipement_components', ['equipement_id'])
    op.create_index('ix_equipement_components_parent_id', 'equipement_components', ['parent_id'])

    op.create_table(
        'equipement_specifications',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('equipement_id', sa.Integer(), nullable=False),
        sa.Column('component_id', sa.Integer(), nullable=True),
        sa.Column('groupe', sa.String(length=120), nullable=True),
        sa.Column('nom', sa.String(length=255), nullable=False),
        sa.Column('valeur', sa.Text(), nullable=True),
        sa.Column('unite', sa.String(length=80), nullable=True),
        sa.Column('type_valeur', sa.String(length=50), nullable=False, server_default='text'),
        sa.Column('ordre', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['component_id'], ['equipement_components.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['equipement_id'], ['equipements.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_equipement_specifications_equipement_id', 'equipement_specifications', ['equipement_id'])
    op.create_index('ix_equipement_specifications_component_id', 'equipement_specifications', ['component_id'])

    op.create_table(
        'equipement_counters',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('equipement_id', sa.Integer(), nullable=False),
        sa.Column('component_id', sa.Integer(), nullable=True),
        sa.Column('nom', sa.String(length=255), nullable=False),
        sa.Column('unite', sa.String(length=80), nullable=False, server_default='h'),
        sa.Column('type_compteur', sa.String(length=80), nullable=False, server_default='usage'),
        sa.Column('actif', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.ForeignKeyConstraint(['component_id'], ['equipement_components.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['equipement_id'], ['equipements.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_equipement_counters_equipement_id', 'equipement_counters', ['equipement_id'])

    op.create_table(
        'equipement_counter_readings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('counter_id', sa.Integer(), nullable=False),
        sa.Column('valeur', sa.Numeric(18, 3), nullable=False),
        sa.Column('releve_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('created_by_user_id', sa.Integer(), nullable=True),
        sa.Column('note', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['counter_id'], ['equipement_counters.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_equipement_counter_readings_counter_id', 'equipement_counter_readings', ['counter_id'])
    op.create_index('ix_equipement_counter_readings_releve_at', 'equipement_counter_readings', ['releve_at'])

    op.create_table(
        'equipement_parts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('equipement_id', sa.Integer(), nullable=False),
        sa.Column('component_id', sa.Integer(), nullable=True),
        sa.Column('article_id', sa.Integer(), nullable=False),
        sa.Column('quantite_recommandee', sa.Numeric(14, 3), nullable=True),
        sa.Column('critique', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.ForeignKeyConstraint(['article_id'], ['stock_articles.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['component_id'], ['equipement_components.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['equipement_id'], ['equipements.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_equipement_parts_equipement_id', 'equipement_parts', ['equipement_id'])
    op.create_index('ix_equipement_parts_article_id', 'equipement_parts', ['article_id'])


def downgrade():
    op.drop_index('ix_equipement_parts_article_id', table_name='equipement_parts')
    op.drop_index('ix_equipement_parts_equipement_id', table_name='equipement_parts')
    op.drop_table('equipement_parts')

    op.drop_index('ix_equipement_counter_readings_releve_at', table_name='equipement_counter_readings')
    op.drop_index('ix_equipement_counter_readings_counter_id', table_name='equipement_counter_readings')
    op.drop_table('equipement_counter_readings')

    op.drop_index('ix_equipement_counters_equipement_id', table_name='equipement_counters')
    op.drop_table('equipement_counters')

    op.drop_index('ix_equipement_specifications_component_id', table_name='equipement_specifications')
    op.drop_index('ix_equipement_specifications_equipement_id', table_name='equipement_specifications')
    op.drop_table('equipement_specifications')

    op.drop_index('ix_equipement_components_parent_id', table_name='equipement_components')
    op.drop_index('ix_equipement_components_equipement_id', table_name='equipement_components')
    op.drop_table('equipement_components')
