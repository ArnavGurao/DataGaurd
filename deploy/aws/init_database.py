"""One-time RDS bootstrap. The deployer supplies the admin secret over stdin.

The instance role never receives Secrets Manager admin-secret permissions.
Credentials are not placed in SSM command text, process arguments, or logs.
"""
import json
import sys

import psycopg
from psycopg import sql

from app.config import get_settings


def main():
    admin = json.load(sys.stdin)
    settings = get_settings()
    with psycopg.connect(
        host=settings.db_host, port=settings.db_port, dbname=settings.db_name,
        user=admin["username"], password=admin["password"], sslmode="require",
        autocommit=True,
    ) as connection:
        exists = connection.execute(
            "SELECT 1 FROM pg_roles WHERE rolname = %s", (settings.db_user,)
        ).fetchone()
        if not exists:
            connection.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                sql.Identifier(settings.db_user), sql.Literal(settings.db_password)
            ))
        else:
            connection.execute(sql.SQL("ALTER ROLE {} PASSWORD {}").format(
                sql.Identifier(settings.db_user), sql.Literal(settings.db_password)
            ))
        connection.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
            sql.Identifier(settings.db_name), sql.Identifier(settings.db_user)
        ))
        connection.execute(sql.SQL("GRANT USAGE, CREATE ON SCHEMA public TO {}").format(
            sql.Identifier(settings.db_user)
        ))
    print("Application database role is ready.")


if __name__ == "__main__":
    main()
