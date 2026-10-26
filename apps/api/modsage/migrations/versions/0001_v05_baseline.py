"""Adopt the complete v0.5 schema as the first immutable Alembic revision."""

from alembic import op

from modsage.migrations.baseline_v05 import baseline_metadata

revision = "0001_v05_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # This metadata is frozen in this revision's package, not imported from live models.
    # Existing legacy databases are inspected and stamped by the guarded runner instead.
    baseline_metadata().create_all(op.get_bind(), checkfirst=False)


def downgrade():
    raise RuntimeError("Destructive downgrade is disabled; restore a verified backup instead")
