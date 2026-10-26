"""Alembic environment using the connection/transaction owned by the guarded runner."""

from alembic import context

connection = context.config.attributes.get("connection")
if connection is None:
    raise RuntimeError("Use ModSage db-upgrade; an already locked connection is required")
context.configure(connection=connection, transactional_ddl=True)
with context.begin_transaction():
    context.run_migrations()
