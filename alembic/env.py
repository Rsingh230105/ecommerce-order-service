from logging.config import fileConfig

from sqlalchemy import create_engine
from sqlalchemy import pool

from alembic import context
from dotenv import load_dotenv

from app.database import Base, DATABASE_URL
from app import models

# Load environment variables from .env
load_dotenv()

# Alembic Config object
config = context.config

# Configure Python logging from alembic.ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# SQLAlchemy metadata used for autogenerate
target_metadata = Base.metadata


# ============================================================
# IMPORTANT
# ============================================================
# Product Service and Order Service use the same PostgreSQL
# database, so they must NOT share the same Alembic version table.
#
# Product Service:
#     alembic_version
#
# Order Service:
#     order_alembic_version
#
# This keeps both services' migration histories independent.
# ============================================================

ORDER_VERSION_TABLE = "order_alembic_version"


def run_migrations_offline() -> None:
    """
    Run migrations in offline mode.

    No database connection is created.
    Alembic generates SQL using the database URL.
    """

    # Use the same DATABASE_URL used by the application.
    url = DATABASE_URL

    context.configure(
        url=url,
        target_metadata=target_metadata,

        # IMPORTANT:
        # Use a separate Alembic version table for Order Service.
        version_table=ORDER_VERSION_TABLE,

        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """
    Run migrations in online mode.

    A real database connection is created and migrations
    are executed directly against PostgreSQL.
    """

    # Create SQLAlchemy engine using the application's
    # PostgreSQL DATABASE_URL.
    connectable = create_engine(
        DATABASE_URL,
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:

        context.configure(
            connection=connection,
            target_metadata=target_metadata,

            # IMPORTANT:
            # Order Service gets its own migration history table.
            version_table=ORDER_VERSION_TABLE,
        )

        with context.begin_transaction():
            context.run_migrations()


# ============================================================
# Alembic Entry Point
# ============================================================

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()