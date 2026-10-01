"""
app/publishers/x.py — X (Twitter) API v2.

X_USER_ACCESS_TOKEN : jeton OAuth 2.0 utilisateur (scopes tweet.write,
users.read, media.write, offline.access). Accès API payant chez X.
"""

from __future__ import annotations

import time
from pathlib import Path

import requests

from app.publishers import PublishContext, PublishError

API = "https://api.x.com/2"
CHUNK = 4 * 1024 * 1024


def _h(cfg: dict) -> dict:
    return {"Authorization": f"Bearer {cfg['X_USER_ACCESS_TOKEN']}"}


def _check(r: requests.Response) -> dict:
    if r.status_code >= 400:
        raise PublishError(f"X ({r.status_code}) : {r.text[:300]}")
    return r.json() if r.content else {}


def _upload(cfg: dict, path: Path, mime: str) -> str:
    video = mime == "video/mp4"
    size = path.stat().st_size
    init = _check(requests.post(f"{API}/media/upload/initialize", headers=_h(cfg), timeout=60, json={
        "media_type": mime, "total_bytes": size,
        "media_category": "tweet_video" if video else "tweet_image"}))
    media_id = init["data"]["id"]
    with path.open("rb") as fh:
        idx = 0
        while chunk := fh.read(CHUNK):
            _check(requests.post(f"{API}/media/upload/{media_id}/append", headers=_h(cfg), timeout=300,
                                 files={"media": chunk}, data={"segment_index": idx}))
            idx += 1
    fin = _check(requests.post(f"{API}/media/upload/{media_id}/finalize", headers=_h(cfg), timeout=60))
    info = fin.get("data", {}).get("processing_info")
    while info and info.get("state") in {"pending", "in_progress"}:
        time.sleep(info.get("check_after_secs", 3))
        st = _check(requests.get(f"{API}/media/upload", headers=_h(cfg), timeout=30,
                                 params={"command": "STATUS", "media_id": media_id}))
        info = st.get("data", {}).get("processing_info")
    if info and info.get("state") == "failed":
        raise PublishError("X a refusé la vidéo.")
    return media_id


def publish(network: str, ctx: PublishContext) -> tuple[str, str]:
    cfg = ctx.config
    if len(ctx.caption) > 280:
        raise PublishError(f"Texte trop long pour X ({len(ctx.caption)} / 280 caractères).")
    medias = [m for m in ctx.media if m["mime"] in {"image/jpeg", "video/mp4"}]
    videos = [m for m in medias if m["mime"] == "video/mp4"]
    chosen = videos[:1] if videos else medias[:4]
    body: dict = {"text": ctx.caption}
    if chosen:
        body["media"] = {"media_ids": [_upload(cfg, Path(m["path"]), m["mime"]) for m in chosen]}
    data = _check(requests.post(f"{API}/tweets", headers=_h(cfg), json=body, timeout=60))
    tid = data["data"]["id"]
    if ctx.first_comment:
        try:
            _check(requests.post(f"{API}/tweets", headers=_h(cfg), timeout=60, json={
                "text": ctx.first_comment[:280], "reply": {"in_reply_to_tweet_id": tid}}))
        except PublishError:
            pass
    return tid, f"https://x.com/i/web/status/{tid}"
