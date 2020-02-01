"""empty message

Revision ID: 1b1ce27ec872
Revises: 
Create Date: 2020-02-01 17:20:23.237105

"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "1b1ce27ec872"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # pycrypto is not enabled by default and we need it for gen_random_uuid().
    op.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto"')
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table(
        "user",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("github_id", sa.Integer(), nullable=False),
        sa.Column("github_username", sa.String(), nullable=False),
        sa.Column("github_access_token", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_user_github_id"), "user", ["github_id"], unique=True)
    op.create_index(
        op.f("ix_user_github_username"), "user", ["github_username"], unique=True
    )
    op.create_index(op.f("ix_user_id"), "user", ["id"], unique=False)
    op.create_table(
        "session",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("session_key", sa.String(), nullable=False),
        sa.Column(
            "session_data", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_session_id"), "session", ["id"], unique=False)
    op.create_index(
        op.f("ix_session_session_key"), "session", ["session_key"], unique=True
    )
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_index(op.f("ix_session_session_key"), table_name="session")
    op.drop_index(op.f("ix_session_id"), table_name="session")
    op.drop_table("session")
    op.drop_index(op.f("ix_user_id"), table_name="user")
    op.drop_index(op.f("ix_user_github_username"), table_name="user")
    op.drop_index(op.f("ix_user_github_id"), table_name="user")
    op.drop_table("user")
    # ### end Alembic commands ###
