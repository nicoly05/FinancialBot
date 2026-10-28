"""Profile fields, habitual amounts and password reset codes

Revision ID: b7c8d9e0f1a2
Revises: 19514e6b7cf0
Create Date: 2026-10-28 20:10:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'b7c8d9e0f1a2'
down_revision = '19514e6b7cf0'
branch_labels = None
depends_on = None


def _columns(table):
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table not in inspector.get_table_names():
        return set()
    return {column['name'] for column in inspector.get_columns(table)}


def _add_column(table, name, column):
    if name not in _columns(table):
        op.add_column(table, column)


def upgrade():
    _add_column('user', 'full_name', sa.Column('full_name', sa.String(length=120), nullable=True))
    _add_column('user', 'phone', sa.Column('phone', sa.String(length=40), nullable=True))
    _add_column('user', 'birth_date', sa.Column('birth_date', sa.Date(), nullable=True))
    _add_column('user', 'city', sa.Column('city', sa.String(length=80), nullable=True))
    _add_column('user', 'currency', sa.Column('currency', sa.String(length=8), nullable=True))
    _add_column('user', 'occupation', sa.Column('occupation', sa.String(length=80), nullable=True))

    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if 'user_preset' not in tables:
        op.create_table(
            'user_preset',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('user_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=False),
            sa.Column('key', sa.String(length=40), nullable=False),
            sa.Column('amount', sa.Float(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=True),
            sa.UniqueConstraint('user_id', 'key', name='uq_user_preset_key'),
        )
    if 'password_reset_code' not in tables:
        op.create_table(
            'password_reset_code',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('email', sa.String(length=120), nullable=False),
            sa.Column('user_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=True),
            sa.Column('code_hash', sa.String(length=128), nullable=False),
            sa.Column('expires_at', sa.DateTime(), nullable=False),
            sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('used', sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column('created_at', sa.DateTime(), nullable=True),
        )
        op.create_index('ix_password_reset_code_email', 'password_reset_code', ['email'])


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if 'password_reset_code' in tables:
        op.drop_index('ix_password_reset_code_email', table_name='password_reset_code')
        op.drop_table('password_reset_code')
    if 'user_preset' in tables:
        op.drop_table('user_preset')
    cols = _columns('user')
    for name in ('occupation', 'currency', 'city', 'birth_date', 'phone', 'full_name'):
        if name in cols:
            op.drop_column('user', name)
