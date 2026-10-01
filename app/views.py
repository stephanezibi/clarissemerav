"""
app/views.py — Pages de l'application.

  /                 Tableau de bord : à poster maintenant, à valider, prochains créneaux
  /nouveau          Création : fichiers, réseaux, options vidéo, brief
  /contenus/<id>    Déclinaisons par réseau : aperçu, légende, tags, créneau, publication
  /calendrier       Planning de la semaine
  /performances     Import / export CSV, tableau, carte des créneaux
  /reglages         Charte, contexte de campagne, connecteurs, équipe
"""

from __future__ import annotations

import io
import json
import secrets
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import (Blueprint, abort, current_app, flash, g, redirect, render_template, request,
                   send_file, session, url_for)
from werkzeug.utils import secure_filename

from app import analytics, media, pipeline, transcribe
from app.auth import ROLES, create_user, login_required, role_required
from app.brand import DEFAULT_BRAND, DEFAULT_CAMPAIGN
from app.db import now_iso, store
from app.networks import FORMATS, NETWORKS, label, networks_for_kind, target_format
from app.publishers import PublishError, compose_text, connected_networks
from app.publishing import mark_published, publish_variant

bp = Blueprint("main", __name__)

STATUTS = {
    "a_relire": "À relire",
    "valide": "Validé",
    "programme": "Programmé",
    "publication": "Publication…",
    "a_poster": "À poster maintenant",
    "publie": "Publié",
    "echec": "Échec",
}


# --------------------------------------------------------------------- #
# Sécurité : jeton CSRF sur tous les formulaires
# --------------------------------------------------------------------- #

def csrf_token() -> str:
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(24)
    return session["csrf"]


@bp.app_context_processor
def _ctx():
    return {"csrf_token": csrf_token, "STATUTS": STATUTS, "NETWORKS": NETWORKS, "label": label}


@bp.before_app_request
def _csrf_protect():
    if request.method == "POST" and not current_app.config.get("TESTING"):
        sent = request.form.get("csrf") or request.headers.get("X-CSRF")
        if not sent or sent != session.get("csrf"):
            abort(400, "Jeton de sécurité manquant ou expiré : rechargez la page.")


def _tz() -> ZoneInfo:
    return ZoneInfo(current_app.config["TIMEZONE"])


def _cfg() -> dict:
    return dict(current_app.config)


def _require_validation() -> bool:
    return bool(store().get_setting("validation_obligatoire", True))


# --------------------------------------------------------------------- #
# Tableau de bord
# --------------------------------------------------------------------- #

@bp.route("/")
@login_required
def dashboard():
    s = store()
    a_poster = s.query("""SELECT v.*, p.title FROM variants v JOIN posts p ON p.id=v.post_id
                          WHERE v.status IN ('a_poster','echec') ORDER BY v.scheduled_at""")
    a_valider = s.query("""SELECT v.*, p.title FROM variants v JOIN posts p ON p.id=v.post_id
                           WHERE v.status='a_relire' AND p.status='pret' ORDER BY p.created_at DESC LIMIT 20""")
    prochains = s.query("""SELECT v.*, p.title FROM variants v JOIN posts p ON p.id=v.post_id
                           WHERE v.status='programme' ORDER BY v.scheduled_at LIMIT 12""")
    en_cours = s.query("SELECT * FROM posts WHERE status IN ('traitement','erreur') ORDER BY updated_at DESC")
    recents = s.query("""SELECT v.*, p.title FROM variants v JOIN posts p ON p.id=v.post_id
                         WHERE v.status='publie' ORDER BY v.published_at DESC LIMIT 8""")
    activite = s.query("""SELECT a.*, u.name FROM activity a LEFT JOIN users u ON u.id=a.user_id
                          ORDER BY a.id DESC LIMIT 12""")
    week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    stats = {
        "publies_7j": s.query("SELECT COUNT(*) n FROM variants WHERE status='publie' AND published_at>=?",
                              (week_ago,), one=True)["n"],
        "programmes": s.query("SELECT COUNT(*) n FROM variants WHERE status='programme'", one=True)["n"],
        "a_valider": len(a_valider),
    }
    perf = analytics.performance_rows(s)
    stats["engagement_moyen"] = round(sum(r["engagement"] for r in perf) / len(perf), 2) if perf else None
    return render_template("dashboard.html", active="dashboard", a_poster=a_poster, a_valider=a_valider,
                           prochains=prochains, en_cours=en_cours, recents=recents, activite=activite,
                           stats=stats)


# --------------------------------------------------------------------- #
# Création
# --------------------------------------------------------------------- #

@bp.route("/nouveau", methods=["GET", "POST"])
@login_required
def nouveau():
    campaign = pipeline.get_campaign(store())
    piliers = [p.strip() for p in campaign.get("piliers", "").split(",") if p.strip()]
    if request.method == "GET":
        return render_template("nouveau.html", active="nouveau", piliers=piliers,
                               connected=connected_networks(_cfg()),
                               whisper=transcribe.available(),
                               kinds={k: list(networks_for_kind(k)) for k in ("photo", "video", "texte")})
    f = request.form
    kind = f.get("kind", "photo")
    networks = [n for n in f.getlist("networks") if n in NETWORKS and kind in NETWORKS[n]["supports"]]
    files = [fs for fs in request.files.getlist("files") if fs and fs.filename]
    title = f.get("title", "").strip() or "Sans titre"
    if not networks:
        flash("Choisissez au moins un réseau compatible avec ce type de contenu.", "error")
        return redirect(url_for("main.nouveau"))
    if kind in ("photo", "video") and not files:
        flash("Ajoutez au moins un fichier.", "error")
        return redirect(url_for("main.nouveau"))
    if kind == "texte" and not f.get("source_text", "").strip():
        flash("Collez le texte à transformer en carrousel.", "error")
        return redirect(url_for("main.nouveau"))

    def _num(name):
        try:
            return float(str(f.get(name, "")).replace(",", ".")) if f.get(name) else None
        except ValueError:
            return None

    opts = {
        "recadrage": f.get("recadrage", "flou"),
        "signature": f.get("signature") == "1",
        "collaborators": f.get("collaborators", "").strip(),
        "user_tags": f.get("user_tags", "").strip(),
        "nb_slides": int(f.get("nb_slides") or 8),
    }
    if kind == "video":
        opts.update({
            "montage": f.get("montage", "aucun"),
            "debut": _parse_tc(f.get("debut", "")),
            "fin": _parse_tc(f.get("fin", "")),
            "duree_cible": int(f.get("duree_cible") or 45),
            "sous_titres": f.get("sous_titres") == "1",
        })
    s = store()
    post_id = s.execute(
        "INSERT INTO posts(title, kind, pillar, source_text, brief, networks, options, status, created_by, "
        "created_at, updated_at) VALUES(?,?,?,?,?,?,?, 'traitement', ?, ?, ?)",
        (title, kind, f.get("pillar", ""), f.get("source_text", ""), f.get("brief", ""),
         json.dumps(networks), json.dumps(opts, ensure_ascii=False), g.user["id"], now_iso(), now_iso()),
    )
    up_dir = Path(current_app.config["UPLOAD_DIR"]) / f"post_{post_id}"
    up_dir.mkdir(parents=True, exist_ok=True)
    for i, fs in enumerate(files):
        name = f"{i:02d}_{secure_filename(fs.filename) or 'fichier'}"
        path = up_dir / name
        fs.save(path)
        pipeline.add_asset(s, post_id, "source", path, position=i)
    s.log(f"Nouveau contenu « {title} » ({kind}) → {', '.join(NETWORKS[n]['short'] for n in networks)}",
          g.user["id"])
    pipeline.submit(current_app.config, post_id)
    return redirect(url_for("main.contenu", post_id=post_id))


def _parse_tc(value: str):
    """« 1:23 », « 83 », « 00:01:23.5 » → secondes."""
    value = (value or "").strip().replace(",", ".")
    if not value:
        return None
    try:
        parts = [float(p) for p in value.split(":")]
    except ValueError:
        return None
    sec = 0.0
    for p in parts:
        sec = sec * 60 + p
    return sec


# --------------------------------------------------------------------- #
# Contenus
# --------------------------------------------------------------------- #

@bp.route("/contenus")
@login_required
def contenus():
    rows = store().query("""
        SELECT p.*, u.name AS author,
          (SELECT GROUP_CONCAT(network || ':' || status) FROM variants v WHERE v.post_id=p.id) AS vstat,
          (SELECT id FROM assets a WHERE a.post_id=p.id AND a.role='output' AND a.mime='image/jpeg'
             ORDER BY (a.fmt='apercu') DESC, position LIMIT 1) AS thumb
        FROM posts p LEFT JOIN users u ON u.id=p.created_by ORDER BY p.created_at DESC LIMIT 200""")
    return render_template("contenus.html", active="contenus", rows=rows)


def _load_post(post_id: int):
    post = store().query("SELECT p.*, u.name AS author FROM posts p LEFT JOIN users u ON u.id=p.created_by "
                         "WHERE p.id=?", (post_id,), one=True)
    if post is None:
        abort(404)
    return post


@bp.route("/contenus/<int:post_id>")
@login_required
def contenu(post_id):
    s = store()
    post = _load_post(post_id)
    opts = json.loads(post["options"] or "{}")
    variants = s.query("SELECT * FROM variants WHERE post_id=? ORDER BY id", (post_id,))
    assets = s.query("SELECT * FROM assets WHERE post_id=? ORDER BY role, network, position", (post_id,))
    by_net: dict[str, list] = {}
    for a in assets:
        if a["role"] == "output" and a["network"]:
            by_net.setdefault(a["network"], []).append(a)
    extras = [a for a in assets if a["role"] == "output" and not a["network"]]
    sources = [a for a in assets if a["role"] == "source"]
    tz = current_app.config["TIMEZONE"]
    slots = {v["network"]: analytics.suggest_slots(s, v["network"], tz) for v in variants}
    connected = connected_networks(_cfg())
    previews = {v["id"]: compose_text(v["caption"], v["hashtags"], v["network"]) for v in variants}
    return render_template("contenu.html", active="contenus", post=post, opts=opts, variants=variants,
                           by_net=by_net, extras=extras, sources=sources, slots=slots, connected=connected,
                           previews=previews, require_validation=_require_validation(),
                           can_validate=g.user["role"] in ("admin", "validateur"))


@bp.route("/contenus/<int:post_id>/statut")
@login_required
def contenu_statut(post_id):
    post = _load_post(post_id)
    if post["status"] != "traitement":
        resp = current_app.response_class("")
        resp.headers["HX-Refresh"] = "true"
        return resp
    return render_template("_statut.html", post=post)


@bp.route("/contenus/<int:post_id>/regenerer", methods=["POST"])
@login_required
def regenerer(post_id):
    """Relance : légendes seules (rapide) ou traitement complet (médias inclus)."""
    _load_post(post_id)
    full = request.form.get("complet") == "1"
    s = store()
    if request.form.get("brief") is not None:
        s.execute("UPDATE posts SET brief=? WHERE id=?", (request.form.get("brief", ""), post_id))
    if full:
        s.execute("UPDATE posts SET status='traitement', updated_at=? WHERE id=?", (now_iso(), post_id))
        pipeline.submit(current_app.config, post_id)
    else:
        s.execute("UPDATE posts SET status='traitement', updated_at=? WHERE id=?", (now_iso(), post_id))
        cfg = dict(current_app.config)
        pipeline._executor.submit(_regen_captions, cfg, post_id)
    return redirect(url_for("main.contenu", post_id=post_id))


def _regen_captions(cfg: dict, post_id: int):
    from app.db import Store

    st = Store(cfg["DATABASE"])
    try:
        pipeline.process_post(cfg, st, post_id, regenerate_media=False)
    except Exception as exc:
        st.execute("UPDATE posts SET status='erreur', error=? WHERE id=?", (str(exc)[:1000], post_id))


@bp.route("/contenus/<int:post_id>/diapos", methods=["POST"])
@login_required
def diapos(post_id):
    """Édition des diapositives du carrousel puis nouveau rendu."""
    post = _load_post(post_id)
    opts = json.loads(post["options"] or "{}")
    slides = []
    n = int(request.form.get("count", 0))
    for i in range(n):
        if request.form.get(f"del_{i}") == "1":
            continue
        slides.append({k: request.form.get(f"{k}_{i}", "") for k in
                       ("type", "surtitre", "titre", "texte", "chiffre", "auteur", "fond")})
    if not slides:
        flash("Le carrousel doit garder au moins une diapositive.", "error")
        return redirect(url_for("main.contenu", post_id=post_id))
    opts["slides"] = slides
    s = store()
    s.execute("UPDATE posts SET options=? WHERE id=?", (json.dumps(opts, ensure_ascii=False), post_id))
    out_dir = Path(current_app.config["OUTPUT_DIR"]) / f"post_{post_id}"
    pipeline.render_carousels(s, post_id, slides, json.loads(post["networks"]), pipeline.get_brand(s), out_dir)
    s.execute("UPDATE posts SET progress='' WHERE id=?", (post_id,))
    flash("Carrousel mis à jour.", "ok")
    return redirect(url_for("main.contenu", post_id=post_id))


@bp.route("/contenus/<int:post_id>/supprimer", methods=["POST"])
@role_required("admin", "validateur")
def supprimer(post_id):
    import shutil

    post = _load_post(post_id)
    s = store()
    s.execute("DELETE FROM posts WHERE id=?", (post_id,))
    for d in (current_app.config["UPLOAD_DIR"], current_app.config["OUTPUT_DIR"]):
        shutil.rmtree(Path(d) / f"post_{post_id}", ignore_errors=True)
    s.log(f"Contenu supprimé : « {post['title']} »", g.user["id"])
    flash("Contenu supprimé.", "ok")
    return redirect(url_for("main.contenus"))


# --------------------------------------------------------------------- #
# Déclinaisons
# --------------------------------------------------------------------- #

def _variant(vid: int):
    v = store().query("SELECT * FROM variants WHERE id=?", (vid,), one=True)
    if v is None:
        abort(404)
    return v


def _back(v):
    return redirect(url_for("main.contenu", post_id=v["post_id"]) + f"#v{v['id']}")


@bp.route("/variantes/<int:vid>", methods=["POST"])
@login_required
def variante_save(vid):
    v = _variant(vid)
    f = request.form
    s = store()
    changed = any(f.get(k, v[k]) != v[k] for k in ("caption", "hashtags"))
    s.execute(
        "UPDATE variants SET caption=?, hashtags=?, first_comment=?, alt_text=?, title=?, collaborators=?, "
        "user_tags=? WHERE id=?",
        (f.get("caption", ""), f.get("hashtags", ""), f.get("first_comment", ""), f.get("alt_text", ""),
         f.get("title", ""), f.get("collaborators", ""), f.get("user_tags", ""), vid),
    )
    if changed and v["status"] == "valide":
        s.execute("UPDATE variants SET status='a_relire', approved_by=NULL WHERE id=?", (vid,))
        flash("Texte modifié : la déclinaison repasse « à relire ».", "info")
    else:
        flash("Enregistré.", "ok")
    return _back(v)


@bp.route("/variantes/<int:vid>/valider", methods=["POST"])
@role_required("admin", "validateur")
def variante_valider(vid):
    v = _variant(vid)
    s = store()
    s.execute("UPDATE variants SET status=CASE WHEN status IN ('a_relire','echec') THEN 'valide' ELSE status END, "
              "approved_by=? WHERE id=?", (g.user["id"], vid))
    s.log(f"Validé : déclinaison {NETWORKS[v['network']]['short']} (contenu {v['post_id']})", g.user["id"])
    return _back(v)


def _can_publish(v) -> bool:
    if _require_validation() and not v["approved_by"] and g.user["role"] not in ("admin", "validateur"):
        flash("Cette déclinaison doit d'abord être validée.", "error")
        return False
    return True


@bp.route("/variantes/<int:vid>/programmer", methods=["POST"])
@login_required
def variante_programmer(vid):
    v = _variant(vid)
    if not _can_publish(v):
        return _back(v)
    raw = request.form.get("scheduled_at", "")
    try:
        local = datetime.fromisoformat(raw)
    except ValueError:
        flash("Date invalide.", "error")
        return _back(v)
    if local.tzinfo is None:
        local = local.replace(tzinfo=_tz())
    when = local.astimezone(timezone.utc).replace(microsecond=0)
    s = store()
    s.execute("UPDATE variants SET scheduled_at=?, status='programme', error='', "
              "approved_by=COALESCE(approved_by, ?) WHERE id=?",
              (when.isoformat(), g.user["id"] if g.user["role"] in ("admin", "validateur") else None, vid))
    s.log(f"Programmé : {NETWORKS[v['network']]['short']} le {local.strftime('%d/%m à %Hh%M')}", g.user["id"])
    flash(f"Programmé le {local.strftime('%d/%m/%Y à %H:%M')}.", "ok")
    return _back(v)


@bp.route("/variantes/<int:vid>/deprogrammer", methods=["POST"])
@login_required
def variante_deprogrammer(vid):
    v = _variant(vid)
    store().execute("UPDATE variants SET status=CASE WHEN approved_by IS NULL THEN 'a_relire' ELSE 'valide' END, "
                    "scheduled_at=NULL WHERE id=? AND status IN ('programme','a_poster','echec')", (vid,))
    return _back(v)


@bp.route("/variantes/<int:vid>/publier", methods=["POST"])
@login_required
def variante_publier(vid):
    v = _variant(vid)
    if not _can_publish(v):
        return _back(v)
    try:
        res = publish_variant(_cfg(), store(), vid, g.user["id"])
        if res["mode"] == "auto":
            flash("Publié ✔", "ok")
        else:
            flash("Réseau en mode assisté : téléchargez le média, copiez la légende et publiez, "
                  "puis collez le lien du post.", "info")
    except PublishError as exc:
        flash(f"Échec : {exc}", "error")
    return _back(v)


@bp.route("/variantes/<int:vid>/marquer-publie", methods=["POST"])
@login_required
def variante_marquer(vid):
    v = _variant(vid)
    mark_published(store(), vid, request.form.get("url", ""), g.user["id"])
    flash("Marqué comme publié.", "ok")
    return _back(v)


@bp.route("/variantes/<int:vid>/pack.zip")
@login_required
def variante_pack(vid):
    """Pack de publication : médias + légende + 1er commentaire + consignes de tag."""
    v = _variant(vid)
    post = _load_post(v["post_id"])
    assets = store().query("SELECT * FROM assets WHERE post_id=? AND role='output' AND (network=? OR fmt='srt') "
                           "ORDER BY position", (v["post_id"], v["network"]))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for a in assets:
            p = Path(a["path"])
            if p.exists():
                z.write(p, p.name)
        text = compose_text(v["caption"], v["hashtags"], v["network"])
        notes = [f"Réseau : {NETWORKS[v['network']]['label']}", "", "LÉGENDE", text]
        if v["title"]:
            notes += ["", "TITRE", v["title"]]
        if v["first_comment"]:
            notes += ["", "PREMIER COMMENTAIRE", v["first_comment"]]
        if v["collaborators"]:
            notes += ["", "COLLABORATION (inviter comme collaborateur)", v["collaborators"]]
        if v["user_tags"]:
            notes += ["", "COMPTES À IDENTIFIER", v["user_tags"]]
        if v["alt_text"]:
            notes += ["", "TEXTE ALTERNATIF", v["alt_text"]]
        z.writestr("legende.txt", "\n".join(notes))
    buf.seek(0)
    name = secure_filename(f"{post['title']}_{v['network']}.zip") or "pack.zip"
    return send_file(buf, as_attachment=True, download_name=name, mimetype="application/zip")


# --------------------------------------------------------------------- #
# Fichiers
# --------------------------------------------------------------------- #

@bp.route("/fichiers/<int:asset_id>")
@login_required
def fichier(asset_id):
    a = store().query("SELECT * FROM assets WHERE id=?", (asset_id,), one=True)
    if a is None or not Path(a["path"]).exists():
        abort(404)
    return send_file(a["path"], mimetype=a["mime"] or None, as_attachment=request.args.get("dl") == "1",
                     download_name=Path(a["path"]).name, conditional=True)


@bp.route("/m/<token>/<name>")
def media_public(token, name):
    """URL publique (jeton non devinable) pour que Meta récupère les médias."""
    a = store().query("SELECT * FROM assets WHERE token=? AND role='output'", (token,), one=True)
    if a is None or Path(a["path"]).name != name or not Path(a["path"]).exists():
        abort(404)
    return send_file(a["path"], mimetype=a["mime"] or None, conditional=True)


# --------------------------------------------------------------------- #
# Calendrier
# --------------------------------------------------------------------- #

@bp.route("/calendrier")
@login_required
def calendrier():
    tz = _tz()
    try:
        offset = int(request.args.get("semaine", 0))
    except ValueError:
        offset = 0
    today = datetime.now(tz).date()
    monday = today - timedelta(days=today.weekday()) + timedelta(weeks=offset)
    start = datetime(monday.year, monday.month, monday.day, tzinfo=tz)
    end = start + timedelta(days=7)
    rows = store().query("""
        SELECT v.*, p.title, p.kind FROM variants v JOIN posts p ON p.id=v.post_id
        WHERE COALESCE(v.published_at, v.scheduled_at) >= ? AND COALESCE(v.published_at, v.scheduled_at) < ?
        ORDER BY COALESCE(v.published_at, v.scheduled_at)""",
                         (start.astimezone(timezone.utc).isoformat(), end.astimezone(timezone.utc).isoformat()))
    days = []
    for i in range(7):
        d = monday + timedelta(days=i)
        items = [r for r in rows if datetime.fromisoformat(r["published_at"] or r["scheduled_at"]).astimezone(tz).date() == d]
        days.append({"date": d, "label": analytics.JOURS[i], "posts": items, "today": d == today})
    return render_template("calendrier.html", active="calendrier", days=days, offset=offset, monday=monday)


# --------------------------------------------------------------------- #
# Performances
# --------------------------------------------------------------------- #

@bp.route("/performances")
@login_required
def performances():
    s = store()
    rows = analytics.performance_rows(s)
    by_net: dict[str, dict] = {}
    for r in rows:
        agg = by_net.setdefault(r["network"], {"n": 0, "impressions": 0, "reach": 0, "inter": 0, "eng": 0.0})
        agg["n"] += 1
        agg["impressions"] += r["impressions"] or 0
        agg["reach"] += r["reach"] or 0
        agg["inter"] += (r["likes"] or 0) + (r["comments"] or 0) + (r["shares"] or 0) + (r["saves"] or 0)
        agg["eng"] += r["engagement"]
    by_pillar: dict[str, dict] = {}
    for r in rows:
        key = r.get("pillar") or "—"
        agg = by_pillar.setdefault(key, {"n": 0, "eng": 0.0})
        agg["n"] += 1
        agg["eng"] += r["engagement"]
    hm = analytics.heatmap(s, current_app.config["TIMEZONE"])
    return render_template("performances.html", active="performances", rows=rows[:300], by_net=by_net,
                           by_pillar=by_pillar, hm=hm, jours=analytics.JOURS)


@bp.route("/performances/import", methods=["POST"])
@login_required
def performances_import():
    fs = request.files.get("csv")
    if not fs or not fs.filename:
        flash("Choisissez un fichier CSV.", "error")
        return redirect(url_for("main.performances"))
    res = analytics.import_csv(store(), fs.read(), request.form.get("network", ""))
    store().log(f"Import CSV « {fs.filename} » : {res['imported']} lignes, {res['matched']} rattachées", g.user["id"])
    cols = ", ".join(f"{k} ← « {v} »" for k, v in res["mapping"].items())
    flash(f"{res['imported']} ligne(s) importée(s), dont {res['matched']} rattachée(s) à une publication. "
          f"Colonnes reconnues : {cols or 'aucune'}.", "ok" if res["imported"] else "error")
    return redirect(url_for("main.performances"))


@bp.route("/performances/export.csv")
@login_required
def performances_export():
    data = analytics.export_csv(store())
    name = f"performances_reseaux_{datetime.now(_tz()).strftime('%Y%m%d')}.csv"
    return send_file(io.BytesIO(data), as_attachment=True, download_name=name, mimetype="text/csv")


@bp.route("/calendrier/export.csv")
@login_required
def calendrier_export():
    data = analytics.export_calendar_csv(store())
    name = f"publications_{datetime.now(_tz()).strftime('%Y%m%d')}.csv"
    return send_file(io.BytesIO(data), as_attachment=True, download_name=name, mimetype="text/csv")


@bp.route("/performances/actualiser", methods=["POST"])
@login_required
def performances_refresh():
    from app.publishing import refresh_insights

    n = refresh_insights(_cfg())
    flash(f"{n} publication(s) mise(s) à jour depuis les API.", "ok")
    return redirect(url_for("main.performances"))


# --------------------------------------------------------------------- #
# Réglages
# --------------------------------------------------------------------- #

BRAND_FIELDS = ["anthracite", "ivoire", "bronze", "secondaire", "signature_1", "signature_2", "accroche", "site"]
CAMPAIGN_FIELDS = list(DEFAULT_CAMPAIGN.keys())


@bp.route("/reglages", methods=["GET", "POST"])
@role_required("admin")
def reglages():
    s = store()
    if request.method == "POST":
        section = request.form.get("section")
        if section == "charte":
            brand = s.get_setting("brand", {}) or {}
            for k in BRAND_FIELDS:
                brand[k] = request.form.get(k, DEFAULT_BRAND[k]).strip()
            brand["watermark"] = request.form.get("watermark") == "1"
            logo = request.files.get("logo")
            if logo and logo.filename:
                path = Path(current_app.config["DATA_DIR"]) / ("logo" + Path(secure_filename(logo.filename)).suffix)
                logo.save(path)
                brand["logo_path"] = str(path)
            if request.form.get("logo_remove") == "1":
                brand["logo_path"] = ""
            s.set_setting("brand", brand)
        elif section == "campagne":
            s.set_setting("campaign", {k: request.form.get(k, "").strip() for k in CAMPAIGN_FIELDS})
            s.set_setting("validation_obligatoire", request.form.get("validation_obligatoire") == "1")
        elif section == "equipe":
            email, name, pwd = (request.form.get(k, "").strip() for k in ("email", "name", "password"))
            role = request.form.get("role", "editeur")
            if not email or not name or len(pwd) < 8 or role not in ROLES:
                flash("Nom, e-mail, rôle et mot de passe (8 caractères min.) requis.", "error")
            elif s.query("SELECT id FROM users WHERE email=?", (email.lower(),), one=True):
                flash("Cet e-mail existe déjà.", "error")
            else:
                create_user(email, name, pwd, role)
                s.log(f"Membre ajouté : {name} ({ROLES[role]})", g.user["id"])
        elif section == "supprimer_membre":
            uid = int(request.form.get("uid", 0))
            if uid != g.user["id"]:
                s.execute("DELETE FROM users WHERE id=?", (uid,))
        flash("Réglages enregistrés.", "ok")
        return redirect(url_for("main.reglages") + f"#{section}")
    cfg = _cfg()
    checks = {
        "Clé Claude (ANTHROPIC_API_KEY)": bool(cfg["ANTHROPIC_API_KEY"]),
        "URL publique (PUBLIC_BASE_URL)": bool(cfg["PUBLIC_BASE_URL"]),
        "Transcription (faster-whisper)": transcribe.available(),
        "Notifications (NOTIFY_WEBHOOK_URL)": bool(cfg["NOTIFY_WEBHOOK_URL"]),
    }
    return render_template("reglages.html", active="reglages", brand=pipeline.get_brand(s),
                           campaign=pipeline.get_campaign(s), users=s.query("SELECT * FROM users ORDER BY name"),
                           roles=ROLES, connected=connected_networks(cfg), checks=checks,
                           validation=_require_validation())


@bp.route("/reglages/apercu.jpg")
@login_required
def apercu_charte():
    """Aperçu d'une diapositive aux couleurs de la charte actuelle."""
    import tempfile

    brand = pipeline.get_brand(store())
    kind = request.args.get("type", "couverture")
    slide = {
        "couverture": {"type": "couverture", "surtitre": "Programme", "titre": "Écrivons ensemble le **Barreau de demain**",
                       "texte": brand["accroche"], "fond": "anthracite"},
        "cloture": {"type": "cloture", "surtitre": brand["signature_2"], "texte": brand["site"], "fond": "anthracite"},
        "chiffre": {"type": "chiffre", "surtitre": "Engagement", "chiffre": "2028",
                    "titre": "Un mandat **au service** de tous", "texte": "", "fond": "anthracite"},
    }.get(kind)
    if slide is None:
        abort(404)
    fmt = request.args.get("fmt", "4:5")
    if fmt not in FORMATS:
        fmt = "4:5"
    with tempfile.TemporaryDirectory() as tmp:
        p = media.render_slide(slide, 1, 3, fmt, brand, Path(tmp) / "apercu.jpg")
        data = p.read_bytes()
    return send_file(io.BytesIO(data), mimetype="image/jpeg")


@bp.app_template_global()
def fmt_for(network: str, kind: str) -> str:
    return target_format(network, kind)
