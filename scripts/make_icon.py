"""Draw the Vendorus app icon (indigo rounded square with a white V) as a
1024 x 1024 PNG with a transparent background, matching app/assets/icon.svg.

    python scripts/make_icon.py OUTPUT.png
"""
import sys

from PIL import Image, ImageDraw

SIZE = 1024
SCALE = 4  # draw large, then shrink, for smooth edges
INDIGO = (99, 102, 241, 255)  # #6366F1
WHITE = (255, 255, 255, 255)


def draw(path: str) -> None:
    big = SIZE * SCALE
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(image)
    # macOS icon grid: the artwork is about 824 px inside the 1024 canvas.
    margin = 100 * SCALE
    box = (margin, margin, big - margin, big - margin)
    d.rounded_rectangle(box, radius=185 * SCALE, fill=INDIGO)

    # The V, in the same proportions as icon.svg (32 px square, V from 9,11 to 23,11 via 16,26).
    side = big - 2 * margin
    def point(x, y):
        return (margin + side * x / 32, margin + side * y / 32)

    width = int(side * 3.2 / 32)
    left, bottom, right = point(9, 11), point(16, 26), point(23, 11)
    d.line([left, bottom, right], fill=WHITE, width=width, joint="curve")
    radius = width / 2
    for x, y in (left, right):
        d.ellipse((x - radius, y - radius, x + radius, y + radius), fill=WHITE)

    image.resize((SIZE, SIZE), Image.LANCZOS).save(path)


if __name__ == "__main__":
    draw(sys.argv[1])
