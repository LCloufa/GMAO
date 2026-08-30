"""Completer fournisseurs stock

Revision ID: f3cbcd8eb091
Revises: a0b25b0ee08d
Create Date: 2026-08-09 00:54:40.866854

"""
from alembic import op
import sqlalchemy as sa

revision = 'f3cbcd8eb091'
down_revision = 'a0b25b0ee08d'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('stock_suppliers', schema=None) as batch_op:
        batch_op.add_column(sa.Column('adresse', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('siret', sa.String(length=14), nullable=True))
        batch_op.add_column(sa.Column('contact_nom', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('contact_prenom', sa.String(length=255), nullable=True))
        batch_op.create_unique_constraint('uq_stock_suppliers_siret', ['siret'])
        batch_op.drop_column('contact')


def downgrade():
    with op.batch_alter_table('stock_suppliers', schema=None) as batch_op:
        batch_op.add_column(sa.Column('contact', sa.VARCHAR(length=255), autoincrement=False, nullable=True))
        batch_op.drop_constraint('uq_stock_suppliers_siret', type_='unique')
        batch_op.drop_column('contact_prenom')
        batch_op.drop_column('contact_nom')
        batch_op.drop_column('siret')
        batch_op.drop_column('adresse')
