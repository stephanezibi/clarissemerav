"""
app/config.py — Configuration lue depuis l'environnement (.env).

Tout ce qui est secret (clé Claude, jetons des réseaux) vit dans les variables
d'environnement du serveur ; les réglages éditoriaux (charte, contexte de
campagne, comptes) sont modifiables depuis la page Réglages et stockés en base.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # python-dotenv est optionnel
    pass

BASE_DIR = Path(__file__).resolve().parent.parent


class Config:
    DATA_DIR = Path(os.environ.get("DATA_DIR", BASE_DIR / "data")).resolve()
    DATABASE = DATA_DIR / "campagne.db"
    UPLOAD_DIR = DATA_DIR / "uploads"
    OUTPUT_DIR = DATA_DIR / "outputs"

    SECRET_KEY = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
    MAX_CONTENT_LENGTH = int(os.environ.get("MAX_UPLOAD_MB", "1024")) * 1024 * 1024

    # URL publique de l'appli (obligatoire pour la publication Instagram /
    # Facebook : Meta va chercher les médias à cette adresse).
    # (sur Render, l'adresse du service est fournie automatiquement)
    PUBLIC_BASE_URL = (os.environ.get("PUBLIC_BASE_URL") or os.environ.get("RENDER_EXTERNAL_URL", "")).rstrip("/")
    TIMEZONE = os.environ.get("TIMEZONE", "Europe/Paris")

    # Claude
    ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
    CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-opus-5-5")
    CLAUDE_EFFORT = os.environ.get("CLAUDE_EFFORT", "medium")

    # Transcription (sous-titres) — faster-whisper, en local sur le serveur
    WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "small")

    # Connecteurs réseaux sociaux (laisser vide = mode « publication assistée »)
    META_ACCESS_TOKEN = os.environ.get("META_ACCESS_TOKEN", "")
    META_GRAPH_VERSION = os.environ.get("META_GRAPH_VERSION", "v23.0")
    INSTAGRAM_USER_ID = os.environ.get("INSTAGRAM_USER_ID", "")
    FACEBOOK_PAGE_ID = os.environ.get("FACEBOOK_PAGE_ID", "")
    FACEBOOK_PAGE_TOKEN = os.environ.get("FACEBOOK_PAGE_TOKEN", "")
    LINKEDIN_ACCESS_TOKEN = os.environ.get("LINKEDIN_ACCESS_TOKEN", "")
    LINKEDIN_AUTHOR_URN = os.environ.get("LINKEDIN_AUTHOR_URN", "")
    LINKEDIN_VERSION = os.environ.get("LINKEDIN_VERSION", "202509")
    X_USER_ACCESS_TOKEN = os.environ.get("X_USER_ACCESS_TOKEN", "")

    # Notifications « c'est l'heure de poster » (Slack, Teams, Google Chat…)
    NOTIFY_WEBHOOK_URL = os.environ.get("NOTIFY_WEBHOOK_URL", "")

    SCHEDULER_ENABLED = os.environ.get("SCHEDULER_ENABLED", "1") == "1"
