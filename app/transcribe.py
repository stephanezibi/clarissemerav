"""
app/transcribe.py — Transcription audio → sous-titres horodatés.

Utilise faster-whisper (modèle exécuté sur le serveur, gratuit, aucune donnée
envoyée à un tiers). Si le paquet n'est pas installé, l'appli le signale et
l'équipe peut téléverser un fichier .srt à la place.
"""

from __future__ import annotations

import re
from pathlib import Path

_model = None


def available() -> bool:
    try:
        import faster_whisper  # noqa: F401

        return True
    except ImportError:
        return False


def transcribe(audio_path, model_size: str = "small", language: str = "fr") -> list[dict]:
    global _model
    from faster_whisper import WhisperModel

    if _model is None:
        _model = WhisperModel(model_size, device="cpu", compute_type="int8")
    segments, _info = _model.transcribe(
        str(audio_path), language=language, vad_filter=True, word_timestamps=False
    )
    return [{"start": s.start, "end": s.end, "text": s.text.strip()} for s in segments]


def split_long(segments: list[dict], max_chars: int = 42) -> list[dict]:
    """Découpe les segments trop longs pour des sous-titres lisibles sur mobile
    (≈ 2 lignes courtes), en répartissant la durée au prorata des caractères."""
    out = []
    for seg in segments:
        words = seg["text"].split()
        if len(seg["text"]) <= max_chars or not words:
            out.append(seg)
            continue
        chunks, cur = [], []
        for w in words:
            if cur and len(" ".join(cur + [w])) > max_chars:
                chunks.append(" ".join(cur))
                cur = []
            cur.append(w)
        if cur:
            chunks.append(" ".join(cur))
        total = sum(len(c) for c in chunks) or 1
        t = seg["start"]
        dur = seg["end"] - seg["start"]
        for c in chunks:
            d = dur * len(c) / total
            out.append({"start": t, "end": t + d, "text": c})
            t += d
    return out


def shift(segments: list[dict], start: float, end: float | None) -> list[dict]:
    """Recale les sous-titres sur une séquence coupée [start, end]."""
    out = []
    for s in segments:
        if s["end"] <= start or (end is not None and s["start"] >= end):
            continue
        a = max(s["start"], start) - start
        b = (min(s["end"], end) if end is not None else s["end"]) - start
        out.append({"start": a, "end": b, "text": s["text"]})
    return out


_SRT_TIME = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)")


def _t(s: str) -> float:
    h, m, sec, ms = _SRT_TIME.match(s.strip()).groups()
    return int(h) * 3600 + int(m) * 60 + int(sec) + int(ms.ljust(3, "0")[:3]) / 1000


def parse_srt(text: str) -> list[dict]:
    out = []
    for block in re.split(r"\n\s*\n", text.replace("\r", "").strip()):
        lines = [l for l in block.split("\n") if l.strip()]
        idx = next((i for i, l in enumerate(lines) if "-->" in l), None)
        if idx is None:
            continue
        a, b = lines[idx].split("-->")
        out.append({"start": _t(a), "end": _t(b.split()[0]), "text": " ".join(lines[idx + 1:])})
    return out


def as_text(segments: list[dict]) -> str:
    return "\n".join(f"[{s['start']:.1f}-{s['end']:.1f}] {s['text']}" for s in segments)


def load_srt(path) -> list[dict]:
    return parse_srt(Path(path).read_text(encoding="utf-8", errors="replace"))
