"""
app/networks.py — Spécifications par réseau : formats, limites, créneaux.

Chaque « réseau » est en fait une cible de publication (un compte peut en
avoir plusieurs : feed, reel, story…). Les créneaux par défaut visent une
audience d'avocats parisiens (matin avant l'audience, pause déjeuner, fin de
journée) ; ils sont ensuite affinés par les performances importées.
"""

from __future__ import annotations

# Formats de sortie (largeur, hauteur)
FORMATS = {
    "9:16": (1080, 1920),
    "4:5": (1080, 1350),
    "1:1": (1080, 1080),
    "16:9": (1920, 1080),
    "1.91:1": (1200, 628),
}

NETWORKS = {
    "instagram_feed": {
        "label": "Instagram — Publication / Carrousel",
        "short": "IG feed",
        "platform": "instagram",
        "image_format": "4:5",
        "video_format": "4:5",
        "carousel_format": "4:5",
        "caption_max": 2200,
        "hashtags_max": 30,
        "hashtags_reco": 5,
        "carousel_max": 20,
        "supports": {"photo", "video", "texte"},
        "collab": True,
        "open_url": "https://www.instagram.com/",
    },
    "instagram_reel": {
        "label": "Instagram — Reel",
        "short": "IG reel",
        "platform": "instagram",
        "video_format": "9:16",
        "caption_max": 2200,
        "hashtags_max": 30,
        "hashtags_reco": 5,
        "video_max_s": 180,
        "supports": {"video"},
        "collab": True,
        "open_url": "https://www.instagram.com/",
    },
    "instagram_story": {
        "label": "Instagram — Story",
        "short": "IG story",
        "platform": "instagram",
        "image_format": "9:16",
        "video_format": "9:16",
        "caption_max": 0,
        "hashtags_max": 0,
        "hashtags_reco": 0,
        "video_max_s": 60,
        "supports": {"photo", "video"},
        "collab": False,
        "open_url": "https://www.instagram.com/",
    },
    "facebook": {
        "label": "Facebook — Page",
        "short": "Facebook",
        "platform": "facebook",
        "image_format": "4:5",
        "video_format": "16:9",
        "carousel_format": "1:1",
        "caption_max": 63206,
        "hashtags_max": 5,
        "hashtags_reco": 2,
        "carousel_max": 10,
        "supports": {"photo", "video", "texte"},
        "collab": False,
        "open_url": "https://www.facebook.com/",
    },
    "linkedin": {
        "label": "LinkedIn",
        "short": "LinkedIn",
        "platform": "linkedin",
        "image_format": "4:5",
        "video_format": "16:9",
        "carousel_format": "4:5",  # publié en document PDF
        "caption_max": 3000,
        "hashtags_max": 5,
        "hashtags_reco": 3,
        "carousel_max": 20,
        "supports": {"photo", "video", "texte"},
        "collab": False,
        "open_url": "https://www.linkedin.com/feed/",
    },
    "x": {
        "label": "X (Twitter)",
        "short": "X",
        "platform": "x",
        "image_format": "16:9",
        "video_format": "16:9",
        "carousel_format": "1:1",
        "caption_max": 280,
        "hashtags_max": 2,
        "hashtags_reco": 1,
        "carousel_max": 4,
        "video_max_s": 140,
        "supports": {"photo", "video", "texte"},
        "collab": False,
        "open_url": "https://x.com/compose/post",
    },
    "threads": {
        "label": "Threads",
        "short": "Threads",
        "platform": "threads",
        "image_format": "4:5",
        "video_format": "9:16",
        "carousel_format": "4:5",
        "caption_max": 500,
        "hashtags_max": 1,
        "hashtags_reco": 1,
        "carousel_max": 20,
        "supports": {"photo", "video", "texte"},
        "collab": False,
        "open_url": "https://www.threads.net/",
    },
    "tiktok": {
        "label": "TikTok",
        "short": "TikTok",
        "platform": "tiktok",
        "image_format": "9:16",
        "video_format": "9:16",
        "carousel_format": "9:16",
        "caption_max": 4000,
        "hashtags_max": 5,
        "hashtags_reco": 4,
        "carousel_max": 35,
        "video_max_s": 600,
        "supports": {"photo", "video", "texte"},
        "collab": False,
        "open_url": "https://www.tiktok.com/upload",
    },
    "youtube_shorts": {
        "label": "YouTube Shorts",
        "short": "Shorts",
        "platform": "youtube",
        "video_format": "9:16",
        "caption_max": 5000,
        "title_max": 100,
        "hashtags_max": 3,
        "hashtags_reco": 3,
        "video_max_s": 180,
        "supports": {"video"},
        "collab": False,
        "open_url": "https://studio.youtube.com/",
    },
}

# Créneaux par défaut : {réseau: {jour (0=lundi): [heures]}}
_WEEK = range(0, 5)
DEFAULT_SLOTS = {
    "linkedin": {d: [8, 12, 18] for d in _WEEK} | {1: [8, 10, 12, 18], 2: [8, 10, 12, 18], 3: [8, 12, 17]},
    "instagram_feed": {d: [12, 19] for d in range(7)} | {6: [11, 19]},
    "instagram_reel": {d: [12, 19, 21] for d in range(7)},
    "instagram_story": {d: [8, 12, 18] for d in range(7)},
    "facebook": {d: [12, 19] for d in range(7)},
    "x": {d: [8, 12, 18] for d in _WEEK},
    "threads": {d: [8, 12, 19] for d in range(7)},
    "tiktok": {d: [19, 21] for d in range(7)} | {6: [12, 20]},
    "youtube_shorts": {d: [18, 20] for d in range(7)},
}


def networks_for_kind(kind: str) -> dict:
    return {k: v for k, v in NETWORKS.items() if kind in v["supports"]}


def target_format(network: str, kind: str) -> str:
    spec = NETWORKS[network]
    if kind == "video":
        return spec.get("video_format", "9:16")
    if kind == "texte":
        return spec.get("carousel_format") or spec.get("image_format", "1:1")
    return spec.get("image_format", "1:1")


def label(network: str) -> str:
    return NETWORKS.get(network, {}).get("label", network)
