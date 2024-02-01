"""Database migrations"""

import os
import sqlite3

from simplebot.bot import DeltaBot

from .util import get_database_path

DATABASE_VERSION = 1


def get_db_version(database: sqlite3.Connection) -> int:
    with database:
        database.execute(
            """CREATE TABLE IF NOT EXISTS "database" (
            "id" INTEGER NOT NULL,
	    "version" INTEGER NOT NULL,
	    PRIMARY KEY("id")
            )"""
        )
    row = database.execute("SELECT version FROM database").fetchone()
    return row["version"] if row else 0


def run_migrations(bot: DeltaBot) -> None:
    path = get_database_path(bot)
    if not os.path.exists(path):
        bot.logger.debug("Database doesn't exists, skipping migrations")
        return

    database = sqlite3.connect(path)
    database.row_factory = sqlite3.Row
    try:
        version = get_db_version(database)
        bot.logger.debug(f"Current database version: v{version}")
        for i in range(version + 1, DATABASE_VERSION + 1):
            migration = globals().get(f"migrate{i}")
            assert migration
            bot.logger.info(f"Migrating database: v{i}")
            with database:
                database.execute("REPLACE INTO database VALUES (?,?)", (1, i))
                migration(database)
    finally:
        database.close()


def migrate1(database: sqlite3.Connection) -> None:
    database.execute("ALTER TABLE account ADD COLUMN  muted_home BOOLEAN")
