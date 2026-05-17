"""PIL-based schema preview PNG renderers.

Two render modes:
- ``_render_generic_schema_preview_png``: metadata-only diagnostic image (used when
  the semantic_schema has no ``render_primitives``). Shows artifact metadata, hard
  requirements, advisory preferences, and a list of semantic keys.
- ``_render_primitives_schema_preview_png``: data-driven blockout (used when the
  semantic_schema includes ``render_primitives`` — rectangles, ellipses, lines,
  polygons, text). The renderer is intentionally generic; all domain-specific
  layout meaning lives in the schema payload, not in renderer code.

The dispatcher ``_render_schema_preview_png`` picks the right mode automatically.
PIL is imported lazily inside each function so the plugin still loads when PIL
is unavailable (the renderer is only invoked for ``render_schema`` operations).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from _constants import ARTIFACT_SCHEMA_VERSION


def _load_pil_font(size: int) -> Any:
    """Load a TrueType font at the requested size, falling back to PIL's default bitmap font."""
    try:
        from PIL import ImageFont
        _FONT_SEARCH_PATHS = [
            # Linux (Debian/Ubuntu/Alpine — most Docker images)
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
            "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
            # macOS
            "/System/Library/Fonts/Helvetica.ttc",
            "/Library/Fonts/Arial.ttf",
            # Windows
            "C:/Windows/Fonts/arial.ttf",
            "C:/Windows/Fonts/calibri.ttf",
        ]
        for fp in _FONT_SEARCH_PATHS:
            if Path(fp).exists():
                return ImageFont.truetype(fp, size)
    except Exception:
        pass
    try:
        from PIL import ImageFont
        return ImageFont.load_default()
    except Exception:
        return None


def _render_schema_preview_png(schema_payload: dict[str, Any], output_path: Path, title: str) -> None:
    """Dispatcher: pick primitive-driven mode when the schema has render_primitives, else generic."""
    semantic = schema_payload.get("semantic_schema") or {}
    if isinstance(semantic.get("render_primitives"), list):
        _render_primitives_schema_preview_png(schema_payload, output_path, title)
    else:
        _render_generic_schema_preview_png(schema_payload, output_path, title)


def _render_generic_schema_preview_png(schema_payload: dict[str, Any], output_path: Path, title: str) -> None:
    """Metadata-only diagnostic preview: artifact info + hard reqs + advisory + semantic keys."""
    from PIL import Image, ImageDraw

    font_title = _load_pil_font(28)
    font_section = _load_pil_font(20)
    font_body = _load_pil_font(17)

    width, height = 1400, 900
    bg = (248, 248, 248)
    image = Image.new("RGB", (width, height), bg)
    draw = ImageDraw.Draw(image)

    # Outer border
    draw.rectangle((20, 20, width - 20, height - 20), outline=(60, 60, 60), width=2)

    # Title bar
    draw.rectangle((20, 20, width - 20, 80), fill=(40, 60, 90))
    draw.text((40, 30), title[:120], fill=(255, 255, 255), font=font_title)

    artifact = schema_payload.get("artifact") or {}
    deterministic = schema_payload.get("deterministic") or {}
    contract = schema_payload.get("fidelity_contract") or {}
    semantic = schema_payload.get("semantic_schema") or {}

    # Metadata column
    meta_lines = [
        f"schema:  {schema_payload.get('schema_version', ARTIFACT_SCHEMA_VERSION)}",
        f"profile: {schema_payload.get('profile', 'unknown')}",
        f"adapter: {artifact.get('adapter', 'unknown')}",
        f"type:    {artifact.get('detected_type', 'unknown')}",
        f"sha256:  {str(artifact.get('sha256', ''))[:28]}…",
    ]
    for key in ("width", "height", "page_count", "line_count", "char_count", "dxfversion", "size_bytes"):
        if key in deterministic:
            meta_lines.append(f"{key}: {deterministic[key]}")

    y = 100
    draw.text((40, y), "METADATA", fill=(40, 60, 90), font=font_section)
    y += 30
    for line in meta_lines:
        draw.text((50, y), line, fill=(30, 30, 30), font=font_body)
        y += 26

    # Hard requirements column
    hard = contract.get("hard_requirements") or []
    if hard:
        y += 16
        draw.text((40, y), "HARD REQUIREMENTS", fill=(140, 40, 40), font=font_section)
        y += 30
        for item in hard[:18]:
            draw.text((50, y), f"• {item[:110]}", fill=(100, 20, 20), font=font_body)
            y += 26
            if y > height - 60:
                break

    # Advisory preferences
    advisory = contract.get("advisory_preferences") or []
    if advisory and y < height - 100:
        y += 16
        draw.text((40, y), "ADVISORY", fill=(60, 100, 40), font=font_section)
        y += 30
        for item in advisory[:8]:
            draw.text((50, y), f"– {item[:110]}", fill=(40, 80, 30), font=font_body)
            y += 26
            if y > height - 60:
                break

    # Semantic schema key count (if present)
    if semantic:
        sem_keys = list(semantic.keys())[:12]
        y += 16
        if y < height - 80:
            draw.text((40, y), "SEMANTIC KEYS", fill=(60, 60, 140), font=font_section)
            y += 30
            draw.text((50, y), ", ".join(sem_keys), fill=(40, 40, 120), font=font_body)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path, optimize=True)


def _render_primitives_schema_preview_png(schema_payload: dict[str, Any], output_path: Path, title: str) -> None:
    """Data-driven blockout: draws every primitive in semantic_schema.render_primitives."""
    from PIL import Image, ImageDraw

    semantic = schema_payload.get("semantic_schema") or {}
    canvas = semantic.get("canvas") if isinstance(semantic.get("canvas"), dict) else {}
    width = _safe_int(canvas.get("width"), 1600)
    height = _safe_int(canvas.get("height"), 950)
    background = _rgb(canvas.get("background"), (255, 255, 255))
    image = Image.new("RGB", (width, height), "white")
    if background != (255, 255, 255):
        image.paste(background, (0, 0, width, height))
    draw = ImageDraw.Draw(image)
    draw.text((40, 25), title, fill=(0, 0, 0))
    draw.text((40, 52), "Deterministic schema primitive preview. Domain meaning lives in schema data, not renderer code.", fill=(70, 70, 70))

    for primitive in semantic.get("render_primitives") or []:
        if not isinstance(primitive, dict):
            continue
        _draw_primitive(draw, primitive, width, height)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path)


def _draw_primitive(draw: Any, primitive: dict[str, Any], canvas_width: int, canvas_height: int) -> None:
    """Render one primitive (rect/ellipse/line/polygon/text) onto the canvas."""
    shape = str(primitive.get("shape") or primitive.get("type") or "").lower()
    fill = _rgb(primitive.get("fill"), None)
    outline = _rgb(primitive.get("outline") or primitive.get("stroke"), (60, 60, 60))
    text_fill = _rgb(primitive.get("text_fill"), (20, 20, 20))
    width = _safe_int(primitive.get("width") or primitive.get("stroke_width"), 2)
    if shape == "rect":
        draw.rectangle(_primitive_box(primitive, canvas_width, canvas_height), fill=fill, outline=outline, width=width)
    elif shape == "ellipse":
        draw.ellipse(_primitive_box(primitive, canvas_width, canvas_height), fill=fill, outline=outline, width=width)
    elif shape == "line":
        points = _primitive_points(primitive, canvas_width, canvas_height)
        if len(points) >= 2:
            draw.line(points[:2], fill=outline, width=width)
    elif shape == "polygon":
        points = _primitive_points(primitive, canvas_width, canvas_height)
        if len(points) >= 3:
            draw.polygon(points, fill=fill, outline=outline)
    elif shape == "text":
        x = _coord(primitive.get("x", 0), canvas_width)
        y = _coord(primitive.get("y", 0), canvas_height)
        draw.text((x, y), str(primitive.get("text") or primitive.get("label") or ""), fill=text_fill)
    if primitive.get("label") and shape != "text" and bool(primitive.get("show_label", True)):
        box = _primitive_box(primitive, canvas_width, canvas_height) if shape in {"rect", "ellipse"} else None
        if box:
            draw.text((box[0] + 5, box[1] + 5), str(primitive["label"])[:80], fill=text_fill)


def _primitive_box(primitive: dict[str, Any], canvas_width: int, canvas_height: int) -> tuple[int, int, int, int]:
    """Resolve a primitive's bounding box from (x,y,w,h) or (x,y,x2,y2)."""
    x = _coord(primitive.get("x", 0), canvas_width)
    y = _coord(primitive.get("y", 0), canvas_height)
    w = _coord(primitive.get("w", primitive.get("width_px", 0)), canvas_width)
    h = _coord(primitive.get("h", primitive.get("height_px", 0)), canvas_height)
    x2 = _coord(primitive.get("x2"), canvas_width) if primitive.get("x2") is not None else x + w
    y2 = _coord(primitive.get("y2"), canvas_height) if primitive.get("y2") is not None else y + h
    return (min(x, x2), min(y, y2), max(x, x2), max(y, y2))


def _primitive_points(primitive: dict[str, Any], canvas_width: int, canvas_height: int) -> list[tuple[int, int]]:
    """Resolve a primitive's points list, supporting both dict-of-points and pair-of-coords forms."""
    raw_points = primitive.get("points")
    if isinstance(raw_points, list):
        points: list[tuple[int, int]] = []
        for point in raw_points:
            if isinstance(point, dict):
                points.append((_coord(point.get("x", 0), canvas_width), _coord(point.get("y", 0), canvas_height)))
            elif isinstance(point, list) and len(point) >= 2:
                points.append((_coord(point[0], canvas_width), _coord(point[1], canvas_height)))
        return points
    return [
        (_coord(primitive.get("x1", primitive.get("x", 0)), canvas_width), _coord(primitive.get("y1", primitive.get("y", 0)), canvas_height)),
        (_coord(primitive.get("x2", 0), canvas_width), _coord(primitive.get("y2", 0), canvas_height)),
    ]


def _coord(value: Any, extent: int) -> int:
    """Resolve a coordinate that may be normalized (0–1) or absolute pixels."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0
    if -1.0 <= number <= 1.0:
        return int(round(number * extent))
    return int(round(number))


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _rgb(value: Any, default: tuple[int, int, int] | None) -> tuple[int, int, int] | None:
    """Resolve an RGB tuple from a list, named color, or #RRGGBB hex string."""
    if value is None:
        return default
    if isinstance(value, list) and len(value) >= 3:
        return tuple(max(0, min(255, int(component))) for component in value[:3])  # type: ignore[return-value]
    if isinstance(value, str):
        named = {
            "black": (0, 0, 0),
            "white": (255, 255, 255),
            "gray": (160, 160, 160),
            "grey": (160, 160, 160),
            "light_gray": (230, 230, 230),
            "light_grey": (230, 230, 230),
            "dark_gray": (80, 80, 80),
            "dark_grey": (80, 80, 80),
        }
        if value.lower() in named:
            return named[value.lower()]
        if value.startswith("#") and len(value) == 7:
            try:
                return (int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16))
            except ValueError:
                return default
    return default


__all__ = ["_render_schema_preview_png"]
