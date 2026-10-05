"""
app/analytics.py — Performances : import CSV, export CSV, meilleurs créneaux.

Import : accepte les exports natifs (Meta Business Suite, LinkedIn, X,
TikTok…), en français ou en anglais, séparateur « ; » ou « , », UTF-8 ou
Latin-1. Les colonnes sont reconnues par synonymes ; chaque ligne est
rattachée à une publication de l'appli par son lien ou son identifiant.

Export : un CSV par publication (date, réseau, thème, lien, métriques, taux
d'engagement) prêt à charger dans l'outil de campagne (Excel FR : « ; », BOM).
"""

from __future__ import annotations

import csv
import io
import re
import unicodedata
from collections import defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.db import Store, now_iso
from app.networks import DEFAULT_SLOTS, NETWORKS

METRIC_COLUMNS = ["impressions", "reach", "likes", "comments", "shares", "saves",
                  "video_views", "clicks", "followers_gained"]

SYNONYMS = {
    "post_url": ["permalien", "permalink", "lien", "url", "post link", "lien de la publication",
                 "link", "url de la publication", "post url"],
    "external_id": ["id de la publication", "post id", "id", "identifiant", "media id", "tweet id"],
    "network": ["reseau", "network", "plateforme", "platform"],
    "published_at": ["heure de publication", "date de publication", "publish time", "date", "created",
                     "publication date", "posted", "date de creation", "time"],
    "label": ["titre", "description", "title", "texte", "post", "legende", "caption", "message"],
    "impressions": ["impressions", "vues", "affichages", "views", "impressions totales"],
    "reach": ["couverture", "reach", "portee", "comptes touches", "personnes touchees", "unique impressions"],
    "likes": ["j'aime", "jaime", "likes", "reactions", "mentions j'aime", "reactions totales", "reaction"],
    "comments": ["commentaires", "comments", "replies", "reponses"],
    "shares": ["partages", "shares", "repartages", "reposts", "retweets", "reposts/retweets"],
    "saves": ["enregistrements", "saves", "saved", "sauvegardes", "favoris"],
    "video_views": ["lectures", "video views", "vues de la video", "plays", "lectures de video", "vues video"],
    "clicks": ["clics", "clicks", "clics sur le lien", "link clicks", "clics totaux"],
    "followers_gained": ["abonnes gagnes", "followers gained", "nouveaux abonnes", "follows", "abonnements"],
}


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", s.replace("’", "'")).strip()


def _num(v) -> int:
    if v is None:
        return 0
    s = str(v).strip().replace(" ", "").replace("\xa0", "").replace(" ", "")
    if not s or s in {"-", "--", "N/A", "n/a"}:
        return 0
    s = s.rstrip("%")
    if "," in s and "." not in s:
        s = s.replace(",", ".")
    else:
        s = s.replace(",", "")
    try:
        return int(round(float(s)))
    except ValueError:
        return 0


def _decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-16", "cp1252", "latin-1"):
        try:
            text = raw.decode(enc)
            if "\x00" not in text:
                return text
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def map_columns(headers: list[str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    normed = {h: _norm(h) for h in headers}
    for field, syns in SYNONYMS.items():
        syns_n = [_norm(s) for s in syns]
        exact = next((h for h, n in normed.items() if n in syns_n and h not in mapping.values()), None)
        partial = exact or next(
            (h for h, n in normed.items() if any(s in n for s in syns_n if len(s) > 3) and h not in mapping.values()),
            None,
        )
        if partial:
            mapping[field] = partial
    return mapping


def _guess_network(url: str, default: str) -> str:
    u = (url or "").lower()
    if "instagram.com" in u:
        return "instagram_reel" if "/reel" in u else "instagram_feed"
    if "facebook.com" in u or "fb.watch" in u:
        return "facebook"
    if "linkedin.com" in u:
        return "linkedin"
    if "x.com" in u or "twitter.com" in u:
        return "x"
    if "tiktok.com" in u:
        return "tiktok"
    if "youtube.com" in u or "youtu.be" in u:
        return "youtube_shorts"
    if "threads.net" in u or "threads.com" in u:
        return "threads"
    return default


def _parse_date(s: str) -> str | None:
    s = (s or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
                "%d/%m/%Y %H:%M", "%d/%m/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(s[:25], fmt).isoformat()
        except ValueError:
            continue
    return None


def import_csv(store: Store, raw: bytes, default_network: str = "") -> dict:
    text = _decode(raw)
    sample = text[:5000]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,\t")
    except csv.Error:
        dialect = csv.excel
        dialect.delimiter = ";" if sample.count(";") > sample.count(",") else ","
    rows = list(csv.DictReader(io.StringIO(text), dialect=dialect))
    if not rows:
        return {"imported": 0, "matched": 0, "mapping": {}, "skipped": 0}
    mapping = map_columns(list(rows[0].keys()))
    imported = matched = skipped = 0
    for row in rows:
        def get(field):
            col = mapping.get(field)
            return row.get(col, "") if col else ""

        url = get("post_url").strip()
        ext = get("external_id").strip()
        metrics = {m: _num(get(m)) for m in METRIC_COLUMNS}
        if not url and not ext and not any(metrics.values()):
            skipped += 1
            continue
        net_raw = _norm(get("network"))
        network = next((k for k in NETWORKS if net_raw and (net_raw in k or k.split("_")[0] in net_raw)), "")
        network = network or _guess_network(url, default_network or "inconnu")
        variant = None
        if url or ext:
            variant = store.query(
                "SELECT id, network FROM variants WHERE (external_url<>'' AND external_url=?) "
                "OR (external_id<>'' AND (external_id=? OR external_id=?))",
                (url, ext, ext or "__"), one=True,
            )
            if variant is None and url:
                tail = url.rstrip("/").rsplit("/", 1)[-1]
                if len(tail) > 5:
                    variant = store.query("SELECT id, network FROM variants WHERE external_url LIKE ?",
                                          (f"%{tail}%",), one=True)
        if variant:
            matched += 1
            network = variant["network"]
        store.execute(
            "INSERT INTO metrics(variant_id, network, post_url, external_id, published_at, "
            + ", ".join(METRIC_COLUMNS) + ", source, label, collected_at) VALUES(?,?,?,?,?,"
            + ",".join("?" * len(METRIC_COLUMNS)) + ",'csv',?,?)",
            (variant["id"] if variant else None, network, url, ext, _parse_date(get("published_at")),
             *[metrics[m] for m in METRIC_COLUMNS], get("label")[:300], now_iso()),
        )
        imported += 1
    return {"imported": imported, "matched": matched, "mapping": mapping, "skipped": skipped}


def latest_metrics_sql() -> str:
    """Dernière mesure par publication (variant ou lien)."""
    return """
    SELECT m.* FROM metrics m
    JOIN (SELECT MAX(id) AS id FROM metrics
          GROUP BY COALESCE(CAST(variant_id AS TEXT), NULLIF(post_url,''), NULLIF(external_id,''), CAST(id AS TEXT))) last
      ON last.id = m.id
    """


def engagement(m) -> float:
    inter = (m["likes"] or 0) + (m["comments"] or 0) + (m["shares"] or 0) + (m["saves"] or 0)
    base = m["reach"] or m["impressions"] or 0
    return round(100.0 * inter / base, 2) if base else 0.0


def performance_rows(store: Store) -> list[dict]:
    rows = store.query(f"""
        SELECT lm.*, v.caption, v.published_at AS v_published, v.external_url, v.network AS v_network,
               p.title, p.pillar, p.kind, u.name AS author
        FROM ({latest_metrics_sql()}) lm
        LEFT JOIN variants v ON v.id = lm.variant_id
        LEFT JOIN posts p ON p.id = v.post_id
        LEFT JOIN users u ON u.id = p.created_by
        ORDER BY COALESCE(v.published_at, lm.published_at) DESC
    """)
    out = []
    for r in rows:
        d = dict(r)
        d["published"] = r["v_published"] or r["published_at"] or ""
        d["url"] = r["external_url"] or r["post_url"] or ""
        d["engagement"] = engagement(r)
        d["titre"] = r["title"] or r["label"] or ""
        out.append(d)
    return out


def export_csv(store: Store) -> bytes:
    rows = performance_rows(store)
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["date_publication", "reseau", "type_contenu", "theme", "titre", "lien", "auteur",
                "impressions", "couverture", "jaime", "commentaires", "partages", "enregistrements",
                "vues_video", "clics", "abonnes_gagnes", "taux_engagement_pct", "source", "releve_le"])
    for r in rows:
        w.writerow([r["published"], NETWORKS.get(r["network"], {}).get("label", r["network"]),
                    r.get("kind") or "", r.get("pillar") or "", r["titre"], r["url"], r.get("author") or "",
                    r["impressions"], r["reach"], r["likes"], r["comments"], r["shares"], r["saves"],
                    r["video_views"], r["clicks"], r["followers_gained"],
                    str(r["engagement"]).replace(".", ","), r["source"], r["collected_at"]])
    return ("﻿" + buf.getvalue()).encode("utf-8")


def export_calendar_csv(store: Store) -> bytes:
    rows = store.query("""
        SELECT v.*, p.title AS post_title, p.pillar, p.kind FROM variants v JOIN posts p ON p.id=v.post_id
        ORDER BY COALESCE(v.published_at, v.scheduled_at) DESC""")
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["id", "titre", "theme", "type", "reseau", "statut", "programme_le", "publie_le", "lien",
                "legende", "hashtags", "collaborateurs"])
    for r in rows:
        w.writerow([r["id"], r["post_title"], r["pillar"], r["kind"], r["network"], r["status"],
                    r["scheduled_at"] or "", r["published_at"] or "", r["external_url"],
                    r["caption"], r["hashtags"], r["collaborators"]])
    return ("﻿" + buf.getvalue()).encode("utf-8")


# --------------------------------------------------------------------- #
# Meilleurs créneaux
# --------------------------------------------------------------------- #

def learned_slots(store: Store, network: str, tz: str, min_samples: int = 3) -> dict[tuple[int, int], float]:
    """Engagement moyen par (jour, heure) sur l'historique du réseau."""
    zone = ZoneInfo(tz)
    buckets: dict[tuple[int, int], list[float]] = defaultdict(list)
    for r in performance_rows(store):
        if r["network"] != network or not r["published"]:
            continue
        try:
            dt = datetime.fromisoformat(r["published"])
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=zone)
        dt = dt.astimezone(zone)
        buckets[(dt.weekday(), dt.hour)].append(r["engagement"])
    return {k: sum(v) / len(v) for k, v in buckets.items() if len(v) >= min_samples}


def suggest_slots(store: Store, network: str, tz: str, count: int = 4, after: datetime | None = None) -> list[dict]:
    """Prochains créneaux conseillés : appris si assez d'historique, sinon par défaut.
    Évite les créneaux déjà pris sur ce réseau (± 2 h)."""
    zone = ZoneInfo(tz)
    now = (after or datetime.now(zone)).astimezone(zone)
    learned = learned_slots(store, network, tz)
    defaults = DEFAULT_SLOTS.get(network, {d: [12, 18] for d in range(7)})
    taken = [
        datetime.fromisoformat(r["scheduled_at"]).astimezone(zone)
        for r in store.query("SELECT scheduled_at FROM variants WHERE network=? AND status='programme' "
                             "AND scheduled_at IS NOT NULL", (network,))
    ]
    candidates = []
    for day in range(0, 8):
        d = (now + timedelta(days=day)).date()
        hours = set(defaults.get(d.weekday(), []))
        hours |= {h for (wd, h) in learned if wd == d.weekday()}
        for h in sorted(hours):
            dt = datetime(d.year, d.month, d.day, h, 0, tzinfo=zone)
            if dt <= now + timedelta(minutes=20):
                continue
            if any(abs((dt - t).total_seconds()) < 7200 for t in taken):
                continue
            score = learned.get((d.weekday(), h))
            candidates.append({
                "dt": dt, "iso": dt.isoformat(), "local": dt.strftime("%Y-%m-%dT%H:%M"),
                "label": _fr_label(dt), "source": "historique" if score is not None else "conseil",
                "score": score or 0.0,
            })
    learned_first = sorted([c for c in candidates if c["source"] == "historique"],
                           key=lambda c: (-c["score"], c["dt"]))[:2]
    rest = [c for c in candidates if c not in learned_first]
    return (learned_first + rest)[:count]


JOURS = ["lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."]


def _fr_label(dt: datetime) -> str:
    return f"{JOURS[dt.weekday()]} {dt.day:02d}/{dt.month:02d} à {dt.hour}h{dt.minute:02d}"


def heatmap(store: Store, tz: str) -> dict:
    zone = ZoneInfo(tz)
    grid: dict[tuple[int, int], list[float]] = defaultdict(list)
    for r in performance_rows(store):
        if not r["published"]:
            continue
        try:
            dt = datetime.fromisoformat(r["published"])
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=zone)
        dt = dt.astimezone(zone)
        grid[(dt.weekday(), dt.hour // 3)].append(r["engagement"])
    cells = {k: round(sum(v) / len(v), 1) for k, v in grid.items()}
    mx = max(cells.values(), default=0) or 1
    return {"cells": cells, "max": mx, "counts": {k: len(v) for k, v in grid.items()}}
