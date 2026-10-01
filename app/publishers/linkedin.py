"""
app/publishers/linkedin.py — LinkedIn Posts API (profil ou page).

LINKEDIN_AUTHOR_URN = urn:li:person:xxxx (profil, scope w_member_social)
                   ou urn:li:organization:xxxx (page, scope w_organization_social).
Carrousel = document PDF (format natif « document » de LinkedIn).
"""

from __future__ import annotations

import re
import time
from pathlib import Path

import requests

from app.publishers import PublishContext, PublishError

API = "https://api.linkedin.com/rest"


def _headers(cfg: dict, json_body: bool = True) -> dict:
    h = {
        "Authorization": f"Bearer {cfg['LINKEDIN_ACCESS_TOKEN']}",
        "LinkedIn-Version": cfg.get("LINKEDIN_VERSION", "202509"),
        "X-Restli-Protocol-Version": "2.0.0",
    }
    if json_body:
        h["Content-Type"] = "application/json"
    return h


def _check(r: requests.Response) -> dict:
    if r.status_code >= 400:
        raise PublishError(f"LinkedIn ({r.status_code}) : {r.text[:300]}")
    return r.json() if r.content else {}


def escape_commentary(text: str) -> str:
    """Le champ commentary de LinkedIn interprète certains caractères ; on les échappe.
    Les hashtags (#mot) restent actifs."""
    return re.sub(r"([\\|{}@\[\]()<>*_~])", r"\\\1", text)


def _upload_simple(cfg: dict, kind: str, path: Path) -> str:
    r = requests.post(f"{API}/{kind}?action=initializeUpload", headers=_headers(cfg), timeout=60,
                      json={"initializeUploadRequest": {"owner": cfg["LINKEDIN_AUTHOR_URN"]}})
    val = _check(r)["value"]
    up = requests.put(val["uploadUrl"], data=path.read_bytes(),
                      headers={"Authorization": f"Bearer {cfg['LINKEDIN_ACCESS_TOKEN']}"}, timeout=300)
    if up.status_code >= 400:
        raise PublishError(f"LinkedIn : échec de l'envoi du fichier ({up.status_code}).")
    return val.get("image") or val.get("document")


def _upload_video(cfg: dict, path: Path) -> str:
    size = path.stat().st_size
    r = requests.post(f"{API}/videos?action=initializeUpload", headers=_headers(cfg), timeout=60, json={
        "initializeUploadRequest": {"owner": cfg["LINKEDIN_AUTHOR_URN"], "fileSizeBytes": size,
                                    "uploadCaptions": False, "uploadThumbnail": False}})
    val = _check(r)["value"]
    etags = []
    with path.open("rb") as fh:
        for part in val["uploadInstructions"]:
            fh.seek(part["firstByte"])
            chunk = fh.read(part["lastByte"] - part["firstByte"] + 1)
            up = requests.put(part["uploadUrl"], data=chunk, timeout=600,
                              headers={"Content-Type": "application/octet-stream"})
            if up.status_code >= 400:
                raise PublishError(f"LinkedIn : échec d'envoi vidéo ({up.status_code}).")
            etags.append(up.headers.get("ETag", "").strip('"'))
    _check(requests.post(f"{API}/videos?action=finalizeUpload", headers=_headers(cfg), timeout=60, json={
        "finalizeUploadRequest": {"video": val["video"], "uploadToken": val.get("uploadToken", ""),
                                  "uploadedPartIds": etags}}))
    # attendre que la vidéo soit disponible
    for _ in range(60):
        st = requests.get(f"{API}/videos/{requests.utils.quote(val['video'])}", headers=_headers(cfg), timeout=30)
        if st.ok and st.json().get("status") == "AVAILABLE":
            break
        time.sleep(5)
    return val["video"]


def publish(network: str, ctx: PublishContext) -> tuple[str, str]:
    cfg = ctx.config
    pdfs = [m for m in ctx.media if m["mime"] == "application/pdf"]
    videos = [m for m in ctx.media if m["mime"] == "video/mp4"]
    images = [m for m in ctx.media if m["mime"] == "image/jpeg"]
    content = None
    if pdfs:
        doc = _upload_simple(cfg, "documents", Path(pdfs[0]["path"]))
        content = {"media": {"title": (ctx.title or "Surin / Griguer — Bâtonnat 2028")[:200], "id": doc}}
    elif videos:
        content = {"media": {"title": ctx.title or "", "id": _upload_video(cfg, Path(videos[0]["path"]))}}
    elif len(images) == 1:
        content = {"media": {"id": _upload_simple(cfg, "images", Path(images[0]["path"])),
                             "altText": ctx.alt_text[:4086]}}
    elif images:
        content = {"multiImage": {"images": [
            {"id": _upload_simple(cfg, "images", Path(m["path"])), "altText": ctx.alt_text[:4086]}
            for m in images[:20]]}}
    body = {
        "author": cfg["LINKEDIN_AUTHOR_URN"],
        "commentary": escape_commentary(ctx.caption),
        "visibility": "PUBLIC",
        "distribution": {"feedDistribution": "MAIN_FEED", "targetEntities": [],
                         "thirdPartyDistributionChannels": []},
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }
    if content:
        body["content"] = content
    r = requests.post(f"{API}/posts", headers=_headers(cfg), json=body, timeout=120)
    _check(r)
    urn = r.headers.get("x-restli-id", "")
    return urn, f"https://www.linkedin.com/feed/update/{urn}/" if urn else ""
