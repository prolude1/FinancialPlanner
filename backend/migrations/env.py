"""Alembic environment; connections and transactions are owned by MigrationRunner."""
from alembic import context

config = context.config


def run_migrations_online():
    connection = config.attributes.get("connection")
    if connection is None:
        raise RuntimeError("MigrationRunner must provide the locked database connection")
    context.configure(connection=connection, target_metadata=None,
                      transaction_per_migration=False, transactional_ddl=True)
    with context.begin_transaction():
        context.run_migrations()


run_migrations_online()
