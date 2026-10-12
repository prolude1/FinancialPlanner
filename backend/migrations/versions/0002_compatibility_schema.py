"""Complete the relational schema and adopt supported pre-Alembic databases.

This revision intentionally owns frozen table definitions and compatibility
changes. It must not import application metadata or domain initialization code.
"""
from alembic import op
import sqlalchemy as sa


revision = "0002_compatibility_schema"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def _frozen_tables():
    """Return the post-Keycloak/workbook/brokerage schema captured for this revision."""
    metadata = sa.MetaData()
    return [
        sa.Table("planner_state", metadata,
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("schema_version", sa.Integer(), nullable=False),
            sa.Column("data", sa.JSON(), nullable=False)),
        sa.Table("instrument_catalog", metadata,
            sa.Column("exchange", sa.String(16), primary_key=True),
            sa.Column("symbol", sa.String(32), primary_key=True),
            sa.Column("name", sa.String(300), nullable=False),
            sa.Column("asset_class", sa.String(16)),
            sa.Column("currency", sa.String(3)),
            sa.Column("native_exchange", sa.String(32)),
            sa.Column("source", sa.String(80), nullable=False),
            sa.Column("active", sa.Boolean(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False)),
        sa.Table("portfolio_history", metadata,
            sa.Column("account", sa.String(10), primary_key=True),
            sa.Column("date", sa.String(10), primary_key=True),
            sa.Column("value_sgd", sa.String(64), nullable=False),
            sa.Column("gross_change_sgd", sa.String(64)),
            sa.Column("adjusted_change_sgd", sa.String(64)),
            sa.Column("external_flow_sgd", sa.String(64)),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False)),
        sa.Table("stock_cache", metadata,
            sa.Column("security_id", sa.String(80), primary_key=True),
            sa.Column("kind", sa.String(32), primary_key=True),
            sa.Column("data", sa.JSON(), nullable=False),
            sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False)),
        sa.Table("tenant_ledger", metadata,
            sa.Column("principal", sa.String(64), primary_key=True),
            sa.Column("schema_version", sa.Integer(), nullable=False),
            sa.Column("data", sa.JSON(), nullable=False)),
        sa.Table("tenant_portfolio_history", metadata,
            sa.Column("principal", sa.String(64), primary_key=True),
            sa.Column("account", sa.String(10), primary_key=True),
            sa.Column("date", sa.String(10), primary_key=True),
            sa.Column("value_sgd", sa.String(64), nullable=False),
            sa.Column("gross_change_sgd", sa.String(64)),
            sa.Column("adjusted_change_sgd", sa.String(64)),
            sa.Column("external_flow_sgd", sa.String(64)),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False)),
        sa.Table("portfolio_deposit_history", metadata,
            sa.Column("account", sa.String(10), primary_key=True),
            sa.Column("date", sa.String(10), primary_key=True),
            sa.Column("amount_sgd", sa.String(64)),
            sa.Column("missing_fx_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("stale_fx_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("stale_fx_last_success_at", sa.String(32))),
        sa.Table("tenant_portfolio_deposit_history", metadata,
            sa.Column("principal", sa.String(64), primary_key=True),
            sa.Column("account", sa.String(10), primary_key=True),
            sa.Column("date", sa.String(10), primary_key=True),
            sa.Column("amount_sgd", sa.String(64)),
            sa.Column("missing_fx_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("stale_fx_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("stale_fx_last_success_at", sa.String(32))),
        sa.Table("portfolio_deposit_fx_rates", metadata,
            sa.Column("currency", sa.String(3), primary_key=True),
            sa.Column("date", sa.String(10), primary_key=True),
            sa.Column("rate", sa.String(64), nullable=False),
            sa.Column("fetched_at", sa.BigInteger(), nullable=False)),
        sa.Table("tenant_portfolio_deposit_fx_rates", metadata,
            sa.Column("principal", sa.String(64), primary_key=True),
            sa.Column("currency", sa.String(3), primary_key=True),
            sa.Column("date", sa.String(10), primary_key=True),
            sa.Column("rate", sa.String(64), nullable=False),
            sa.Column("fetched_at", sa.BigInteger(), nullable=False)),
        sa.Table("bot_runtime", metadata,
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("data", sa.JSON(), nullable=False)),
        sa.Table("legacy_ledger_claim", metadata,
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("claimed_by", sa.String(64)),
            sa.Column("claimed_at", sa.DateTime(timezone=True))),
        sa.Table("legacy_ledger_claim_codes", metadata,
            sa.Column("token_hash", sa.String(64), primary_key=True),
            sa.Column("created_at", sa.BigInteger(), nullable=False),
            sa.Column("expires_at", sa.BigInteger(), nullable=False),
            sa.Column("consumed_at", sa.BigInteger())),
        sa.Table("telegram_connections", metadata,
            sa.Column("principal", sa.String(255), primary_key=True),
            sa.Column("telegram_user_id", sa.BigInteger(), nullable=False, unique=True),
            sa.Column("linked_at", sa.BigInteger(), nullable=False)),
        sa.Table("telegram_link_challenges", metadata,
            sa.Column("token_hash", sa.String(64), primary_key=True),
            sa.Column("principal", sa.String(255), nullable=False, index=True),
            sa.Column("created_at", sa.BigInteger(), nullable=False),
            sa.Column("expires_at", sa.BigInteger(), nullable=False),
            sa.Column("consumed_at", sa.BigInteger())),
        sa.Table("telegram_user_state", metadata,
            sa.Column("principal", sa.String(64), primary_key=True),
            sa.Column("telegram_user_id", sa.BigInteger(), primary_key=True),
            sa.Column("data", sa.JSON(), nullable=False)),
        sa.Table("workbook_confirmations", metadata,
            sa.Column("token_hash", sa.String(64), primary_key=True),
            sa.Column("principal", sa.String(255)),
            sa.Column("workbook_sha256", sa.String(64), nullable=False),
            sa.Column("current_revision", sa.Integer(), nullable=False),
            sa.Column("current_etag", sa.String(64), nullable=False),
            sa.Column("created_at", sa.BigInteger(), nullable=False),
            sa.Column("expires_at", sa.BigInteger(), nullable=False),
            sa.Column("workbook_bytes", sa.LargeBinary(), nullable=False)),
    ]


def _ensure_tables():
    bind = op.get_bind()
    for table in _frozen_tables():
        table.create(bind=bind, checkfirst=True)


def _add_missing_column(table, column):
    inspector = sa.inspect(op.get_bind())
    names = {item["name"] for item in inspector.get_columns(table)}
    if column.name not in names:
        op.add_column(table, column)


def _make_external_flow_nullable(table):
    column = next(item for item in sa.inspect(op.get_bind()).get_columns(table)
                  if item["name"] == "external_flow_sgd")
    if column["nullable"]:
        return
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table(table, recreate="always") as batch:
            batch.alter_column("external_flow_sgd", existing_type=sa.String(64), nullable=True)
    else:
        op.alter_column(table, "external_flow_sgd", existing_type=sa.String(64), nullable=True)


def _initialize_singletons():
    bind = op.get_bind()
    state = sa.table("planner_state",
        sa.column("id", sa.Integer), sa.column("schema_version", sa.Integer), sa.column("data", sa.JSON))
    bot_runtime = sa.table("bot_runtime", sa.column("id", sa.Integer), sa.column("data", sa.JSON))
    legacy_claim = sa.table("legacy_ledger_claim",
        sa.column("id", sa.Integer), sa.column("claimed_by", sa.String), sa.column("claimed_at", sa.DateTime))

    row = bind.execute(sa.select(state).where(state.c.id == 1)).mappings().first()
    if row is None:
        # Frozen initial JSON contract. Keep ledger schema_version independent
        # from this relational migration revision.
        data = {
            "accounts": {}, "credit_accounts": {}, "events": [], "loans": {},
            "prices": {}, "fx": {}, "revision": 0, "receipts": {},
            "recurring_schedules": [], "provider_status": {},
        }
        bind.execute(state.insert().values(id=1, schema_version=1, data=data))
        bot_data = {"offset": 0, "session": None}
    else:
        if row["schema_version"] != 1:
            raise RuntimeError("Unsupported planner_state schema_version during database migration")
        data = dict(row["data"])
        bot_data = data.pop("bot", {"offset": 0, "session": None})
        if data != row["data"]:
            bind.execute(state.update().where(state.c.id == 1).values(data=data))

    existing_bot = bind.execute(sa.select(bot_runtime.c.id).where(bot_runtime.c.id == 1)).first()
    if existing_bot is None:
        bind.execute(bot_runtime.insert().values(id=1, data=bot_data))
    if bind.execute(sa.select(legacy_claim.c.id).where(legacy_claim.c.id == 1)).first() is None:
        bind.execute(legacy_claim.insert().values(id=1, claimed_by=None, claimed_at=None))

    tenant_ledger = sa.table("tenant_ledger",
        sa.column("principal", sa.String), sa.column("schema_version", sa.Integer))
    tenant_versions = bind.execute(sa.select(tenant_ledger.c.schema_version)).scalars().all()
    if any(version != 1 for version in tenant_versions):
        raise RuntimeError("Unsupported tenant_ledger schema_version during database migration")


def upgrade():
    _ensure_tables()
    _add_missing_column("portfolio_deposit_history",
        sa.Column("missing_fx_count", sa.Integer(), nullable=False, server_default="0"))
    _add_missing_column("tenant_portfolio_deposit_history",
        sa.Column("missing_fx_count", sa.Integer(), nullable=False, server_default="0"))
    _add_missing_column("portfolio_deposit_history",
        sa.Column("stale_fx_count", sa.Integer(), nullable=False, server_default="0"))
    _add_missing_column("tenant_portfolio_deposit_history",
        sa.Column("stale_fx_count", sa.Integer(), nullable=False, server_default="0"))
    _add_missing_column("portfolio_deposit_history", sa.Column("stale_fx_last_success_at", sa.String(32)))
    _add_missing_column("tenant_portfolio_deposit_history", sa.Column("stale_fx_last_success_at", sa.String(32)))
    _make_external_flow_nullable("portfolio_history")
    _make_external_flow_nullable("tenant_portfolio_history")
    _initialize_singletons()


def downgrade():
    raise RuntimeError("Automatic downgrade is unsupported; restore a database backup instead")
