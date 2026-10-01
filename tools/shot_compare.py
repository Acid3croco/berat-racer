"""Before / after screenshots of one spot: the same views (top, oblique, road level) from two players and two worlds, side by side.

Usage: uv run python shot_compare.py OUT.jpg X Z --before APP WORLD --after APP WORLD
Each player is a BeratRacer.app one folder under Build/ (see shot.sh). Writes one JPEG: a row per view, before on the left.
"""
import argparse
import os
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
VIEWS = ("top", "oblique", "road")
WIDTH = 800                        # of each half


def shoot(app, world, x, z, out_dir):
    env = dict(os.environ, BERAT_APP=str(Path(app).resolve()))
    subprocess.run([str(ROOT / "tools" / "shot.sh"), "spot", str(x), str(z), str(Path(world).resolve()), str(out_dir)], env=env, check=True,
                   capture_output=True)
    return [Image.open(out_dir / f"spot_{view}.png").convert("RGB") for view in VIEWS]


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out", type=Path)
    parser.add_argument("x", type=float)
    parser.add_argument("z", type=float)
    parser.add_argument("--before", nargs=2, required=True, metavar=("APP", "WORLD"))
    parser.add_argument("--after", nargs=2, required=True, metavar=("APP", "WORLD"))
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        rows = []
        for label, (app, world) in (("before", args.before), ("after", args.after)):
            out_dir = Path(tmp) / label
            out_dir.mkdir()
            rows.append(shoot(app, world, args.x, args.z, out_dir))
    height = [round(img.height * WIDTH / img.width) for img in rows[0]]
    sheet = Image.new("RGB", (2 * WIDTH, sum(height)), "white")
    y = 0
    for k, h in enumerate(height):
        for col, images in enumerate(rows):
            sheet.paste(images[k].resize((WIDTH, h)), (col * WIDTH, y))
        y += h
    draw = ImageDraw.Draw(sheet)
    for col, label in enumerate(("before", "after")):
        draw.text((col * WIDTH + 10, 10), f"{label}  ({args.x:.0f}, {args.z:.0f})", fill="white", stroke_width=2, stroke_fill="black")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(args.out, quality=82)
    print(args.out)


if __name__ == "__main__":
    main()
