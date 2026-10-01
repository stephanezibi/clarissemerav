"""
app/brand.py — Charte graphique SURIN / GRIGUER · PARIS · BÂTONNAT 2028.

Source : charte « réseaux sociaux » de la campagne (alignée sur
batonnatsuringriguer.com). Les valeurs ci-dessous sont les valeurs par
défaut ; elles restent ajustables depuis Réglages → Charte, mais la charte
demande d'utiliser EXCLUSIVEMENT ces couleurs et cette typographie.
"""

from __future__ import annotations

from pathlib import Path

FONTS_DIR = Path(__file__).parent / "static" / "fonts"

DEFAULT_BRAND = {
    "anthracite": "#393736",   # fond principal
    "ivoire": "#EEEAE3",       # fond clair / texte principal sur anthracite
    "bronze": "#BF9C75",       # accent : mots importants, dates, chiffres, filets
    "secondaire": "#C3B9AD",   # texte secondaire clair
    "font_regular": "Montserrat-Regular.ttf",
    "font_bold": "Montserrat-Bold.ttf",
    "signature_1": "SURIN / GRIGUER",
    "signature_2": "PARIS · BÂTONNAT 2028",
    "accroche": "PARIS. 2028. ENSEMBLE.",
    "site": "batonnatsuringriguer.com",
    "logo_path": "",           # logo optionnel (PNG transparent), téléversé en Réglages
    "watermark": True,         # signature incrustée sur photos et vidéos
}

DEFAULT_CAMPAIGN = {
    "nom": "Surin / Griguer — Bâtonnat de Paris 2028",
    "candidats": "Clarisse Surin et Merav Griguer, candidates au Bâtonnat de Paris (mandat 2028).",
    "site": "https://batonnatsuringriguer.com",
    "ton": (
        "Éditorial, institutionnel, élégant, contemporain, sobre, premium. "
        "Phrases courtes, vocabulaire précis, aucune familiarité, pas d'emojis criards "
        "(au plus un symbole sobre si utile)."
    ),
    "messages_cles": (
        "PARIS. 2028. ENSEMBLE.\n"
        "Écrivons ensemble le Barreau de demain."
    ),
    "piliers": "Programme, Terrain, Rencontres, Presse, Engagements, Coulisses",
    "hashtags_defaut": "#Batonnat2028 #BarreauDeParis #Avocats #SurinGriguer",
    "glossaire": "Clarisse Surin, Merav Griguer, Bâtonnat, Bâtonnier, Bâtonnière, Barreau de Paris, Ordre, Conseil de l'Ordre, CNB, RIN",
    "interdits": (
        "Aucun dénigrement des autres candidats ; aucune promesse contraire aux "
        "règles ordinales ; aucun nom de client ni information couverte par le "
        "secret professionnel ; pas de photo de confrère sans son accord."
    ),
}

# Hiérarchie de référence pour les grands visuels (ivoire / ivoire / bronze)
HERO_LINES = [
    ("ÉCRIVONS", "ivoire"),
    ("ENSEMBLE", "ivoire"),
    ("LE BARREAU\nDE DEMAIN", "bronze"),
]


def font_path(brand: dict, bold: bool = False) -> Path:
    """Chemin de la police de la charte (Montserrat), avec repli système."""
    name = brand.get("font_bold" if bold else "font_regular") or ""
    p = Path(name)
    if not p.is_absolute():
        p = FONTS_DIR / name
    if p.exists():
        return p
    fallback = FONTS_DIR / ("Montserrat-Bold.ttf" if bold else "Montserrat-Regular.ttf")
    return fallback


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    v = value.lstrip("#")
    return tuple(int(v[i:i + 2], 16) for i in (0, 2, 4))


def ass_color(value: str, alpha: int = 0) -> str:
    """Couleur au format ASS (&HAABBGGRR) pour les sous-titres libass."""
    r, g, b = hex_to_rgb(value)
    return f"&H{alpha:02X}{b:02X}{g:02X}{r:02X}"
