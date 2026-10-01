"""
app/publishers/meta.py — Instagram (Content Publishing API) et Page Facebook.

Prérequis Meta : compte Instagram professionnel relié à une Page Facebook,
application Meta avec les permissions instagram_basic,
instagram_content_publish, pages_manage_posts, pages_read_engagement,
instagram_manage_insights. Meta télécharge les médias depuis PUBLIC_BASE_URL :
l'appli doit donc être en ligne (HTTPS).

Collaboration Instagram : paramètre `collaborators` (jusqu'à 3 comptes) → le
post apparaît sur les deux profils après acceptation par le collaborateur.
"""

from __future__ import annotations

import json
import time

import requests

from app.publishers import PublishContext, PublishError, compose_text  # noqa: F401


def _graph(config: dict) -> str:
    return f"https://graph.facebook.com/{config.get('META_GRAPH_VERSION', 'v23.0')}"


def _post(url: str, data: dict) -> dict:
    r = requests.post(url, data=data, timeout=120)
    body = r.json() if r.content else {}
    if r.status_code >= 400 or "error" in body:
        msg = body.get("error", {}).get("message", r.text[:300])
        raise PublishError(f"Meta : {msg}")
    return body


def _get(url: str, params: dict) -> dict:
    r = requests.get(url, params=params, timeout=60)
    body = r.json() if r.content else {}
    if r.status_code >= 400 or "error" in body:
        raise PublishError(f"Meta : {body.get('error', {}).get('message', r.text[:300])}")
    return body


def _wait_container(config: dict, container_id: str, token: str, timeout_s: int = 900) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        status = _get(f"{_graph(config)}/{container_id}", {"fields": "status_code,status", "access_token": token})
        code = status.get("status_code")
        if code == "FINISHED":
            return
        if code in {"ERROR", "EXPIRED"}:
            raise PublishError(f"Instagram a refusé le média : {status.get('status', code)}")
        time.sleep(5)
    raise PublishError("Instagram : traitement du média trop long.")


def _tags(handles: list[str]) -> str:
    # Positions réparties sur l'image (Instagram exige x, y entre 0 et 1)
    return json.dumps([
        {"username": h, "x": round(0.2 + 0.6 * (i % 3) / 2, 2), "y": round(0.3 + 0.2 * (i // 3), 2)}
        for i, h in enumerate(handles[:20])
    ])


def publish_instagram(network: str, ctx: PublishContext) -> tuple[str, str]:
    cfg = ctx.config
    token, ig = cfg["META_ACCESS_TOKEN"], cfg["INSTAGRAM_USER_ID"]
    base = f"{_graph(cfg)}/{ig}"
    items = [m for m in ctx.media if m["mime"] in {"image/jpeg", "video/mp4"}]
    if not items:
        raise PublishError("Aucun média publiable pour Instagram.")
    common = {"access_token": token}
    collab = {"collaborators": json.dumps(ctx.collaborators[:3])} if ctx.collaborators else {}

    def single(m: dict, extra: dict) -> str:
        data = dict(common, **extra)
        if m["mime"] == "video/mp4":
            data["video_url"] = m["url"]
        else:
            data["image_url"] = m["url"]
        cid = _post(f"{base}/media", data)["id"]
        if m["mime"] == "video/mp4":
            _wait_container(cfg, cid, token)
        return cid

    if network == "instagram_story":
        m = items[0]
        cid = single(m, {"media_type": "STORIES"})
    elif network == "instagram_reel" or (len(items) == 1 and items[0]["mime"] == "video/mp4"):
        cid = single(items[0], {"media_type": "REELS", "caption": ctx.caption, "share_to_feed": "true", **collab})
    elif len(items) == 1:
        extra = {"caption": ctx.caption, **collab}
        if ctx.user_tags:
            extra["user_tags"] = _tags(ctx.user_tags)
        if ctx.alt_text:
            extra["alt_text"] = ctx.alt_text[:1000]
        cid = single(items[0], extra)
    else:
        children = []
        for i, m in enumerate(items[:20]):
            extra = {"is_carousel_item": "true"}
            if m["mime"] == "video/mp4":
                extra["media_type"] = "VIDEO"
            if i == 0 and ctx.user_tags:
                extra["user_tags"] = _tags(ctx.user_tags)
            children.append(single(m, extra))
        cid = _post(f"{base}/media", dict(common, media_type="CAROUSEL", children=",".join(children),
                                           caption=ctx.caption, **collab))["id"]
        _wait_container(cfg, cid, token)

    media_id = _post(f"{base}/media_publish", dict(common, creation_id=cid))["id"]
    if ctx.first_comment and network != "instagram_story":
        try:
            _post(f"{_graph(cfg)}/{media_id}/comments", dict(common, message=ctx.first_comment))
        except PublishError:
            pass
    permalink = ""
    try:
        permalink = _get(f"{_graph(cfg)}/{media_id}", {"fields": "permalink", "access_token": token}).get("permalink", "")
    except PublishError:
        pass
    return media_id, permalink


def publish_facebook(network: str, ctx: PublishContext) -> tuple[str, str]:
    cfg = ctx.config
    token = cfg.get("FACEBOOK_PAGE_TOKEN") or cfg["META_ACCESS_TOKEN"]
    page = cfg["FACEBOOK_PAGE_ID"]
    base = f"{_graph(cfg)}/{page}"
    videos = [m for m in ctx.media if m["mime"] == "video/mp4"]
    images = [m for m in ctx.media if m["mime"] == "image/jpeg"]
    if videos:
        res = _post(f"{base}/videos", {"file_url": videos[0]["url"], "description": ctx.caption,
                                        "title": ctx.title or "", "access_token": token})
        vid = res["id"]
        return vid, f"https://www.facebook.com/{page}/videos/{vid}"
    if len(images) == 1:
        res = _post(f"{base}/photos", {"url": images[0]["url"], "caption": ctx.caption, "access_token": token})
        pid = res.get("post_id") or res["id"]
    elif images:
        ids = [
            _post(f"{base}/photos", {"url": m["url"], "published": "false", "access_token": token})["id"]
            for m in images[:10]
        ]
        data = {"message": ctx.caption, "access_token": token}
        for i, fid in enumerate(ids):
            data[f"attached_media[{i}]"] = json.dumps({"media_fbid": fid})
        pid = _post(f"{base}/feed", data)["id"]
    else:
        pid = _post(f"{base}/feed", {"message": ctx.caption, "access_token": token})["id"]
    if ctx.first_comment:
        try:
            _post(f"{_graph(cfg)}/{pid}/comments", {"message": ctx.first_comment, "access_token": token})
        except PublishError:
            pass
    return pid, f"https://www.facebook.com/{pid}"


# --------------------------------------------------------------------- #
# Statistiques (remontée automatique quotidienne)
# --------------------------------------------------------------------- #

def instagram_insights(config: dict, media_id: str, network: str) -> dict:
    token = config["META_ACCESS_TOKEN"]
    base = _graph(config)
    info = _get(f"{base}/{media_id}", {"fields": "like_count,comments_count,permalink,timestamp,media_type",
                                         "access_token": token})
    metrics = "reach,saved,shares,views,total_interactions"
    if network == "instagram_story":
        metrics = "reach,views,replies,shares"
    out = {"likes": info.get("like_count", 0), "comments": info.get("comments_count", 0),
           "post_url": info.get("permalink", "")}
    try:
        data = _get(f"{base}/{media_id}/insights", {"metric": metrics, "access_token": token})
        for row in data.get("data", []):
            val = (row.get("values") or [{}])[0].get("value", 0)
            name = row.get("name")
            if name == "reach":
                out["reach"] = val
            elif name == "saved":
                out["saves"] = val
            elif name == "shares":
                out["shares"] = val
            elif name == "views":
                out["video_views"] = val
                out["impressions"] = val
    except PublishError:
        pass
    return out


def facebook_insights(config: dict, post_id: str) -> dict:
    token = config.get("FACEBOOK_PAGE_TOKEN") or config["META_ACCESS_TOKEN"]
    base = _graph(config)
    info = _get(f"{base}/{post_id}", {
        "fields": "shares,comments.summary(true).limit(0),reactions.summary(true).limit(0),permalink_url",
        "access_token": token,
    })
    out = {
        "likes": info.get("reactions", {}).get("summary", {}).get("total_count", 0),
        "comments": info.get("comments", {}).get("summary", {}).get("total_count", 0),
        "shares": info.get("shares", {}).get("count", 0),
        "post_url": info.get("permalink_url", ""),
    }
    try:
        data = _get(f"{base}/{post_id}/insights", {"metric": "post_impressions,post_impressions_unique,post_clicks",
                                                     "access_token": token})
        for row in data.get("data", []):
            val = (row.get("values") or [{}])[0].get("value", 0)
            key = {"post_impressions": "impressions", "post_impressions_unique": "reach",
                   "post_clicks": "clicks"}.get(row.get("name"))
            if key:
                out[key] = val
    except PublishError:
        pass
    return out
