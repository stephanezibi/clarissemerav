"""
app/pipeline.py — Chaîne automatique : contenu source → déclinaisons par réseau.

  vidéo  : (montage manuel ou IA) → transcription → sous-titres corrigés →
           recadrage par format (9:16, 4:5, 16:9…) + signature + sous-titres
  photo  : recadrage par format + signature
  texte  : découpage en carrousel par Claude → rendu charte → PDF LinkedIn
  puis   : légendes, hashtags, 1er commentaire, texte alternatif par réseau

Le traitement tourne en arrière-plan ; la page du contenu suit sa progression.
"""

from __future__ import annotations

import json
import secrets
import shutil
import tempfile
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app import ai, media, transcribe
from app.brand import DEFAULT_BRAND, DEFAULT_CAMPAIGN
from app.db import Store, now_iso
from app.networks import NETWORKS, target_format

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="traitement")


def get_brand(store: Store) -> dict:
    return {**DEFAULT_BRAND, **(store.get_setting("brand", {}) or {})}


def get_campaign(store: Store) -> dict:
    return {**DEFAULT_CAMPAIGN, **(store.get_setting("campaign", {}) or {})}


def ai_cfg(config) -> dict:
    return {
        "api_key": config["ANTHROPIC_API_KEY"],
        "model": config["CLAUDE_MODEL"],
        "effort": config["CLAUDE_EFFORT"],
    }


def submit(config, post_id: int) -> None:
    cfg = dict(config)
    _executor.submit(_safe_process, cfg, post_id)


def _safe_process(config: dict, post_id: int) -> None:
    store = Store(config["DATABASE"])
    try:
        process_post(config, store, post_id)
    except Exception as exc:  # l'erreur est affichée sur la page du contenu
        traceback.print_exc()
        store.execute("UPDATE posts SET status='erreur', error=?, updated_at=? WHERE id=?",
                      (str(exc)[:2000], now_iso(), post_id))


def _progress(store: Store, post_id: int, msg: str) -> None:
    store.execute("UPDATE posts SET progress=?, updated_at=? WHERE id=?", (msg, now_iso(), post_id))


def add_asset(store: Store, post_id: int, role: str, path: Path, network="", fmt="", position=0) -> int:
    mime = "video/mp4" if media.is_video(path) else (
        "application/pdf" if path.suffix.lower() == ".pdf" else (
            "text/plain" if path.suffix.lower() in {".srt", ".txt"} else "image/jpeg"))
    return store.execute(
        "INSERT INTO assets(post_id, role, network, fmt, path, mime, position, token) VALUES(?,?,?,?,?,?,?,?)",
        (post_id, role, network, fmt, str(path), mime, position, secrets.token_urlsafe(18)),
    )


def process_post(config: dict, store: Store, post_id: int, regenerate_media: bool = True) -> None:
    post = store.query("SELECT * FROM posts WHERE id=?", (post_id,), one=True)
    if post is None:
        return
    opts = json.loads(post["options"] or "{}")
    networks = [n for n in json.loads(post["networks"] or "[]") if n in NETWORKS]
    brand, campaign, cfg = get_brand(store), get_campaign(store), ai_cfg(config)
    out_dir = Path(config["OUTPUT_DIR"]) / f"post_{post_id}"
    sources = store.query("SELECT * FROM assets WHERE post_id=? AND role='source' ORDER BY position", (post_id,))
    store.execute("UPDATE posts SET status='traitement', error='', updated_at=? WHERE id=?", (now_iso(), post_id))

    vision: list[bytes] = []
    transcript_txt = post["transcript"] or ""
    source_text = post["source_text"] or ""

    if regenerate_media:
        if out_dir.exists():
            shutil.rmtree(out_dir)
        store.execute("DELETE FROM assets WHERE post_id=? AND role='output'", (post_id,))
        out_dir.mkdir(parents=True, exist_ok=True)

    kind = post["kind"]
    if kind == "video" and not regenerate_media:
        # légendes seules : on réutilise transcription et vignette existantes
        thumb = store.query("SELECT path FROM assets WHERE post_id=? AND fmt='apercu'", (post_id,), one=True)
        vision = [media.image_for_vision(thumb["path"])] if thumb and Path(thumb["path"]).exists() else []
    elif kind == "video":
        transcript_txt, vision = _process_video(config, store, post, opts, networks, brand, campaign,
                                                cfg, sources, out_dir, regenerate_media)
    elif kind == "photo":
        if regenerate_media:
            _process_photos(store, post_id, opts, networks, brand, sources, out_dir)
        vision = [media.image_for_vision(s["path"]) for s in sources if media.is_image(s["path"])][:6]
    elif kind == "texte":
        slides = opts.get("slides")
        if not slides:
            _progress(store, post_id, "Découpage du texte en carrousel…")
            data = ai.carrousel(cfg, campaign, brand, text=source_text, title=post["title"],
                                nb_max=int(opts.get("nb_slides", 8)), brief=post["brief"])
            slides = data["diapositives"]
            opts["slides"] = slides
            if data.get("alerte"):
                opts["alerte"] = data["alerte"]
            store.execute("UPDATE posts SET options=? WHERE id=?", (json.dumps(opts, ensure_ascii=False), post_id))
        if regenerate_media:
            render_carousels(store, post_id, slides, networks, brand, out_dir)
        source_text = source_text + "\n\nDiapositives :\n" + "\n".join(
            f"- {s.get('titre', '')} {s.get('texte', '')}" for s in slides)

    # ---- légendes ------------------------------------------------------ #
    _progress(store, post_id, "Rédaction des légendes et hashtags…")
    data = ai.legendes(
        cfg, campaign, brand, networks=networks, kind=kind, title=post["title"], brief=post["brief"],
        source_text=source_text, transcript=transcript_txt, pillar=post["pillar"], images=vision,
        collaborators=opts.get("collaborators", ""),
    )
    for v in data.get("variantes", []):
        _upsert_variant(store, post_id, v, opts)
    if data.get("alerte"):
        opts["alerte"] = (opts.get("alerte", "") + "\n" + data["alerte"]).strip()
        store.execute("UPDATE posts SET options=? WHERE id=?", (json.dumps(opts, ensure_ascii=False), post_id))
    store.execute("UPDATE posts SET status='pret', progress='', transcript=?, updated_at=? WHERE id=?",
                  (transcript_txt, now_iso(), post_id))


def _upsert_variant(store: Store, post_id: int, v: dict, opts: dict) -> None:
    net = v["reseau"]
    hashtags = " ".join(h if h.startswith("#") else f"#{h}" for h in v.get("hashtags", []))
    existing = store.query("SELECT id, status FROM variants WHERE post_id=? AND network=?", (post_id, net), one=True)
    collab = opts.get("collaborators", "") if NETWORKS[net]["collab"] else ""
    if existing:
        store.execute(
            "UPDATE variants SET caption=?, hashtags=?, first_comment=?, alt_text=?, title=? WHERE id=?",
            (v["legende"], hashtags, v.get("premier_commentaire", ""), v.get("texte_alternatif", ""),
             v.get("titre", ""), existing["id"]),
        )
    else:
        store.execute(
            "INSERT INTO variants(post_id, network, caption, hashtags, first_comment, alt_text, title, "
            "collaborators, user_tags, status) VALUES(?,?,?,?,?,?,?,?,?, 'a_relire')",
            (post_id, net, v["legende"], hashtags, v.get("premier_commentaire", ""),
             v.get("texte_alternatif", ""), v.get("titre", ""), collab, opts.get("user_tags", "")),
        )


def _process_photos(store, post_id, opts, networks, brand, sources, out_dir: Path) -> None:
    mode = opts.get("recadrage", "flou")
    watermark = opts.get("signature", True)
    done: dict[str, list[Path]] = {}
    for net in networks:
        fmt = target_format(net, "photo")
        if fmt not in done:
            _progress(store, post_id, f"Recadrage des photos en {fmt}…")
            done[fmt] = [
                media.reformat_image(s["path"], out_dir / f"photo_{fmt.replace(':', 'x')}_{i:02d}.jpg",
                                     fmt, brand, mode, watermark)
                for i, s in enumerate(sources) if media.is_image(s["path"])
            ]
        limit = NETWORKS[net].get("carousel_max", 1)
        for i, p in enumerate(done[fmt][:limit]):
            add_asset(store, post_id, "output", p, net, fmt, i)


def render_carousels(store, post_id, slides, networks, brand, out_dir: Path) -> None:
    store.execute("DELETE FROM assets WHERE post_id=? AND role='output'", (post_id,))
    done: dict[str, list[Path]] = {}
    for net in networks:
        fmt = target_format(net, "texte")
        if fmt not in done:
            _progress(store, post_id, f"Rendu du carrousel en {fmt}…")
            done[fmt] = media.render_carousel(slides, fmt, brand, out_dir, f"slide_{fmt.replace(':', 'x')}")
        imgs = done[fmt][: NETWORKS[net].get("carousel_max", 10)]
        if net == "linkedin":
            pdf = media.images_to_pdf(imgs, out_dir / "carrousel_linkedin.pdf")
            add_asset(store, post_id, "output", pdf, net, fmt, 0)
            for i, p in enumerate(imgs, 1):
                add_asset(store, post_id, "output", p, net, fmt, i)
        else:
            for i, p in enumerate(imgs):
                add_asset(store, post_id, "output", p, net, fmt, i)


def _process_video(config, store, post, opts, networks, brand, campaign, cfg, sources, out_dir, regenerate):
    post_id = post["id"]
    src = next((Path(s["path"]) for s in sources if media.is_video(s["path"])), None)
    srt_src = next((Path(s["path"]) for s in sources if s["path"].lower().endswith(".srt")), None)
    if src is None:
        raise media.MediaError("Aucune vidéo trouvée dans les fichiers envoyés.")
    info = media.probe(src)
    montage = opts.get("montage", "aucun")
    want_subs = bool(opts.get("sous_titres", True))

    segments: list[dict] = []
    need_transcript = want_subs or montage == "auto"
    if need_transcript:
        if srt_src:
            segments = transcribe.load_srt(srt_src)
        elif transcribe.available():
            _progress(store, post_id, "Transcription de la vidéo (sous-titres)…")
            with tempfile.TemporaryDirectory() as tmp:
                wav = media.extract_audio(src, Path(tmp) / "audio.wav") if info["has_audio"] else None
                if wav:
                    segments = transcribe.transcribe(wav, config.get("WHISPER_MODEL", "small"))
        elif want_subs:
            opts["alerte"] = ("Transcription automatique indisponible sur ce serveur "
                              "(installer faster-whisper) : vidéo générée sans sous-titres. "
                              "Vous pouvez joindre un fichier .srt.")
    transcript_txt = transcribe.as_text(segments)

    start, end = 0.0, None
    if montage == "manuel":
        start = float(opts.get("debut") or 0)
        end = float(opts["fin"]) if opts.get("fin") else None
    elif montage == "auto" and segments:
        _progress(store, post_id, "Montage automatique : choix de la meilleure séquence…")
        seq = ai.choisir_sequence(cfg, campaign, brand, transcript_txt, int(opts.get("duree_cible", 45)),
                                  info["duration"])
        start, end = seq["debut"], seq["fin"]
        opts["sequence_ia"] = seq
    opts["debut_effectif"], opts["fin_effective"] = start, end
    store.execute("UPDATE posts SET options=? WHERE id=?", (json.dumps(opts, ensure_ascii=False), post_id))

    subs: list[dict] = []
    if want_subs and segments:
        subs = transcribe.shift(segments, start, end)
        _progress(store, post_id, "Correction des sous-titres (noms propres, ponctuation)…")
        try:
            subs = ai.corriger_sous_titres(cfg, campaign, brand, subs)
        except ai.AIError:
            pass  # on garde la transcription brute
        subs = transcribe.split_long(subs)

    vision: list[bytes] = []
    if regenerate:
        work = src
        if start > 0 or end is not None:
            _progress(store, post_id, "Découpe de la séquence…")
            work = media.trim_video(src, out_dir / "sequence.mp4", start, end)
        if subs:
            srt = media.write_srt([{**s, "text": s["text"].replace("**", "")} for s in subs],
                                  out_dir / "sous-titres.srt")
            add_asset(store, post_id, "output", srt, "", "srt")
        done: dict[tuple, Path] = {}
        for net in networks:
            fmt = target_format(net, "video")
            max_s = NETWORKS[net].get("video_max_s")
            key = (fmt, max_s)
            if key not in done:
                _progress(store, post_id, f"Montage vidéo {fmt} ({NETWORKS[net]['short']})…")
                name = f"video_{fmt.replace(':', 'x')}_{int(max_s or 0)}.mp4"
                done[key] = media.reformat_video(work, out_dir / name, fmt, brand,
                                                 opts.get("recadrage", "flou"), subs or None,
                                                 opts.get("signature", True), max_s)
            add_asset(store, post_id, "output", done[key], net, fmt, 0)
        thumb = media.video_thumbnail(work, out_dir / "apercu.jpg", min(1.5, info["duration"] / 2))
        add_asset(store, post_id, "output", thumb, "", "apercu")
        vision = [media.image_for_vision(thumb)]
    return transcript_txt, vision
