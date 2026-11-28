"""Locate a preset's artwork independently in two mobile screenshots using Pillow."""
import hashlib
import io

from PIL import Image, ImageChops, ImageFilter, ImageOps, ImageStat


def _decode(data):
    with Image.open(io.BytesIO(data)) as image:
        if min(image.size) < 12 or image.width * image.height > 16_000_000:
            raise ValueError("unsupported image dimensions")
        image = ImageOps.exif_transpose(image)
        if "A" in image.getbands() or "transparency" in image.info:
            rgba = image.convert("RGBA")
            base = Image.new("RGB", image.size, "white")
            base.paste(rgba, mask=rgba.getchannel("A"))
            return base
        return image.convert("RGB")


def _difference(a, b):
    return sum(ImageStat.Stat(ImageChops.difference(a, b)).mean) / 3


def _coarse_boxes(screen, template, card_width):
    # Resize the whole screen so each putative card is the size of the template.
    # Compare 16 spatially distributed colour samples at every possible origin.
    tw, th = template.size
    scale = tw / card_width
    small = screen.resize((round(screen.width * scale), round(screen.height * scale)), Image.Resampling.BILINEAR)
    cols, rows = small.width - tw + 1, small.height - th + 1
    if min(cols, rows) < 1:
        return
    maps = []
    for j in range(4):
        for i in range(4):
            x, y = int((i + .5) * tw / 4), int((j + .5) * th / 4)
            sample = small.crop((x, y, x + cols, y + rows))
            color = Image.new("RGB", sample.size, template.getpixel((x, y)))
            maps.append(ImageChops.difference(sample, color).convert("L", (.333, .334, .333, 0)))
    while len(maps) > 1:
        maps = [Image.blend(a, b, .5) for a, b in zip(maps[::2], maps[1::2])]
    distances = maps[0]
    for _ in range(4):
        minimum = distances.getextrema()[0]
        if minimum > 40:
            break
        y, x = divmod(distances.tobytes().index(minimum), cols)
        yield (x / scale, y / scale, tw / scale, th / scale)
        distances.paste(255, (max(0, x-tw//2), max(0, y-th//2), min(cols, x+tw//2+1), min(rows, y+th//2+1)))


def _score(screen, template, detail, rect):
    # The inset discards the rounded screen-card corners, and allows mild edge clipping.
    x, y, w, h = rect
    box = (x + .10*w, y + .10*h, x + .90*w, y + .90*h)
    if box[0] < 0 or box[1] < 0 or box[2] > screen.width or box[3] > screen.height:
        return 0.0
    candidate = screen.resize(template.size, Image.Resampling.BILINEAR, box)
    return max(0.0, 1 - _difference(candidate, template) / detail)


def _same_location(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return (abs((ax + aw/2) - (bx + bw/2)) < min(aw, bw)*.45
            and abs((ay + ah/2) - (by + bh/2)) < min(ah, bh)*.45)


def _refine(screen, template, detail, score, box):
    for step in (.05, .02, .009):
        x, y, w, h = box
        for zoom in (-step, 0, step):
            dw, dh = w * zoom, h * zoom
            for dx in (-step, 0, step):
                for dy in (-step, 0, step):
                    option = (x - dw/2 + w*dx, y - dh/2 + h*dy, w+dw, h+dh)
                    quality = _score(screen, template, detail, option)
                    if quality > score:
                        score, box = quality, option
    return score, box


def _locate(screen, resource):
    result = {"status": "no_match", "size": list(screen.size), "bbox": None, "score": 0.0}
    aspect = resource.height / resource.width
    if not .25 <= aspect <= 4 or max(screen.size) > 5 * min(screen.size):
        return result
    coarse = resource.resize((16, round(16*aspect)), Image.Resampling.BILINEAR)
    interior = (.1*resource.width, .1*resource.height, .9*resource.width, .9*resource.height)
    template = resource.resize((32, 32), Image.Resampling.BILINEAR, interior)
    mean_color = tuple(round(v) for v in ImageStat.Stat(template).mean)
    detail = _difference(template, Image.new("RGB", template.size, mean_color))
    texture = _difference(template, template.filter(ImageFilter.GaussianBlur(1.3)))
    if detail < 12 or texture < 3:
        return dict(result, status="insufficient_detail")

    options = []
    width = max(12, screen.width*.06)
    while width <= min(screen.width, screen.height/aspect):
        for box in _coarse_boxes(screen, coarse, width):
            score = _score(screen, template, detail, box)
            if score > .30:
                options.append((score, box))
        width *= 1.10

    peaks = []
    for score, box in sorted(options, reverse=True):
        if all(not _same_location(box, other) for _, other in peaks):
            peaks.append(_refine(screen, template, detail, score, box))
            if len(peaks) >= 8:
                break
    if not peaks:
        return result
    peaks.sort(reverse=True)
    best, (x, y, w, h) = peaks[0]
    second = max((s for s, b in peaks[1:] if not _same_location(b, peaks[0][1])), default=0)
    result.update(score=round(best, 4), runner_up_score=round(second, 4))
    if best >= .76:
        if second >= .76 or (second >= .71 and best - second < .10):
            result["status"] = "ambiguous"
        else:
            result.update(status="matched", bbox=[max(0, round(x)), max(0, round(y)),
                                                  min(screen.width, round(x+w)), min(screen.height, round(y+h))])
    return result


def match_preset(board_bytes, shot_bytes, resource_bytes):
    """Return each artwork's exclusive XYXY bounds in its input's oriented pixel space."""
    invalid = {"status": "invalid_image", "size": [], "score": 0.0, "bbox": None}
    result = {"matched": False, "method": "multiscale_rgb_template_v1",
              "resource_sha256": hashlib.sha256(resource_bytes).hexdigest()}
    try:
        resource = _decode(resource_bytes)
    except (OSError, ValueError, Image.DecompressionBombError):
        return dict(result, board=dict(invalid), shot=dict(invalid))
    for key, data in (("board", board_bytes), ("shot", shot_bytes)):
        try:
            screen = _decode(data)
        except (OSError, ValueError, Image.DecompressionBombError):
            result[key] = dict(invalid)
        else:
            result[key] = _locate(screen, resource)
    result["matched"] = all(result[key]["status"] == "matched" for key in ("board", "shot"))
    return result
