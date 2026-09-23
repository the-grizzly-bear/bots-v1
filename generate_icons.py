"""Generates persona avatars in a cute minimal style:
round circular head, flat solid color, simple expressive features
(dot eyes, curved mouth), soft organic shapes, playful and approachable."""

from pathlib import Path
from PIL import Image, ImageDraw

OUT_DIR = Path(__file__).parent / "icons"
SIZE = 512
C = SIZE / 2


def base_circle(color):
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    ImageDraw.Draw(img).ellipse([0, 0, SIZE, SIZE], fill=color)
    return img


def make_analyst() -> Image.Image:
    img = base_circle((42, 156, 146, 255))  # teal
    draw = ImageDraw.Draw(img)
    fg = (250, 252, 251, 255)

    # calm, steady vibe: two simple dot eyes, straight brow line, flat neutral mouth
    eye_y = C - 40
    eye_r = 18
    draw.ellipse([C - 50, eye_y - eye_r, C - 50 + eye_r * 2, eye_y + eye_r], fill=fg)
    draw.ellipse([C + 50 - eye_r * 2, eye_y - eye_r, C + 50, eye_y + eye_r], fill=fg)

    # small pupils
    pupil = 8
    draw.ellipse([C - 50, eye_y - pupil, C - 50 + pupil * 2, eye_y + pupil], fill=(42, 156, 146, 255))
    draw.ellipse([C + 50 - pupil * 2, eye_y - pupil, C + 50, eye_y + pupil], fill=(42, 156, 146, 255))

    # straight brow
    draw.rounded_rectangle([C - 62, eye_y - 48, C + 62, eye_y - 38], radius=8, fill=fg)
    # flat mouth
    draw.rounded_rectangle([C - 34, C + 72, C + 34, C + 88], radius=6, fill=fg)

    img = img.resize((256, 256), Image.LANCZOS)
    return img


def make_skeptic() -> Image.Image:
    img = base_circle((220, 100, 70, 255))  # coral/rust
    draw = ImageDraw.Draw(img)
    fg = (255, 240, 225, 255)

    eye_y = C - 40
    # one narrowed eye (skeptical), one normal eye
    draw.rounded_rectangle([C - 50, eye_y - 14, C - 18, eye_y + 14], radius=8, fill=fg)
    eye_r = 18
    draw.ellipse([C + 50 - eye_r * 2, eye_y - eye_r, C + 50, eye_y + eye_r], fill=fg)

    # pupils
    draw.rounded_rectangle([C - 48, eye_y - 8, C - 22, eye_y + 8], radius=5, fill=(220, 100, 70, 255))
    pupil = 8
    draw.ellipse([C + 50 - pupil * 2, eye_y - pupil, C + 50, eye_y + pupil], fill=(220, 100, 70, 255))

    # raised brow (skeptical)
    draw.rounded_rectangle([C - 60, eye_y - 46, C - 20, eye_y - 36], radius=8, fill=fg)
    # smirk/slight frown
    draw.arc([C - 50, C + 50, C + 50, C + 130], start=200, end=340, fill=fg, width=10)

    img = img.resize((256, 256), Image.LANCZOS)
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
