import io
import json
from datetime import datetime, timedelta, timezone

from PIL import Image

from app import ai, analytics, media, pipeline, publishing, transcribe
from app.brand import DEFAULT_BRAND
from app.db import Store
from app.publishers import compose_text, split_handles
from app.publishers.linkedin import escape_commentary


def fake_legendes(cfg, campaign, brand, *, networks, **kw):
    return {"variantes": [{"reseau": n, "legende": f"Légende {n}", "hashtags": ["Batonnat2028", "#Paris"],
                           "premier_commentaire": "", "texte_alternatif": "Photo", "titre": ""} for n in networks],
            "alerte": ""}


def fake_carrousel(cfg, campaign, brand, **kw):
    return {"diapositives": [
        {"type": "couverture", "surtitre": "Programme", "titre": "Le **Barreau** de demain", "texte": "",
         "chiffre": "", "auteur": "", "fond": "anthracite"},
        {"type": "cloture", "surtitre": "", "titre": "", "texte": "batonnatsuringriguer.com", "chiffre": "",
         "auteur": "", "fond": "anthracite"}], "alerte": ""}


def test_setup_login_and_pages(logged):
    for url in ["/", "/nouveau", "/contenus", "/calendrier", "/performances", "/reglages"]:
        r = logged.get(url)
        assert r.status_code == 200, url
    assert b"SURIN / GRIGUER" in logged.get("/").data
    r = logged.get("/reglages/apercu.jpg?type=cloture")
    assert r.status_code == 200 and r.mimetype == "image/jpeg"


def test_anonymous_redirects_to_setup(client):
    r = client.get("/")
    assert r.status_code == 302 and "premier-lancement" in r.location


def _jpeg():
    buf = io.BytesIO()
    Image.new("RGB", (1200, 800), (90, 90, 90)).save(buf, "JPEG")
    buf.seek(0)
    return buf


def test_photo_pipeline_end_to_end(logged, app, monkeypatch):
    monkeypatch.setattr(ai, "legendes", fake_legendes)
    monkeypatch.setattr(pipeline, "submit", lambda config, pid: None)
    r = logged.post("/nouveau", data={
        "kind": "photo", "title": "Rencontre", "networks": ["instagram_feed", "linkedin", "instagram_story"],
        "recadrage": "flou", "signature": "1", "collaborators": "@clarisse @merav",
        "files": [(_jpeg(), "a.jpg"), (_jpeg(), "b.jpg")],
    }, content_type="multipart/form-data")
    assert r.status_code == 302
    store = Store(app.config["DATABASE"])
    post_id = store.query("SELECT id FROM posts", one=True)["id"]
    pipeline.process_post(dict(app.config), store, post_id)
    post = store.query("SELECT * FROM posts WHERE id=?", (post_id,), one=True)
    assert post["status"] == "pret", post["error"]
    outs = store.query("SELECT * FROM assets WHERE role='output'")
    story = [a for a in outs if a["network"] == "instagram_story"]
    feed = [a for a in outs if a["network"] == "instagram_feed"]
    assert len(feed) == 2 and Image.open(feed[0]["path"]).size == (1080, 1350)
    assert Image.open(story[0]["path"]).size == (1080, 1920)
    v = store.query("SELECT * FROM variants WHERE network='instagram_feed'", one=True)
    assert v["collaborators"] == "@clarisse @merav" and v["hashtags"] == "#Batonnat2028 #Paris"
    assert store.query("SELECT collaborators FROM variants WHERE network='linkedin'", one=True)["collaborators"] == ""
    assert logged.get(f"/contenus/{post_id}").status_code == 200

    # validation → programmation → échéance → mode assisté (pas de connecteur)
    logged.post(f"/variantes/{v['id']}/valider")
    past = (datetime.now() - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M")
    logged.post(f"/variantes/{v['id']}/programmer", data={"scheduled_at": past})
    assert store.query("SELECT status FROM variants WHERE id=?", (v["id"],), one=True)["status"] == "programme"
    publishing.run_due(dict(app.config))
    assert store.query("SELECT status FROM variants WHERE id=?", (v["id"],), one=True)["status"] == "a_poster"
    pack = logged.get(f"/variantes/{v['id']}/pack.zip")
    assert pack.status_code == 200 and pack.mimetype == "application/zip"
    logged.post(f"/variantes/{v['id']}/marquer-publie", data={"url": "https://www.instagram.com/p/ABC123xyz/"})
    assert store.query("SELECT status FROM variants WHERE id=?", (v["id"],), one=True)["status"] == "publie"

    # import CSV rattaché par le lien
    csv_fr = ("Identifiant de la publication;Permalien;Heure de publication;Couverture;J'aime;Commentaires;"
              "Partages;Enregistrements\n"
              "1;https://www.instagram.com/p/ABC123xyz/;10/01/2026 12:30;1 200;80;5;3;12\n").encode("cp1252")
    res = analytics.import_csv(store, csv_fr)
    assert res["imported"] == 1 and res["matched"] == 1
    rows = analytics.performance_rows(store)
    assert rows[0]["reach"] == 1200 and rows[0]["engagement"] == 8.33
    out = analytics.export_csv(store).decode("utf-8-sig")
    assert "Rencontre" in out and "8,33" in out


def test_text_carousel_pipeline(app, monkeypatch):
    monkeypatch.setattr(ai, "legendes", fake_legendes)
    monkeypatch.setattr(ai, "carrousel", fake_carrousel)
    store = Store(app.config["DATABASE"])
    pid = store.execute(
        "INSERT INTO posts(title, kind, source_text, networks, options, created_at, updated_at) "
        "VALUES('Programme','texte','Notre programme…',?, '{}', 'x', 'x')",
        (json.dumps(["linkedin", "instagram_feed", "x"]),))
    pipeline.process_post(dict(app.config), store, pid)
    assets = store.query("SELECT * FROM assets WHERE post_id=? AND role='output'", (pid,))
    assert any(a["mime"] == "application/pdf" and a["network"] == "linkedin" for a in assets)
    assert len([a for a in assets if a["network"] == "x"]) == 2
    assert json.loads(store.query("SELECT options FROM posts WHERE id=?", (pid,), one=True)["options"])["slides"]


def test_video_pipeline_with_srt(app, monkeypatch, tmp_path):
    import subprocess

    monkeypatch.setattr(ai, "legendes", fake_legendes)
    monkeypatch.setattr(ai, "corriger_sous_titres", lambda c, ca, b, segs: segs)
    src = tmp_path / "v.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25",
                    "-f", "lavfi", "-i", "sine", "-t", "4", "-shortest", str(src)], check=True)
    srt = tmp_path / "v.srt"
    srt.write_text("1\n00:00:00,500 --> 00:00:02,000\nBonjour à toutes et à tous\n\n"
                   "2\n00:00:02,100 --> 00:00:03,800\nParis 2028, ensemble.\n", encoding="utf-8")
    store = Store(app.config["DATABASE"])
    opts = {"montage": "manuel", "debut": 1, "fin": 3.5, "sous_titres": True, "recadrage": "flou", "signature": True}
    pid = store.execute(
        "INSERT INTO posts(title, kind, networks, options, created_at, updated_at) VALUES('V','video',?,?,'x','x')",
        (json.dumps(["instagram_reel", "linkedin"]), json.dumps(opts)))
    pipeline.add_asset(store, pid, "source", src)
    pipeline.add_asset(store, pid, "source", srt, position=1)
    pipeline.process_post(dict(app.config), store, pid)
    post = store.query("SELECT * FROM posts WHERE id=?", (pid,), one=True)
    assert post["status"] == "pret", post["error"]
    reel = store.query("SELECT path FROM assets WHERE network='instagram_reel'", one=True)["path"]
    info = media.probe(reel)
    assert (info["width"], info["height"]) == (1080, 1920) and 2.0 <= info["duration"] <= 2.8
    assert store.query("SELECT 1 FROM assets WHERE fmt='srt'", one=True)


def test_suggest_slots_avoids_taken(app):
    store = Store(app.config["DATABASE"])
    slots = analytics.suggest_slots(store, "linkedin", "Europe/Paris", count=3)
    assert len(slots) == 3
    assert all(s["dt"] > datetime.now(timezone.utc) for s in slots)


def test_helpers():
    assert split_handles("@a, b ;@c") == ["a", "b", "c"]
    assert compose_text("Texte", "#a #b", "x") == "Texte #a #b"
    assert compose_text("Texte", "#a", "instagram_story") == "Texte"
    assert escape_commentary("Hello (Paris) @x #tag") == "Hello \\(Paris\\) \\@x #tag"
    segs = transcribe.split_long([{"start": 0, "end": 4, "text": "un deux trois quatre cinq six sept huit neuf dix onze douze"}], 20)
    assert len(segs) > 1 and abs(segs[-1]["end"] - 4) < 1e-6
    assert transcribe.shift([{"start": 0, "end": 2, "text": "a"}, {"start": 5, "end": 6, "text": "b"}], 1, 4) == \
        [{"start": 0, "end": 1, "text": "a"}]
    assert media._ass_text("En 2028 **ensemble**", "&HB", "&HI").count("\\c&HB") == 2
    assert analytics._num("1 234,5") == 1234 and analytics._num("12%") == 12
    assert DEFAULT_BRAND["bronze"] == "#BF9C75"
