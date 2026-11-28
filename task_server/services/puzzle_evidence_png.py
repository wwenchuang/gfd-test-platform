"""Render a shareable PNG from the public fields of one preset request and response."""
import io
import json
import shutil
import subprocess
import textwrap
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont, ImageOps, PngImagePlugin


@lru_cache(maxsize=1)
def _font_path():
    candidates = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/google-noto-cjk/NotoSansCJK-Regular.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
    ]
    if shutil.which("fc-list"):
        try:
            result = subprocess.run(["fc-list", ":lang=zh-cn", "--format=%{file}\\n"],
                                    capture_output=True, timeout=2, check=True)
            candidates.extend(result.stdout.decode("utf-8", errors="replace").splitlines())
        except (OSError, subprocess.SubprocessError):
            pass
    for path in candidates:
        try:
            ImageFont.truetype(path, 20)
            return path
        except OSError:
            continue
    return None


def render_evidence(request, entry, index, resource_bytes=None):
    """Render only the captured fields. Keep JSON text copyable in PNG metadata."""
    font_path = _font_path()
    font = ImageFont.truetype(font_path, 22) if font_path else ImageFont.load_default(size=22)
    heading = ImageFont.truetype(font_path, 30) if font_path else ImageFont.load_default(size=30)
    rows = [(f"{request.get('method') or '—'}  {request.get('url') or '—'}", False)]
    for key, label in (("queried_at", "Query time"), ("request_id", "Request ID"),
                       ("response_request_id", "Response request ID"), ("http", "HTTP"),
                       ("business_code", "Business code"), ("count", "Preset count")):
        value = request.get(key)
        if value is not None and value != "":
            rows.append((f"{label}   {value}", False))

    if entry is not None and index is not None:
        rows.append((f"Response excerpt   ·   data[{index}]", True))
        fields = {k: entry[k] for k in ("name", "imageUrl") if k in entry}
        rows.extend((line, False) for line in json.dumps(fields, ensure_ascii=False, indent=2).splitlines())
    else:
        rows.append(("No matching response entry recorded", True))
    thumbnail = None
    if entry and resource_bytes:
        try:
            with Image.open(io.BytesIO(resource_bytes)) as original:
                if original.width * original.height <= 16_000_000:
                    original.thumbnail((320, 200))
                    thumbnail = ImageOps.exif_transpose(original).convert("RGBA")
        except (OSError, ValueError, Image.DecompressionBombError):
            pass
    if thumbnail:
        rows.append(("Image from imageUrl", True))
    width, margin = 1080, 40
    lines = []
    for text, section in rows:
        if section:
            lines.append(("", False))
        # JSON escaping preserves non-Latin values on hosts without any CJK fonts.
        display = text if font_path else text.encode("ascii", errors="backslashreplace").decode()
        for part in textwrap.wrap(display, width=80, replace_whitespace=False, drop_whitespace=False) or [""]:
            while font.getlength(part) > width - 2 * margin:
                length = max(1, int(len(part) * (width - 2 * margin) / font.getlength(part)) - 1)
                lines.append((part[:length], section))
                part = part[length:]
            lines.append((part, section))
    height = 112 + len(lines) * 33 + (thumbnail.height + 12 if thumbnail else 0) + margin
    image = Image.new("RGB", (width, height), "#f5f8f7")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, width, 78), fill="#173e37")
    draw.text((margin, 19), "Puzzle presets   /   API evidence", font=heading, fill="white")
    y = 100
    for text, section in lines:
        draw.text((margin, y), text, font=font, fill="#136354" if section else "#172d29")
        y += 33
    if thumbnail:
        image.paste(thumbnail, (margin, y + 4), mask=thumbnail.getchannel("A"))
    metadata = PngImagePlugin.PngInfo()
    metadata.add_itxt("Description", "\n".join(line for line, _ in rows))
    output = io.BytesIO()
    image.save(output, format="PNG", pnginfo=metadata)
    return output.getvalue()
