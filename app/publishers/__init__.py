"""
app/publishers — Publication sur les réseaux.

Deux modes par réseau :
  - automatique : un connecteur API est configuré (jetons dans .env) →
    l'appli publie elle-même à l'heure programmée ;
  - assisté : pas de connecteur (TikTok, YouTube, Threads, ou jetons absents)
    → à l'heure dite, l'équipe est notifiée et trouve sur la fiche le média
    prêt à télécharger, la légende à copier et le lien pour ouvrir le réseau.
"""

from __future__ import annotations

from dataclasses import dataclass, field


class PublishError(RuntimeError):
    pass


@dataclass
class PublishContext:
    config: dict
    caption: str
    first_comment: str = ""
    alt_text: str = ""
    title: str = ""
    collaborators: list[str] = field(default_factory=list)
    user_tags: list[str] = field(default_factory=list)
    media: list[dict] = field(default_factory=list)  # [{path, url, mime}]
    kind: str = "photo"


def compose_text(caption: str, hashtags: str, network: str) -> str:
    caption = (caption or "").strip()
    hashtags = (hashtags or "").strip()
    if not hashtags or network == "instagram_story":
        return caption
    sep = " " if network == "x" else "\n\n"
    return f"{caption}{sep}{hashtags}"


def split_handles(value: str) -> list[str]:
    out = []
    for part in (value or "").replace(",", " ").replace(";", " ").split():
        h = part.strip().lstrip("@")
        if h:
            out.append(h)
    return out


def connector_for(network: str, config: dict):
    """Renvoie la fonction de publication si le réseau est connecté, sinon None."""
    from app.publishers import linkedin, meta, x

    if network.startswith("instagram") and config.get("META_ACCESS_TOKEN") and config.get("INSTAGRAM_USER_ID"):
        return meta.publish_instagram
    if network == "facebook" and config.get("FACEBOOK_PAGE_ID") and (
        config.get("FACEBOOK_PAGE_TOKEN") or config.get("META_ACCESS_TOKEN")
    ):
        return meta.publish_facebook
    if network == "linkedin" and config.get("LINKEDIN_ACCESS_TOKEN") and config.get("LINKEDIN_AUTHOR_URN"):
        return linkedin.publish
    if network == "x" and config.get("X_USER_ACCESS_TOKEN"):
        return x.publish
    return None


def connected_networks(config: dict) -> dict[str, bool]:
    from app.networks import NETWORKS

    return {n: connector_for(n, config) is not None for n in NETWORKS}
