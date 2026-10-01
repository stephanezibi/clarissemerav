"""
Studio Réseaux — Surin / Griguer · Paris · Bâtonnat 2028.

Application web de community management de la campagne : création,
déclinaison automatique par réseau (formats, sous-titres, légendes, charte),
validation, programmation au meilleur créneau, publication et suivi des
performances (CSV pour l'outil de campagne).
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from flask import Flask, g

__version__ = "1.0.0"


def create_app(overrides: dict | None = None) -> Flask:
    from app import auth, db, views
    from app.config import Config

    app = Flask(__name__)
    app.config.from_object(Config)
    if overrides:
        app.config.update(overrides)
    app.permanent_session_lifetime = timedelta(days=30)
    app.config.setdefault("SESSION_COOKIE_SAMESITE", "Lax")
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    if str(app.config.get("PUBLIC_BASE_URL", "")).startswith("https://"):
        app.config["SESSION_COOKIE_SECURE"] = True

    for key in ("DATA_DIR", "UPLOAD_DIR", "OUTPUT_DIR"):
        os.makedirs(app.config[key], exist_ok=True)
    db.init_db(app.config["DATABASE"])
    app.extensions["store"] = db.Store(app.config["DATABASE"])
    app.teardown_appcontext(db.close_db)

    app.register_blueprint(auth.bp)
    app.register_blueprint(views.bp)
    app.before_request(auth.load_user)

    tz = ZoneInfo(app.config["TIMEZONE"])

    @app.template_filter("local")
    def local_dt(value, fmt="%d/%m/%Y %H:%M"):
        if not value:
            return ""
        try:
            dt = datetime.fromisoformat(str(value))
        except ValueError:
            return value
        if dt.tzinfo is None:
            return dt.strftime(fmt)
        return dt.astimezone(tz).strftime(fmt)

    @app.context_processor
    def inject():
        return {"current_user": g.get("user"), "version": __version__}

    if app.config.get("SCHEDULER_ENABLED") and not app.config.get("TESTING"):
        from app.publishing import start_scheduler

        start_scheduler(dict(app.config))
    return app
