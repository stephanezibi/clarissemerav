"""
app/ai.py — Rédaction assistée par Claude.

  - legendes()        légende + hashtags + 1er commentaire + texte alternatif par réseau
  - carrousel()       découpe un texte en diapositives (charte : capitales, mots bronze)
  - corriger_sous_titres()  ponctuation, noms propres, glossaire — horodatage conservé
  - choisir_sequence() montage automatique : meilleure séquence pour un format court

Chaque appel renvoie du JSON validé par schéma (structured outputs).
Le garde-fou déontologique (règles de la profession d'avocat et des élections
ordinales) est inscrit dans le prompt système.
"""

from __future__ import annotations

import base64
import json

from anthropic import Anthropic, APIConnectionError, APIStatusError, RateLimitError

from app.networks import NETWORKS

DEONTOLOGIE = """Cadre impératif (profession d'avocat, élection ordinale) :
- Respect des principes essentiels : dignité, conscience, indépendance, probité,
  humanité, honneur, loyauté, désintéressement, confraternité, délicatesse,
  modération et courtoisie.
- Jamais de dénigrement, d'attaque ou de comparaison péjorative envers les autres
  candidats ou des confrères ; on parle de projet, pas d'adversaires.
- Aucune information couverte par le secret professionnel, aucun nom de client,
  aucune affaire en cours.
- Pas de promesse irréaliste ni d'affirmation invérifiable ; pas de chiffre inventé.
- Sobriété : pas de ton racoleur, pas de superlatifs creux, pas de « clickbait ».
Si la demande sort de ce cadre, reformule de façon conforme et signale-le dans "alerte"."""


class AIError(RuntimeError):
    pass


def _client(api_key: str) -> Anthropic:
    if not api_key:
        raise AIError("Clé ANTHROPIC_API_KEY absente : renseignez-la dans le fichier .env du serveur.")
    return Anthropic(api_key=api_key)


def _system(campaign: dict, brand: dict) -> str:
    return f"""Tu es le community manager de la campagne « {campaign.get('nom', '')} ».
{campaign.get('candidats', '')}
Site : {campaign.get('site', '')}

Ton et style : {campaign.get('ton', '')}
Messages clés : {campaign.get('messages_cles', '')}
Signature : {brand.get('signature_1', '')} — {brand.get('signature_2', '')}
Accroche de référence : {brand.get('accroche', '')}
Hashtags de campagne : {campaign.get('hashtags_defaut', '')}
Noms et termes à orthographier exactement : {campaign.get('glossaire', '')}
Interdits : {campaign.get('interdits', '')}

{DEONTOLOGIE}

Tu écris en français impeccable (typographie française : espaces insécables
avant « : ; ? ! », guillemets « »)."""


def _call(cfg: dict, system: str, content, schema: dict, max_tokens: int = 16000) -> dict:
    client = _client(cfg["api_key"])
    try:
        resp = client.beta.messages.create(
            model=cfg.get("model", "claude-opus-5-5"),
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": content}],
            thinking={"type": "adaptive"},
            output_config={
                "effort": cfg.get("effort", "medium"),
                "format": {"type": "json_schema", "schema": schema},
            },
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except RateLimitError as exc:
        raise AIError("Limite de débit Claude atteinte, réessayez dans une minute.") from exc
    except APIStatusError as exc:
        raise AIError(f"Erreur Claude ({exc.status_code}) : {exc.message}") from exc
    except APIConnectionError as exc:
        raise AIError("Impossible de joindre l'API Claude (réseau).") from exc
    if resp.stop_reason == "refusal":
        raise AIError("Claude a décliné cette demande ; reformulez le brief.")
    if resp.stop_reason == "max_tokens":
        raise AIError("Réponse tronquée (trop longue) ; raccourcissez le texte source.")
    text = next((b.text for b in resp.content if b.type == "text"), "")
    try:
        return json.loads(text)
    except ValueError as exc:
        raise AIError("Réponse Claude illisible, réessayez.") from exc


def _images_content(images: list[bytes]) -> list[dict]:
    return [
        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                     "data": base64.standard_b64encode(b).decode()}}
        for b in images[:6]
    ]


# --------------------------------------------------------------------- #

def legendes(cfg, campaign, brand, *, networks: list[str], kind: str, title: str,
             brief: str = "", source_text: str = "", transcript: str = "",
             pillar: str = "", images: list[bytes] | None = None,
             collaborators: str = "") -> dict:
    specs = []
    for n in networks:
        s = NETWORKS[n]
        specs.append(
            f"- {n} ({s['label']}) : légende ≤ {s['caption_max']} caractères"
            f"{' (story : pas de légende, propose un texte court à incruster/sticker)' if n == 'instagram_story' else ''}"
            f", {s['hashtags_reco']} hashtags recommandés (max {s['hashtags_max']})"
            f"{', titre ≤ 100 caractères' if n == 'youtube_shorts' else ''}"
        )
    schema = {
        "type": "object",
        "properties": {
            "variantes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "reseau": {"type": "string", "enum": networks},
                        "legende": {"type": "string"},
                        "hashtags": {"type": "array", "items": {"type": "string"}},
                        "premier_commentaire": {"type": "string"},
                        "texte_alternatif": {"type": "string"},
                        "titre": {"type": "string"},
                    },
                    "required": ["reseau", "legende", "hashtags", "premier_commentaire",
                                 "texte_alternatif", "titre"],
                    "additionalProperties": False,
                },
            },
            "alerte": {"type": "string"},
        },
        "required": ["variantes", "alerte"],
        "additionalProperties": False,
    }
    prompt = f"""Rédige une déclinaison par réseau pour ce contenu.

Type de contenu : {kind}
Titre interne : {title}
Thème de campagne : {pillar or 'non précisé'}
Brief de l'équipe : {brief or 'aucun'}
Comptes à mentionner / collaborateurs : {collaborators or 'aucun'}

Texte source :
{source_text or '(aucun)'}

Transcription de la vidéo :
{transcript or '(aucune)'}

Réseaux et contraintes :
{chr(10).join(specs)}

Règles :
- Adapte l'écriture à chaque réseau (LinkedIn : plus argumenté, aéré ;
  Instagram : accroche forte en première ligne ; X : une idée, percutant ;
  TikTok/Shorts : accroche orale ; Facebook : chaleureux et clair).
- Première ligne = accroche (capitales possibles pour une accroche courte).
- Les hashtags sont renvoyés à part (sans les répéter dans la légende), avec le « # ».
- premier_commentaire : complément utile (lien du site, appel à l'action) ou vide.
- texte_alternatif : description factuelle du visuel pour l'accessibilité.
- titre : uniquement pour youtube_shorts, sinon chaîne vide.
- alerte : vide si tout est conforme, sinon explique ce que tu as corrigé."""
    content = _images_content(images or []) + [{"type": "text", "text": prompt}]
    return _call(cfg, _system(campaign, brand), content, schema)


def carrousel(cfg, campaign, brand, *, text: str, title: str, nb_max: int = 8, brief: str = "") -> dict:
    schema = {
        "type": "object",
        "properties": {
            "diapositives": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string", "enum": ["couverture", "contenu", "chiffre", "citation", "cloture"]},
                        "surtitre": {"type": "string"},
                        "titre": {"type": "string"},
                        "texte": {"type": "string"},
                        "chiffre": {"type": "string"},
                        "auteur": {"type": "string"},
                        "fond": {"type": "string", "enum": ["anthracite", "ivoire"]},
                    },
                    "required": ["type", "surtitre", "titre", "texte", "chiffre", "auteur", "fond"],
                    "additionalProperties": False,
                },
            },
            "alerte": {"type": "string"},
        },
        "required": ["diapositives", "alerte"],
        "additionalProperties": False,
    }
    prompt = f"""Transforme ce texte en carrousel de {3}-{nb_max} diapositives.

Titre interne : {title}
Brief : {brief or 'aucun'}
Texte :
{text}

Charte éditoriale du carrousel :
- Diapo 1 « couverture » : titre court en CAPITALES, fort, 3 à 8 mots ;
  surtitre = petit titre (ex. « PROGRAMME », « ENGAGEMENT 01 »).
- Diapos « contenu » : un titre court (≤ 8 mots) + texte ≤ 260 caractères.
- « chiffre » si un chiffre réel du texte mérite d'être mis en avant
  (champ chiffre, ex. « 2028 », « 30 % ») — n'invente jamais de chiffre.
- « citation » pour une phrase des candidates présente dans le texte (auteur renseigné).
- Dernière diapo « cloture » : texte d'appel sobre (ex. renvoi vers le site) ;
  laisse titre vide (la hiérarchie ÉCRIVONS / ENSEMBLE / LE BARREAU DE DEMAIN est ajoutée).
- Encadre de ** les mots importants, dates et chiffres (ils passent en bronze),
  un à trois par diapo maximum.
- fond : « anthracite » par défaut ; « ivoire » au plus pour une diapo de respiration.
- Champs non utilisés = chaîne vide."""
    return _call(cfg, _system(campaign, brand), prompt, schema)


def corriger_sous_titres(cfg, campaign, brand, segments: list[dict]) -> list[dict]:
    if not segments:
        return segments
    schema = {
        "type": "object",
        "properties": {
            "segments": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"i": {"type": "integer"}, "texte": {"type": "string"}},
                    "required": ["i", "texte"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["segments"],
        "additionalProperties": False,
    }
    lines = "\n".join(f"{i}\t{s['text']}" for i, s in enumerate(segments))
    prompt = f"""Corrige ces sous-titres issus d'une transcription automatique :
ponctuation, majuscules, accents, orthographe des noms propres (glossaire),
suppression des hésitations (euh, ben). Ne reformule pas le propos, ne fusionne
pas et ne supprime pas de lignes : renvoie exactement une entrée par index.
Encadre de ** au plus un mot clé ou chiffre important par ligne.

{lines}"""
    data = _call(cfg, _system(campaign, brand), prompt, schema)
    fixed = {d["i"]: d["texte"] for d in data.get("segments", [])}
    return [{**s, "text": fixed.get(i, s["text"])} for i, s in enumerate(segments)]


def choisir_sequence(cfg, campaign, brand, transcript: str, duree_cible: int, duree_totale: float) -> dict:
    schema = {
        "type": "object",
        "properties": {
            "debut": {"type": "number"},
            "fin": {"type": "number"},
            "raison": {"type": "string"},
        },
        "required": ["debut", "fin", "raison"],
        "additionalProperties": False,
    }
    prompt = f"""Voici la transcription horodatée (secondes) d'une vidéo de {duree_totale:.0f} s.
Choisis LA séquence continue d'environ {duree_cible} s (tolérance ±30 %) la plus
forte pour un format court sur les réseaux : idée complète, phrase d'accroche au
début, fin sur une phrase terminée. Les bornes doivent tomber sur des débuts/fins
de segments.

{transcript}"""
    data = _call(cfg, _system(campaign, brand), prompt, schema)
    debut = max(0.0, float(data["debut"]))
    fin = min(float(data["fin"]), duree_totale)
    if fin <= debut:
        debut, fin = 0.0, min(duree_cible, duree_totale)
    return {"debut": debut, "fin": fin, "raison": data.get("raison", "")}
