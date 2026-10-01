"""
app/auth.py — Comptes de l'équipe.

Rôles :
  admin       tout, y compris Réglages et équipe
  validateur  relit et valide les déclinaisons (ex. les candidates)
  editeur     crée, prépare, programme (publication soumise à validation si activée)
"""

from __future__ import annotations

from functools import wraps

from flask import Blueprint, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from app.db import now_iso, store

bp = Blueprint("auth", __name__)

ROLES = {"admin": "Administrateur", "validateur": "Validateur", "editeur": "Éditeur"}


def load_user():
    uid = session.get("uid")
    g.user = store().query("SELECT * FROM users WHERE id=?", (uid,), one=True) if uid else None


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            if store().query("SELECT COUNT(*) AS n FROM users", one=True)["n"] == 0:
                return redirect(url_for("auth.setup"))
            return redirect(url_for("auth.login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def role_required(*roles):
    def deco(view):
        @wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            if g.user["role"] not in roles:
                flash("Action réservée : " + ", ".join(ROLES[r] for r in roles) + ".", "error")
                return redirect(request.referrer or url_for("main.dashboard"))
            return view(*args, **kwargs)

        return wrapped

    return deco


def create_user(email: str, name: str, password: str, role: str = "editeur") -> int:
    return store().execute(
        "INSERT INTO users(email, name, password_hash, role, created_at) VALUES(?,?,?,?,?)",
        (email.strip().lower(), name.strip(), generate_password_hash(password), role, now_iso()),
    )


@bp.route("/premier-lancement", methods=["GET", "POST"])
def setup():
    if store().query("SELECT COUNT(*) AS n FROM users", one=True)["n"] > 0:
        return redirect(url_for("auth.login"))
    if request.method == "POST":
        email, name, pwd = request.form.get("email", ""), request.form.get("name", ""), request.form.get("password", "")
        if not email or not name or len(pwd) < 8:
            flash("Renseignez nom, e-mail et un mot de passe d'au moins 8 caractères.", "error")
        else:
            session["uid"] = create_user(email, name, pwd, "admin")
            flash("Compte administrateur créé. Bienvenue !", "ok")
            return redirect(url_for("main.reglages"))
    return render_template("setup.html")


@bp.route("/connexion", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user = store().query("SELECT * FROM users WHERE email=?",
                             (request.form.get("email", "").strip().lower(),), one=True)
        if user and check_password_hash(user["password_hash"], request.form.get("password", "")):
            session.clear()
            session["uid"] = user["id"]
            session.permanent = True
            nxt = request.args.get("next", "")
            return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("main.dashboard"))
        flash("Identifiants incorrects.", "error")
    return render_template("login.html")


@bp.route("/deconnexion", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
