"""SQLite access layer for the Expense Splitter app.

Uses plain sqlite3 (no ORM) with an app-factory-friendly connection helper.
In testing mode the database lives entirely in memory and is kept alive for
the whole app instance (a single shared connection), because Python's
sqlite3 module tears down an in-memory database as soon as its one and only
connection is closed, and Flask's test client makes several separate
requests against the same app during a test.
"""
from __future__ import annotations

import sqlite3

from flask import current_app, g

SCHEMA = """
CREATE TABLE IF NOT EXISTS groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id INTEGER NOT NULL REFERENCES groups(id),
    name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS expenses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id INTEGER NOT NULL REFERENCES groups(id),
    description TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    paid_by_member_id INTEGER NOT NULL REFERENCES members(id),
    split_method TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS expense_splits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    expense_id INTEGER NOT NULL REFERENCES expenses(id),
    member_id INTEGER NOT NULL REFERENCES members(id),
    share_cents INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS settlements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id INTEGER NOT NULL REFERENCES groups(id),
    from_member_id INTEGER NOT NULL REFERENCES members(id),
    to_member_id INTEGER NOT NULL REFERENCES members(id),
    amount_cents INTEGER NOT NULL,
    completed INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    completed_at TEXT
);
"""


def get_db():
    """Return a request-scoped sqlite3 connection (row_factory=Row)."""
    if "db" not in g:
        if current_app.config["DATABASE"] == ":memory:":
            conn = getattr(current_app, "_memory_conn", None)
            if conn is None:
                conn = sqlite3.connect(":memory:", check_same_thread=False)
                conn.row_factory = sqlite3.Row
                conn.executescript(SCHEMA)
                current_app._memory_conn = conn
            g.db = conn
        else:
            conn = sqlite3.connect(current_app.config["DATABASE"])
            conn.row_factory = sqlite3.Row
            g.db = conn
    return g.db


def close_db(e=None):
    db = g.pop("db", None)
    # Never close the shared in-memory connection -- closing it would wipe
    # out the whole database, and we reuse it across every request.
    if db is not None and current_app.config["DATABASE"] != ":memory:":
        db.close()


def init_db_file(app) -> None:
    """Make sure the schema exists in the on-disk database file."""
    conn = sqlite3.connect(app.config["DATABASE"])
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()


def init_app(app) -> None:
    app.teardown_appcontext(close_db)
    if app.config["DATABASE"] != ":memory:":
        init_db_file(app)
