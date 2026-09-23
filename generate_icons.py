"""Generates simple, original minimal-face avatar icons per persona -
flat color field + basic geometric facial features (dot/line eyes, a mouth
shape, optional brow). Fully original shapes, not based on any existing
character design - just a clean abstract "face mark" per persona."""

from pathlib import Path
from PIL import Image, ImageDraw

OUT_DIR = Path(__file__).parent / "icons"
SIZE = 256
C = SIZE / 2  # center


def base(bg):
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([4, 4, SIZE - 4, SIZE - 4], fill=bg + (255,))
    return img, draw


def eye(draw, cx, cy, r, color):
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color)


def make_analyst() -> Image.Image:
    # calm, steady: level brow line, small round eyes, flat mouth
    img, draw = base((42, 110, 108))
    fg = (235, 245, 244, 255)
    eye_y = C - 18
    eye(draw, C - 40, eye_y, 12, fg)
    eye(draw, C + 40, eye_y, 12, fg)
    # brow: a single straight bar above both eyes
    draw.rounded_rectangle([C - 62, eye_y - 34, C + 62, eye_y - 24], radius=5, fill=fg)
    # mouth: flat line
    draw.rounded_rectangle([C - 34, C + 46, C + 34, C + 56], radius=5, fill=fg)
    return img


def make_skeptic() -> Image.Image:
    # skeptical: one raised brow, asymmetric eyes, slight smirk
    img, draw = base((140, 70, 70))
    fg = (250, 235, 230, 255)
    eye_y = C - 16
    eye(draw, C - 40, eye_y, 11, fg)
    eye(draw, C + 40, eye_y - 8, 9, fg)  # slightly smaller/higher = skeptical
    # brows: left flat, right angled up
    draw.rounded_rectangle([C - 60, eye_y - 32, C - 18, eye_y - 22], radius=5, fill=fg)
    draw.polygon(
        [(C + 18, eye_y - 16), (C + 62, eye_y - 34), (C + 62, eye_y - 24), (C + 24, eye_y - 8)],
        fill=fg,
    )
    # mouth: smirk (asymmetric arc)
    draw.arc([C - 40, C + 20, C + 40, C + 70], start=200, end=340, fill=fg, width=8)
    return img


BUILDERS = {
    "analyst": make_analyst,
    "skeptic": make_skeptic,
}


def main():
    OUT_DIR.mkdir(exist_ok=True)
    for key, builder in BUILDERS.items():
        img = builder()
        path = OUT_DIR / f"{key}.png"
        img.save(path)
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
