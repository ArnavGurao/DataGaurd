"""Email verification and idempotent S3 ingestion.

Existing users remain unverified; no existing address is silently trusted.
"""
from alembic import op
import sqlalchemy as sa

revision = "7ab24e9c1001"
down_revision = "036e986f36f3"
branch_labels = None
depends_on = None


def upgrade():
    for name in ("email_verified_at", "verification_expires_at", "verification_sent_at"):
        op.add_column("users", sa.Column(name, sa.DateTime(timezone=True)))
    op.add_column("users", sa.Column("verification_digest", sa.String(64)))
    op.add_column("users", sa.Column("verification_attempts", sa.Integer(), nullable=False, server_default="0"))
    op.create_table("ingestion_sources",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("rule_set_id", sa.Uuid(), sa.ForeignKey("rule_sets.id"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("enabled", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
    op.create_index("ix_ingestion_sources_owner_id", "ingestion_sources", ["owner_id"])
    op.create_table("ingestion_receipts",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("source_id", sa.Uuid(), sa.ForeignKey("ingestion_sources.id"), nullable=False),
        sa.Column("job_id", sa.Uuid(), sa.ForeignKey("jobs.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))


def downgrade():
    op.drop_table("ingestion_receipts")
    op.drop_index("ix_ingestion_sources_owner_id", table_name="ingestion_sources")
    op.drop_table("ingestion_sources")
    for name in ("verification_attempts", "verification_digest", "verification_sent_at", "verification_expires_at", "email_verified_at"):
        op.drop_column("users", name)
