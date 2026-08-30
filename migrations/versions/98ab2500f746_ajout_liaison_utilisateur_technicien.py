"""Ajout liaison utilisateur technicien

Revision ID: 98ab2500f746
Revises:
Create Date: 2026-08-08 23:43:58.661014

"""
from alembic import op
import sqlalchemy as sa

revision = '98ab2500f746'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'technicien_user_links',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('technicien_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['technicien_id'], ['techniciens.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('technicien_id'),
        sa.UniqueConstraint('user_id'),
    )


def downgrade():
    op.drop_table('technicien_user_links')
