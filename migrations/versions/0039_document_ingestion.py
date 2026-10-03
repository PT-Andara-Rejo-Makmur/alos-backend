"""Link immutable objects and extraction jobs to canonical document/source versions."""

import sqlalchemy as sa
from alembic import op

revision = "0039_document_ingestion"
down_revision = "0038_ara_progress"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "source_versions", sa.Column("extracted_content", sa.Text(), nullable=True), schema="core"
    )
    op.create_table(
        "document_uploads",
        sa.Column("upload_id", sa.String(128), primary_key=True),
        *(
            sa.Column(name, sa.String(128), nullable=False)
            for name in ("tenant_id", "organization_id", "workspace_id")
        ),
        sa.Column("document_id", sa.String(128), nullable=False),
        sa.Column("version", sa.String(100), nullable=False),
        sa.Column("filename", sa.String(180), nullable=False),
        sa.Column("source_type", sa.String(16), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.String(128), sa.ForeignKey("jobs.queue.job_id"), nullable=False),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "tenant_id", "workspace_id", "document_id", "version", name="uq_document_upload_version"
        ),
        sa.CheckConstraint("size_bytes > 0", name="ck_document_upload_size"),
        schema="core",
    )
    op.create_index(
        "ix_document_upload_scope",
        "document_uploads",
        ["tenant_id", "organization_id", "workspace_id", "document_id"],
        schema="core",
    )


def downgrade() -> None:
    raise RuntimeError("Preserve uploaded objects and source provenance; use forward correction")
