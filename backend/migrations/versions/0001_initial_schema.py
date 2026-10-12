"""Frozen relational schema from the first single-ledger release."""
from alembic import op
import sqlalchemy as sa


revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def _frozen_tables():
    metadata = sa.MetaData()
    return [
      sa.Table(
        "planner_state",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False)),
      sa.Table(
        "instrument_catalog",
        metadata,
        sa.Column("exchange", sa.String(16), primary_key=True),
        sa.Column("symbol", sa.String(32), primary_key=True),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("asset_class", sa.String(16)),
        sa.Column("currency", sa.String(3)),
        sa.Column("native_exchange", sa.String(32)),
        sa.Column("source", sa.String(80), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False)),
      sa.Table(
        "portfolio_history",
        metadata,
        sa.Column("account", sa.String(10), primary_key=True),
        sa.Column("date", sa.String(10), primary_key=True),
        sa.Column("value_sgd", sa.String(64), nullable=False),
        sa.Column("gross_change_sgd", sa.String(64)),
        sa.Column("adjusted_change_sgd", sa.String(64)),
        sa.Column("external_flow_sgd", sa.String(64), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False)),
      sa.Table(
        "stock_cache",
        metadata,
        sa.Column("security_id", sa.String(80), primary_key=True),
        sa.Column("kind", sa.String(32), primary_key=True),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False)),
    ]


def upgrade():
    bind = op.get_bind()
    for table in _frozen_tables():
        table.create(bind=bind)


def downgrade():
    raise RuntimeError("Automatic downgrade is unsupported; restore a database backup instead")
