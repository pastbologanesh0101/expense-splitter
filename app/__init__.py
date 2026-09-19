"""Expense Splitter: a Splitwise-style group expense app.

Flask app-factory module. Use ``create_app(testing=True)`` to get an app
backed by an in-memory SQLite database, ideal for unit tests.
"""
from __future__ import annotations

import os

from flask import Flask

from . import db as db_module


def create_app(testing: bool = False, database: str | None = None) -> Flask:
    app = Flask(__name__, instance_relative_config=True)

    if testing:
        app.config.update(TESTING=True, DATABASE=":memory:")
    else:
        os.makedirs(app.instance_path, exist_ok=True)
        app.config.update(
            DATABASE=database
            or os.path.join(app.instance_path, "expense_splitter.db")
        )

    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-key")

    db_module.init_app(app)

    from . import routes

    app.register_blueprint(routes.bp)

    return app
