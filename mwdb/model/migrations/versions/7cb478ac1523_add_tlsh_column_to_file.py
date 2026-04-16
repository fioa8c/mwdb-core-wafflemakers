"""add tlsh column to file

Revision ID: 7cb478ac1523
Revises: 01dea56ffaf7
Create Date: 2026-04-16 10:32:11.639814

"""
import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "7cb478ac1523"
down_revision = "01dea56ffaf7"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "object",
        sa.Column("tlsh", sa.String(length=72, collation="C"), nullable=True),
    )
    op.create_index("ix_object_tlsh", "object", ["tlsh"], unique=False)


def downgrade():
    op.drop_index("ix_object_tlsh", table_name="object")
    op.drop_column("object", "tlsh")
