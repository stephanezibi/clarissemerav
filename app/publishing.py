"""
app/publishing.py — Publication d'une déclinaison + planificateur.

Le planificateur (APScheduler) tourne dans le serveur :
  - chaque minute : publie les déclinaisons programmées arrivées à échéance
    (ou notifie l'équipe si le réseau est en mode assisté) ;
  - chaque matin : remonte les statistiques Instagram / Facebook des
    publications des 30 derniers jours.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from app.db import Store, now_iso
from app.networks import NETWORKS
from app.publishers import PublishContext, PublishError, compose_text, connector_for, split_handles

log = logging.getLogger(__name__)


def public_url(config: dict, asset) -> str:
    base = config.get("PUBLIC_BASE_URL", "")
    return f"{base}/m/{asset['token']}/{Path(asset['path']).name}" if base else ""


def variant_assets(store: Store, variant) -> list:
    rows = store.query(
        "SELECT * FROM assets WHERE post_id=? AND role='output' AND network=? ORDER BY position",
        (variant["post_id"], variant["network"]),
    )
    if variant["network"] == "instagram_story":
        rows = rows[:1]
    return rows


def notify(config: dict, text: str) -> None:
    url = config.get("NOTIFY_WEBHOOK_URL")
    if not url:
        return
    try:
        requests.post(url, json={"text": text}, timeout=10)
    except requests.RequestException:
        log.warning("Notification webhook impossible.")


def publish_variant(config: dict, store: Store, variant_id: int, user_id=None) -> dict:
    v = store.query("SELECT * FROM variants WHERE id=?", (variant_id,), one=True)
    if v is None:
        raise PublishError("Déclinaison introuvable.")
    post = store.query("SELECT * FROM posts WHERE id=?", (v["post_id"],), one=True)
    net = v["network"]
    link = f"{config.get('PUBLIC_BASE_URL', '')}/contenus/{post['id']}#v{v['id']}"
    connector = connector_for(net, config)
    if connector is None:
        store.execute("UPDATE variants SET status='a_poster', error='' WHERE id=?", (variant_id,))
        notify(config, f"⏰ C'est l'heure de poster « {post['title']} » sur {NETWORKS[net]['label']} : {link}")
        store.log(f"À poster manuellement : « {post['title']} » sur {NETWORKS[net]['short']}", user_id)
        return {"mode": "assiste"}

    assets = variant_assets(store, v)
    needs_url = net.startswith("instagram") or net == "facebook"
    if needs_url and not config.get("PUBLIC_BASE_URL"):
        raise PublishError("PUBLIC_BASE_URL n'est pas défini : Meta ne peut pas récupérer les médias.")
    ctx = PublishContext(
        config=config,
        caption=compose_text(v["caption"], v["hashtags"], net),
        first_comment=v["first_comment"] or "",
        alt_text=v["alt_text"] or "",
        title=v["title"] or post["title"],
        collaborators=split_handles(v["collaborators"]),
        user_tags=split_handles(v["user_tags"]),
        media=[{"path": a["path"], "url": public_url(config, a), "mime": a["mime"]} for a in assets],
        kind=post["kind"],
    )
    try:
        ext_id, url = connector(net, ctx)
    except PublishError as exc:
        store.execute("UPDATE variants SET status='echec', error=? WHERE id=?", (str(exc)[:1000], variant_id))
        notify(config, f"⚠️ Échec de publication « {post['title']} » sur {NETWORKS[net]['label']} : {exc} — {link}")
        store.log(f"Échec de publication sur {NETWORKS[net]['short']} : {exc}", user_id)
        raise
    store.execute(
        "UPDATE variants SET status='publie', external_id=?, external_url=?, published_at=?, error='' WHERE id=?",
        (ext_id, url, now_iso(), variant_id),
    )
    store.log(f"Publié : « {post['title']} » sur {NETWORKS[net]['short']}", user_id)
    return {"mode": "auto", "url": url}


def mark_published(store: Store, variant_id: int, url: str, user_id=None) -> None:
    store.execute(
        "UPDATE variants SET status='publie', external_url=?, published_at=?, error='' WHERE id=?",
        (url.strip(), now_iso(), variant_id),
    )
    store.log(f"Marqué publié (manuel) : déclinaison {variant_id}", user_id)


# --------------------------------------------------------------------- #
# Tâches planifiées
# --------------------------------------------------------------------- #

def run_due(config: dict) -> int:
    store = Store(config["DATABASE"])
    now = datetime.now(timezone.utc).isoformat()
    due = store.query(
        "SELECT id FROM variants WHERE status='programme' AND scheduled_at IS NOT NULL AND scheduled_at<=?",
        (now,),
    )
    count = 0
    for row in due:
        # verrou : une seule instance publie (si plusieurs processus tournent)
        if not store.claim("UPDATE variants SET status='publication' WHERE id=? AND status='programme'",
                           (row["id"],)):
            continue
        try:
            publish_variant(config, store, row["id"])
            count += 1
        except PublishError:
            pass
        except Exception as exc:  # garde le planificateur en vie
            store.execute("UPDATE variants SET status='echec', error=? WHERE id=?", (str(exc)[:500], row["id"]))
    return count


def refresh_insights(config: dict) -> int:
    from app.publishers import meta

    store = Store(config["DATABASE"])
    since = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    rows = store.query(
        "SELECT * FROM variants WHERE status='publie' AND external_id<>'' AND published_at>=?", (since,))
    n = 0
    for v in rows:
        try:
            if v["network"].startswith("instagram") and config.get("META_ACCESS_TOKEN"):
                data = meta.instagram_insights(config, v["external_id"], v["network"])
            elif v["network"] == "facebook" and config.get("FACEBOOK_PAGE_ID"):
                data = meta.facebook_insights(config, v["external_id"])
            else:
                continue
        except PublishError as exc:
            log.warning("Statistiques indisponibles pour %s : %s", v["id"], exc)
            continue
        store.execute(
            "INSERT INTO metrics(variant_id, network, post_url, external_id, published_at, impressions, reach, "
            "likes, comments, shares, saves, video_views, clicks, source, collected_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?, 'api', ?)",
            (v["id"], v["network"], data.get("post_url") or v["external_url"], v["external_id"], v["published_at"],
             data.get("impressions", 0), data.get("reach", 0), data.get("likes", 0), data.get("comments", 0),
             data.get("shares", 0), data.get("saves", 0), data.get("video_views", 0), data.get("clicks", 0),
             now_iso()),
        )
        n += 1
    return n


_scheduler = None


def start_scheduler(config: dict) -> None:
    global _scheduler
    if _scheduler is not None:
        return
    from apscheduler.schedulers.background import BackgroundScheduler

    _scheduler = BackgroundScheduler(timezone=config.get("TIMEZONE", "Europe/Paris"))
    _scheduler.add_job(run_due, "interval", minutes=1, args=[config], id="publication",
                       max_instances=1, coalesce=True)
    _scheduler.add_job(refresh_insights, "cron", hour=7, minute=10, args=[config], id="statistiques",
                       max_instances=1, coalesce=True)
    _scheduler.start()
