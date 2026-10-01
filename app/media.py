"""
app/media.py — Traitement des médias : recadrage, sous-titres, carrousels.

Tout est fait avec ffmpeg (vidéo) et Pillow (images), en respectant la charte :
fonds anthracite, texte ivoire, accents bronze, Montserrat 400/700, aucun
dégradé ni ombre lourde.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from app.brand import FONTS_DIR, ass_color, font_path, hex_to_rgb
from app.networks import FORMATS

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = shutil.which("ffprobe") or "ffprobe"

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".gif", ".bmp", ".tif", ".tiff"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm", ".3gp"}


class MediaError(RuntimeError):
    pass


def is_image(path) -> bool:
    return Path(path).suffix.lower() in IMAGE_EXT


def is_video(path) -> bool:
    return Path(path).suffix.lower() in VIDEO_EXT


def _run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise MediaError(f"ffmpeg a échoué : {proc.stderr[-800:]}")


# ===================================================================== #
# Vidéo
# ===================================================================== #

def probe(path) -> dict:
    proc = subprocess.run(
        [FFPROBE, "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise MediaError(f"Fichier vidéo illisible : {proc.stderr[-300:]}")
    data = json.loads(proc.stdout or "{}")
    v = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
    has_audio = any(s.get("codec_type") == "audio" for s in data.get("streams", []))
    w, h = int(v.get("width", 0)), int(v.get("height", 0))
    rotation = 0
    for sd in v.get("side_data_list", []) or []:
        if "rotation" in sd:
            rotation = abs(int(float(sd["rotation"])))
    if rotation in (90, 270):
        w, h = h, w
    return {
        "duration": float(data.get("format", {}).get("duration", 0) or 0),
        "width": w,
        "height": h,
        "has_audio": has_audio,
    }


def trim_video(src, dst, start: float, end: float | None) -> Path:
    """Coupe une séquence [start, end] (réencodage pour une coupe précise)."""
    cmd = [FFMPEG, "-y", "-ss", f"{max(start, 0):.3f}", "-i", str(src)]
    if end is not None and end > start:
        cmd += ["-t", f"{end - start:.3f}"]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
            "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(dst)]
    _run(cmd)
    return Path(dst)


def extract_audio(src, dst) -> Path:
    _run([FFMPEG, "-y", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000", str(dst)])
    return Path(dst)


def video_thumbnail(src, dst, at: float = 1.0) -> Path:
    _run([FFMPEG, "-y", "-ss", f"{at:.2f}", "-i", str(src), "-frames:v", "1",
          "-vf", "scale=720:-2", str(dst)])
    return Path(dst)


def _fmt_ts_ass(t: float) -> str:
    t = max(t, 0)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def _fmt_ts_srt(t: float) -> str:
    t = max(t, 0)
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(segments: list[dict], path) -> Path:
    lines = []
    for i, seg in enumerate(segments, 1):
        lines += [str(i), f"{_fmt_ts_srt(seg['start'])} --> {_fmt_ts_srt(seg['end'])}", seg["text"].strip(), ""]
    Path(path).write_text("\n".join(lines), encoding="utf-8")
    return Path(path)


_HIGHLIGHT = re.compile(r"(\*\*[^*]+\*\*|\b\d[\d\s.,:/%h€]*\d\b|\b\d+\b)")


def _ass_text(text: str, bronze: str, base: str) -> str:
    """Chiffres, dates et **mots marqués** passent en bronze (charte)."""
    text = text.replace("\n", " ").replace("{", "(").replace("}", ")").strip()

    def repl(m):
        word = m.group(0).strip("*")
        return "{\\c" + bronze + "}" + word + "{\\c" + base + "}"

    return _HIGHLIGHT.sub(repl, text)


def write_ass(segments: list[dict], path, size: tuple[int, int], brand: dict) -> Path:
    """Sous-titres stylés charte : Montserrat Bold, ivoire sur cartouche
    anthracite (sans ombre), accents bronze, placés au-dessus des zones
    d'interface des formats verticaux."""
    w, h = size
    vertical = h > w
    fontsize = int(w * (0.058 if vertical else 0.034))
    margin_v = int(h * (0.25 if vertical else 0.07))
    ivoire = ass_color(brand["ivoire"])
    bronze = ass_color(brand["bronze"])
    plate = ass_color(brand["anthracite"], alpha=0x30)
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Charte,Montserrat,{fontsize},{ivoire},{ivoire},{plate},{plate},-1,0,0,0,100,100,0,0,3,{max(6, fontsize // 4)},0,2,{int(w*0.08)},{int(w*0.08)},{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = [
        f"Dialogue: 0,{_fmt_ts_ass(s['start'])},{_fmt_ts_ass(s['end'])},Charte,,0,0,0,,"
        f"{_ass_text(s['text'], bronze, ivoire)}"
        for s in segments if s.get("text", "").strip()
    ]
    Path(path).write_text(header + "\n".join(events) + "\n", encoding="utf-8")
    return Path(path)


def _ff_escape(path: Path) -> str:
    return str(path).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def reformat_video(
    src,
    dst,
    fmt: str,
    brand: dict,
    mode: str = "flou",
    segments: list[dict] | None = None,
    watermark: bool = True,
    max_seconds: float | None = None,
) -> Path:
    """Recadre la vidéo au format cible (9:16, 4:5, 1:1, 16:9…).

    mode « flou »  : la vidéo entière, fond = la même vidéo floutée (sobre)
    mode « aplat » : la vidéo entière sur aplat anthracite
    mode « recadre » : remplissage plein cadre (coupe centrée)
    """
    W, H = FORMATS[fmt]
    info = probe(src)
    anthracite = brand["anthracite"].lstrip("#")
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        inputs = ["-i", str(src)]
        if mode == "recadre":
            chain = (f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,"
                     f"crop={W}:{H},setsar=1[base]")
        elif mode == "aplat":
            chain = (f"color=c=0x{anthracite}:s={W}x{H}:r=30[bg];"
                     f"[0:v]scale={W}:{H}:force_original_aspect_ratio=decrease,setsar=1[fg];"
                     f"[bg][fg]overlay=(W-w)/2:(H-h)/2:shortest=1[base]")
        else:
            chain = (f"[0:v]split=2[a][b];"
                     f"[a]scale={W // 4}:{H // 4}:force_original_aspect_ratio=increase,"
                     f"crop={W // 4}:{H // 4},boxblur=12:2,scale={W}:{H},"
                     f"eq=brightness=-0.12:saturation=0.6[bg];"
                     f"[b]scale={W}:{H}:force_original_aspect_ratio=decrease,setsar=1[fg];"
                     f"[bg][fg]overlay=(W-w)/2:(H-h)/2[base]")
        last = "base"
        if watermark:
            sig = signature_png(brand, min(W, H), tmp / "signature.png")
            inputs += ["-i", str(sig)]
            margin = int(min(W, H) * 0.045)
            # vertical : en bas, au-dessus des boutons de l'appli ; sinon en haut
            # à gauche pour laisser le bas de l'image aux sous-titres
            y = f"H-h-{int(H * 0.14)}" if H > W * 1.5 else str(margin)
            chain += f";[{last}][1:v]overlay={margin}:{y}[wm]"
            last = "wm"
        if segments:
            ass = write_ass(segments, tmp / "subs.ass", (W, H), brand)
            chain += f";[{last}]ass='{_ff_escape(ass)}':fontsdir='{_ff_escape(FONTS_DIR)}'[subs]"
            last = "subs"
        cmd = [FFMPEG, "-y", *inputs, "-filter_complex", chain + f";[{last}]fps=30,format=yuv420p[out]",
               "-map", "[out]"]
        if info["has_audio"]:
            cmd += ["-map", "0:a:0", "-c:a", "aac", "-b:a", "160k"]
        if max_seconds:
            cmd += ["-t", f"{max_seconds:.2f}"]
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                "-movflags", "+faststart", "-shortest", str(dst)]
        _run(cmd)
    return Path(dst)


# ===================================================================== #
# Typographie (Pillow)
# ===================================================================== #

def _font(brand: dict, size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(font_path(brand, bold)), size)


def _tracked_width(text: str, font, tracking: float) -> int:
    if not text:
        return 0
    return int(sum(font.getlength(c) for c in text) + tracking * (len(text) - 1))


def draw_tracked(draw: ImageDraw.ImageDraw, xy, text: str, font, fill, tracking: float = 0) -> int:
    """Texte avec espacement entre lettres (petits titres, signatures)."""
    x, y = xy
    if not tracking:
        draw.text((x, y), text, font=font, fill=fill)
        return int(font.getlength(text))
    for c in text:
        draw.text((x, y), c, font=font, fill=fill)
        x += font.getlength(c) + tracking
    return int(x - xy[0] - tracking)


def _rich_tokens(text: str) -> list[tuple[str, bool]]:
    """Découpe « mots **importants** » en [(mot, accent?)]."""
    out: list[tuple[str, bool]] = []
    for part in re.split(r"(\*\*[^*]+\*\*)", text):
        if not part:
            continue
        accent = part.startswith("**") and part.endswith("**")
        for word in part.strip("*").split():
            out.append((word, accent))
    return out


def _wrap_rich(tokens, font, max_w: int) -> list[list[tuple[str, bool]]]:
    lines, cur, cur_w = [], [], 0.0
    space = font.getlength(" ")
    for word, accent in tokens:
        if word == "\n":
            lines.append(cur)
            cur, cur_w = [], 0.0
            continue
        w = font.getlength(word)
        if cur and cur_w + space + w > max_w:
            lines.append(cur)
            cur, cur_w = [], 0.0
        cur.append((word, accent))
        cur_w += (space if cur_w else 0) + w
    if cur:
        lines.append(cur)
    return lines


def draw_rich(draw, xy, text: str, font, max_w: int, color, accent_color,
              leading: float = 1.25, align: str = "left", max_lines: int | None = None) -> int:
    """Paragraphe avec mots en bronze ; renvoie la hauteur occupée."""
    tokens: list[tuple[str, bool]] = []
    for i, para in enumerate(text.split("\n")):
        if i:
            tokens.append(("\n", False))
        tokens += _rich_tokens(para)
    lines = _wrap_rich(tokens, font, max_w)
    if max_lines:
        lines = lines[:max_lines]
    x0, y = xy
    asc, desc = font.getmetrics()
    lh = int((asc + desc) * leading)
    space = font.getlength(" ")
    for line in lines:
        line_w = sum(font.getlength(w) for w, _ in line) + space * max(len(line) - 1, 0)
        x = x0 + (max_w - line_w) / 2 if align == "center" else x0
        for word, accent in line:
            draw.text((x, y), word, font=font, fill=accent_color if accent else color)
            x += font.getlength(word) + space
        y += lh
    return y - xy[1]


def _fit_font(brand, text: str, max_w: int, max_h: int, start: int, bold=True, leading=1.0, minimum=24):
    size = start
    while size > minimum:
        f = _font(brand, size, bold)
        lines = _wrap_rich([(w, False) for w in text.split()], f, max_w)
        asc, desc = f.getmetrics()
        if len(lines) * (asc + desc) * leading <= max_h and all(
            sum(f.getlength(w) for w, _ in ln) <= max_w for ln in lines
        ):
            return f
        size -= 4
    return _font(brand, minimum, bold)


def signature_png(brand: dict, frame_w: int, dst) -> Path:
    """Cartouche de signature : SURIN / GRIGUER + filet bronze + PARIS · BÂTONNAT 2028."""
    s1, s2 = brand["signature_1"].upper(), brand["signature_2"].upper()
    f1 = _font(brand, max(18, int(frame_w * 0.024)), bold=True)
    f2 = _font(brand, max(12, int(frame_w * 0.0145)))
    t1, t2 = f1.size * 0.28, f2.size * 0.35
    pad = int(f1.size * 0.75)
    w = max(_tracked_width(s1, f1, t1), _tracked_width(s2, f2, t2)) + 2 * pad
    h = int(f1.size * 1.5 + f2.size * 1.4 + f1.size * 0.6 + 2 * pad)
    img = Image.new("RGBA", (w, h), hex_to_rgb(brand["anthracite"]) + (225,))
    d = ImageDraw.Draw(img)
    y = pad
    draw_tracked(d, (pad, y), s1, f1, hex_to_rgb(brand["ivoire"]), t1)
    y += int(f1.size * 1.5)
    d.line([(pad, y), (pad + int(f1.size * 2.2), y)], fill=hex_to_rgb(brand["bronze"]), width=max(2, f1.size // 12))
    y += int(f1.size * 0.45)
    draw_tracked(d, (pad, y), s2, f2, hex_to_rgb(brand["secondaire"]), t2)
    if brand.get("logo_path") and Path(brand["logo_path"]).exists():
        try:
            logo = Image.open(brand["logo_path"]).convert("RGBA")
            logo.thumbnail((h - 2 * pad, h - 2 * pad))
            canvas = Image.new("RGBA", (w + logo.width + pad, h), img.getpixel((0, 0)))
            canvas.paste(logo, (pad, (h - logo.height) // 2), logo)
            canvas.paste(img, (logo.width + pad, 0))
            img = canvas
        except Exception:
            pass
    img.save(dst)
    return Path(dst)


# ===================================================================== #
# Photos
# ===================================================================== #

def open_image(path) -> Image.Image:
    try:
        if Path(path).suffix.lower() in {".heic", ".heif"}:
            from pillow_heif import register_heif_opener  # type: ignore

            register_heif_opener()
    except ImportError:
        pass
    img = Image.open(path)
    img = ImageOps.exif_transpose(img)
    return img.convert("RGB")


def reformat_image(src, dst, fmt: str, brand: dict, mode: str = "flou", watermark: bool = True) -> Path:
    W, H = FORMATS[fmt]
    img = open_image(src)
    if mode == "recadre":
        out = ImageOps.fit(img, (W, H), Image.LANCZOS, centering=(0.5, 0.4))
    else:
        if mode == "aplat":
            out = Image.new("RGB", (W, H), hex_to_rgb(brand["anthracite"]))
        else:
            out = ImageOps.fit(img, (W // 4, H // 4), Image.LANCZOS)
            out = out.filter(ImageFilter.GaussianBlur(6)).resize((W, H), Image.LANCZOS)
            dim = Image.new("RGB", (W, H), hex_to_rgb(brand["anthracite"]))
            out = Image.blend(out, dim, 0.45)
        fg = ImageOps.contain(img, (W, H), Image.LANCZOS)
        out.paste(fg, ((W - fg.width) // 2, (H - fg.height) // 2))
    if watermark:
        with tempfile.TemporaryDirectory() as tmp:
            sig = Image.open(signature_png(brand, min(W, H), Path(tmp) / "s.png")).convert("RGBA")
            margin = int(min(W, H) * 0.045)
            y = H - sig.height - (int(H * 0.14) if H > W * 1.5 else margin)
            out.paste(sig, (margin, y), sig)
    out.save(dst, quality=92)
    return Path(dst)


# ===================================================================== #
# Carrousels texte (charte)
# ===================================================================== #

def render_slide(slide: dict, idx: int, total: int, fmt: str, brand: dict, dst) -> Path:
    """Rend une diapositive. Types : couverture | contenu | chiffre | citation | cloture.
    Les **mots marqués** passent en bronze."""
    W, H = FORMATS[fmt]
    anth, ivo = hex_to_rgb(brand["anthracite"]), hex_to_rgb(brand["ivoire"])
    bronze, sec = hex_to_rgb(brand["bronze"]), hex_to_rgb(brand["secondaire"])
    light = slide.get("fond") == "ivoire"
    bg, fg, fg2 = (ivo, anth, anth) if light else (anth, ivo, sec)
    img = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(img)
    m = int(W * 0.085)
    inner = W - 2 * m
    kind = slide.get("type", "contenu")
    unit = W / 1080

    # En-tête : petit titre espacé en bronze + filet
    kicker = (slide.get("surtitre") or "").upper()
    vertical = H / W > 1.5  # 9:16 : zones de sécurité stories / reels (haut et bas)
    y = int(H * (0.17 if vertical else 0.09))
    if kicker:
        fk = _font(brand, int(26 * unit), bold=True)
        draw_tracked(d, (m, y), kicker, fk, bronze, fk.size * 0.32)
        y += int(fk.size * 1.9)
    d.line([(m, y), (m + int(90 * unit), y)], fill=bronze, width=max(3, int(4 * unit)))
    y += int(60 * unit)

    footer_h = int(H * (0.2 if vertical else 0.12))
    avail_h = H - y - footer_h - int(40 * unit)

    if kind == "cloture":
        lines = slide.get("lignes") or [
            {"texte": t, "couleur": c} for t, c in
            [("ÉCRIVONS", "ivoire"), ("ENSEMBLE", "ivoire"), ("LE BARREAU DE DEMAIN", "bronze")]
        ]
        size = int(132 * unit)
        f = _font(brand, size, bold=True)
        while size > 40 and any(f.getlength(w) > inner for ln in lines for w in ln["texte"].split()):
            size -= 6
            f = _font(brand, size, bold=True)
        yy = y + int(avail_h * 0.08)
        for ln in lines:
            color = bronze if ln.get("couleur") == "bronze" else fg
            for row in _wrap_rich([(w, False) for w in ln["texte"].upper().split()], f, inner):
                d.text((m, yy), " ".join(w for w, _ in row), font=f, fill=color)
                yy += int(size * 0.98)
            yy += int(size * 0.18)
        if slide.get("texte"):
            ft = _font(brand, int(34 * unit))
            draw_rich(d, (m, yy + int(30 * unit)), slide["texte"], ft, inner, fg2, bronze, 1.35)
    elif kind == "chiffre":
        big = slide.get("chiffre", "")
        f = _fit_font(brand, big, inner, int(avail_h * 0.45), int(300 * unit))
        d.text((m, y), big, font=f, fill=bronze)
        asc, desc = f.getmetrics()
        yy = y + asc + desc + int(20 * unit)
        ft = _font(brand, int(50 * unit), bold=True)
        yy += draw_rich(d, (m, yy), (slide.get("titre") or "").upper(), ft, inner, fg, bronze, 1.05)
        if slide.get("texte"):
            fb = _font(brand, int(34 * unit))
            draw_rich(d, (m, yy + int(24 * unit)), slide["texte"], fb, inner, fg2, bronze, 1.4)
    elif kind == "citation":
        fq = _font(brand, int(180 * unit), bold=True)
        d.text((m, y - int(40 * unit)), "«", font=fq, fill=bronze)
        yy = y + int(140 * unit)
        ft = _fit_font(brand, slide.get("texte", ""), inner, int(avail_h * 0.6), int(64 * unit), bold=True, leading=1.2)
        yy += draw_rich(d, (m, yy), slide.get("texte", ""), ft, inner, fg, bronze, 1.2)
        if slide.get("auteur"):
            fa = _font(brand, int(26 * unit), bold=True)
            draw_tracked(d, (m, yy + int(30 * unit)), slide["auteur"].upper(), fa, bronze, fa.size * 0.3)
    else:
        title = (slide.get("titre") or "").upper()
        is_cover = kind == "couverture"
        start = int((120 if is_cover else 88) * unit)
        title_box_h = int(avail_h * (0.75 if is_cover else 0.4))
        plain = re.sub(r"\*\*", "", title)
        ft = _fit_font(brand, plain, inner, title_box_h, start, bold=True, leading=1.0)
        yy = y + draw_rich(d, (m, y), title, ft, inner, fg, bronze, leading=1.0)
        body = slide.get("texte", "")
        if body:
            yy += int(36 * unit)
            fb = _font(brand, int((38 if is_cover else 40) * unit))
            draw_rich(d, (m, yy), body, fb, inner, fg2 if is_cover else fg, bronze, 1.4,
                      max_lines=max(3, int((H - yy - footer_h) / (fb.size * 1.4))))

    # Pied : signature espacée + pagination bronze
    fs = _font(brand, int(22 * unit), bold=True)
    yb = H - footer_h + int(30 * unit)
    d.line([(m, yb - int(24 * unit)), (W - m, yb - int(24 * unit))], fill=sec if not light else bronze, width=1)
    draw_tracked(d, (m, yb), brand["signature_1"].upper(), fs, fg, fs.size * 0.3)
    fs2 = _font(brand, int(18 * unit))
    draw_tracked(d, (m, yb + int(34 * unit)), brand["signature_2"].upper(), fs2, fg2, fs2.size * 0.35)
    if total > 1:
        pg = f"{idx:02d} / {total:02d}"
        pw = _tracked_width(pg, fs, fs.size * 0.2)
        draw_tracked(d, (W - m - pw, yb), pg, fs, bronze, fs.size * 0.2)
        if idx < total:
            fa = _font(brand, int(40 * unit), bold=True)
            d.text((W - m - fa.getlength("→"), yb + int(30 * unit)), "→", font=fa, fill=bronze)
    img.save(dst, quality=95)
    return Path(dst)


def render_carousel(slides: list[dict], fmt: str, brand: dict, out_dir: Path, prefix: str) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    total = len(slides)
    return [
        render_slide(s, i, total, fmt, brand, out_dir / f"{prefix}_{i:02d}.jpg")
        for i, s in enumerate(slides, 1)
    ]


def images_to_pdf(images: list[Path], dst: Path) -> Path:
    """Carrousel LinkedIn = document PDF (une diapo par page)."""
    pages = [Image.open(p).convert("RGB") for p in images]
    if not pages:
        raise MediaError("Aucune diapositive à assembler.")
    pages[0].save(dst, save_all=True, append_images=pages[1:], resolution=150)
    return dst


def image_for_vision(path, max_side: int = 1568) -> bytes:
    """JPEG réduit pour l'analyse d'image par Claude."""
    import io

    img = open_image(path)
    img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return buf.getvalue()
