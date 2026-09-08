"""Add favorite and metadata to financial planning

Revision ID: a1b2c3d4e5f6
Revises: 9e4e9e98963d
Create Date: 2026-09-07

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a1b2c3d4e5f6'
down_revision = '9e4e9e98963d'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('financial_planning', schema=None) as batch_op:
        batch_op.add_column(sa.Column('name', sa.String(length=200), nullable=True, server_default='Planejamento'))
        batch_op.add_column(sa.Column('observation', sa.Text(), nullable=True, server_default=''))
        batch_op.add_column(sa.Column('plan_date', sa.Date(), nullable=True))
        batch_op.add_column(sa.Column('is_favorite', sa.Boolean(), nullable=True, server_default=sa.false()))

    with op.batch_alter_table('financial_planning', schema=None) as batch_op:
        batch_op.alter_column('name', server_default=None)
        batch_op.alter_column('observation', server_default=None)
        batch_op.alter_column('is_favorite', server_default=None)


def downgrade():
    with op.batch_alter_table('financial_planning', schema=None) as batch_op:
        batch_op.drop_column('is_favorite')
        batch_op.drop_column('plan_date')
        batch_op.drop_column('observation')
        batch_op.drop_column('name')
