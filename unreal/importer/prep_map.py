"""The in-game map: a stylized top-down image of the block (land cover colours with hill shading, buildings, water, roads
by width and surface), for the HUD's minimap and full map. Writes <out>/map.png and <out>/map.json (west, north, metres
per pixel).

    uv run python prep_map.py C:/Users/jack/berat70scale-1m --si 4 6 --sj 4 6 --out C:/Users/jack/berat-cache/b3x3
"""

import argparse
import json
from pathlib import Path

import numpy as np
import tifffile
from PIL import Image, ImageDraw

# land-cover class (manifest order) -> map colour
CLASS_RGB = {
    0: (196, 186, 160),    # none / bare
    1: (150, 178, 104),    # meadow
    2: (214, 198, 140),    # cereal
    3: (190, 168, 128),    # row crop
    4: (170, 170, 110),    # vineyard
    5: (140, 170, 100),    # orchard
    6: (176, 182, 128),    # fallow
    7: (170, 170, 168),    # parking
    8: (160, 186, 120),    # garden
    9: (186, 180, 166),    # yard
    10: (78, 116, 70),     # forest
    11: (160, 170, 150),   # cemetery
    12: (130, 176, 96),    # pitch
    13: (120, 140, 90),    # scrub
}
PIXELS = 4096


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("package", type=Path)
    ap.add_argument("--si", type=int, nargs=2, required=True)
    ap.add_argument("--sj", type=int, nargs=2, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    size = 3200
    west = size * a.si[0] - 16000
    north = size * (a.sj[1] + 1) - 16000
    span = size * (a.si[1] - a.si[0] + 1)
    mpp = span / PIXELS
    pal = np.zeros((256, 3), np.uint8)
    for k, c in CLASS_RGB.items():
        pal[k] = c
    img = np.zeros((PIXELS, PIXELS, 3), np.float32)

    def px(x, y):
        return (x - west) / mpp, (north - y) / mpp

    # land cover and hill shading, sector by sector
    for sj in range(a.sj[0], a.sj[1] + 1):
        for si in range(a.si[0], a.si[1] + 1):
            d = a.package / "sectors" / f"{si}_{sj}"
            cls = np.asarray(Image.open(d / "classes.png"))[:-1, :-1]          # 3200 x 3200, 1 m, north row first
            h = tifffile.imread(d / "height.tif").astype(np.float32)[:-1, :-1]
            gy, gx = np.gradient(h)
            shade = np.clip(1.0 + (-gx * 0.6 + gy * 0.6) * 0.35, 0.7, 1.25)   # light from the north-west
            rgb = pal[cls].astype(np.float32) * shade[..., None]
            n = int(np.ceil(size / mpp)) + 1        # one pixel of overlap: no seam between sectors
            x0, y0 = px(size * si - 16000, size * (sj + 1) - 16000)
            tile = np.asarray(Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8)).resize((n, n), Image.BILINEAR), np.float32)
            r0, c0 = int(round(y0)), int(round(x0))
            img[r0:r0 + n, c0:c0 + n] = tile[: PIXELS - r0, : PIXELS - c0]
    im = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8))
    dr = ImageDraw.Draw(im)

    for sj in range(a.sj[0], a.sj[1] + 1):
        for si in range(a.si[0], a.si[1] + 1):
            d = a.package / "sectors" / f"{si}_{sj}"
            # water: areas filled, streams as lines
            w = json.loads((d / "water.geojson").read_text(encoding="utf-8"))
            for f in w["features"]:
                g = f["geometry"]
                if g["type"] == "Polygon":
                    dr.polygon([px(x, y) for x, y, *_ in g["coordinates"][0]], fill=(96, 146, 196))
                elif g["type"] == "LineString":
                    width = max(2, int(round((f["properties"].get("width") or 1) / mpp)) + 1)
                    dr.line([px(x, y) for x, y, *_ in g["coordinates"]], fill=(96, 146, 196), width=width)
            # buildings
            b = json.loads((d / "buildings.geojson").read_text(encoding="utf-8"))
            for f in b["features"]:
                ring = f["geometry"]["coordinates"][0]
                dr.polygon([px(x, y) for x, y, *_ in ring], fill=(118, 108, 104), outline=(84, 76, 74))
    # roads: dirt first, then asphalt; a dark casing under each
    for dirt_pass in (True, False):
        for casing in (True, False):
            for sj in range(a.sj[0], a.sj[1] + 1):
                for si in range(a.si[0], a.si[1] + 1):
                    d = a.package / "sectors" / f"{si}_{sj}"
                    g = json.loads((d / "roads.geojson").read_text(encoding="utf-8"))
                    for f in g["features"]:
                        p = f["properties"]
                        if bool(p.get("dirt")) != dirt_pass or p.get("tunnel"):
                            continue
                        pts = [px(x, y) for x, y, *_ in f["geometry"]["coordinates"]]
                        if len(pts) < 2:
                            continue
                        width = max(2.0, (p.get("width_drawn") or 4.0) / mpp)
                        if casing:
                            dr.line(pts, fill=(70, 66, 60) if not dirt_pass else (140, 120, 90), width=int(round(width + 2)), joint="curve")
                        else:
                            colour = (214, 196, 158) if dirt_pass else (246, 244, 236)
                            dr.line(pts, fill=colour, width=int(round(width)), joint="curve")
    a.out.mkdir(parents=True, exist_ok=True)
    im.save(a.out / "map.png")
    (a.out / "map.json").write_text(json.dumps({"west": west, "north": north, "metres_per_pixel": mpp, "pixels": PIXELS}, indent=1))
    print(f"map {PIXELS} px, {mpp:.3f} m/px, west {west} north {north}")


if __name__ == "__main__":
    main()
