"""Check a map package: decode every file the way an importer would, measure it, and draw previews.

  heights   height.png decodes to height.tif within one 16-bit step; neighbouring sectors share their edge row / column
  rasters   sizes, class ids within the manifest's list
  meshes    every .glb parses; its vertices lie in or next to the sector; no NaN; roads, paint, roofs face up, walls out
  vectors   GeoJSON / JSON parse; ids are unique over the package; every lane link (next, left, right, yields) resolves, except
            to lanes beyond the package's border
  roads     how the carved terrain meets the drawn road surface (from report.json)
  previews  previews/<si>_<sj>.png (hillshade, classes, water, roads, buildings) and previews/overview.png
Returns True when every check passes.
"""
import gzip
import json
from pathlib import Path

import numpy as np
import rasterio
import shapely
from PIL import Image, ImageDraw

from . import gltf

CLASS_COLOURS = {"none": (150, 140, 120), "meadow": (120, 170, 90), "cereal": (215, 195, 110), "row crop": (180, 150, 90), "vineyard": (140, 90, 120),
                 "orchard": (90, 150, 70), "fallow": (190, 180, 130), "parking": (90, 90, 95), "garden": (110, 160, 100), "yard": (160, 150, 140),
                 "forest": (40, 100, 50), "cemetery": (130, 130, 150), "pitch": (80, 190, 80), "scrub": (100, 130, 70)}
MESH_SLACK = 400.0         # m a mesh may reach past its sector: a junction, car park or building belongs to the sector holding its
                           # centre, and a Toulouse warehouse or interchange is large
SEAM_TOLERANCE = 0.05      # m between the shared edges of two sectors


def load_json(path):
    if str(path).endswith(".gz"):
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return json.load(f)
    return json.loads(Path(path).read_text())


def hillshade(h, cell):
    gy, gx = np.gradient(h, cell)
    light = np.array([-1.0, 1.0, 1.4]); light /= np.linalg.norm(light)
    n = np.dstack([-gx, -gy, np.ones_like(h)])
    n /= np.linalg.norm(n, axis=2, keepdims=True)
    return np.clip(n @ light, 0, 1)


def check(out, log=print):
    out = Path(out)
    man = load_json(out / "manifest.json")
    problems, notes = [], {}
    sectors = [tuple(s) for s in man["sectors"]]
    size, n = man["sector_size"], man["samples"]
    lo, hi = man["height"]["min"], man["height"]["max"]
    step = (hi - lo) / 65535
    heights, lane_ids, lane_refs, road_ids, building_ids = {}, {}, [], set(), set()
    (out / "previews").mkdir(exist_ok=True)
    over, wrong_faces, overhang = {}, [0, 0], [0.0]
    for si, sj in sectors:
        d = out / "sectors" / f"{si}_{sj}"
        ox, oy = -16000 + size * si, -16000 + size * sj
        missing = [f for f in man["files"] if not (d / f).exists()]
        if missing:
            problems.append(f"{si}_{sj}: missing {missing}")
            continue
        with rasterio.open(d / "height.tif") as f:
            tif = f.read(1).astype(np.float64)
        png = np.asarray(Image.open(d / "height.png")).astype(np.float64)
        dec = lo + png / 65535 * (hi - lo)
        err = float(np.abs(dec - tif).max())
        if png.shape != (n, n) or err > step:
            problems.append(f"{si}_{sj}: height.png {png.shape}, decode error {err:.4f} m > step {step:.4f}")
        heights[(si, sj)] = tif
        cls = np.asarray(Image.open(d / "classes.png"))
        for name in ("holes.png", "rows.png", "classes.png"):
            a = np.asarray(Image.open(d / name))
            if a.shape != (n, n) or a.dtype != np.uint8:
                problems.append(f"{si}_{sj}: {name} {a.shape} {a.dtype}")
        if cls.max() >= len(man["classes"]):
            problems.append(f"{si}_{sj}: class id {cls.max()} not in the manifest")
        blds = load_json(d / "buildings.geojson")["features"]
        footprints = shapely.union_all([shapely.geometry.shape(f["geometry"]) for f in blds]) if blds else None
        for name in ("roads.glb", "buildings.glb"):
            doc, blob = gltf.read_glb(d / name)
            wrong, faces = facing(triangles_of(doc, blob), footprints if name == "buildings.glb" else None)
            wrong_faces[0] += wrong; wrong_faces[1] += faces
            for material, v in gltf.positions_of(doc, blob).items():
                if not np.isfinite(v).all():
                    problems.append(f"{si}_{sj}: {name} {material} has NaN")
                    continue
                reach = np.maximum.reduce([ox - v[:, 0], v[:, 0] - ox - size, oy - v[:, 1], v[:, 1] - oy - size]).max(initial=0.0)
                overhang[0] = max(overhang[0], float(reach))
                outside = (v[:, 0] < ox - MESH_SLACK) | (v[:, 0] > ox + size + MESH_SLACK) | (v[:, 1] < oy - MESH_SLACK) | (v[:, 1] > oy + size + MESH_SLACK)
                if outside.any():
                    problems.append(f"{si}_{sj}: {name} {material}: {int(outside.sum())} vertices beyond the sector")
        roads = load_json(d / "roads.geojson")["features"]
        for f in roads:
            rid = f["properties"]["id"]
            if rid in road_ids:
                problems.append(f"{si}_{sj}: road id {rid} twice")
            road_ids.add(rid)
        for f in blds:
            bid = f["properties"]["id"]
            if bid in building_ids:
                problems.append(f"{si}_{sj}: building id {bid} twice")
            building_ids.add(bid)
        for e in load_json(d / "lanes.json.gz")["elements"]:
            if e["id"] in lane_ids:
                problems.append(f"{si}_{sj}: lane id {e['id']} twice")
            lane_ids[e["id"]] = e["points"]
            lane_refs += [(r, e["id"]) for r in e["next"] + e["yields"] + [x for x in (e["left"], e["right"]) if x >= 0]]
        veg = load_json(d / "vegetation.json.gz")
        water = load_json(d / "water.geojson")["features"]
        if any(not np.isfinite(np.asarray(f["geometry"]["coordinates"][0] if f["geometry"]["type"] == "Polygon" else f["geometry"]["coordinates"], float)).all() for f in water):
            problems.append(f"{si}_{sj}: water with NaN levels")
        over[(si, sj)] = preview(d, out / "previews" / f"{si}_{sj}.png", tif[::-1], cls[::-1], roads, blds, water, man, (ox, oy))
        notes[f"{si}_{sj}"] = dict(roads=len(roads), buildings=len(blds), trees=len(veg["trees"]["rows"]), water=len(water))

    # seams: the east column of a sector is the west column of the next, the north row the south row of the one above
    seam = 0.0
    for (si, sj), h in heights.items():
        if (si + 1, sj) in heights:
            seam = max(seam, float(np.abs(h[:, -1] - heights[(si + 1, sj)][:, 0]).max()))
        if (si, sj + 1) in heights:
            seam = max(seam, float(np.abs(h[0, :] - heights[(si, sj + 1)][-1, :]).max()))
    if seam > SEAM_TOLERANCE:
        problems.append(f"sector seams differ by up to {seam:.3f} m")

    # lane links: unresolved ones must point beyond the package (lanes whose first point lies outside every sector)
    unresolved = [(r, owner) for r, owner in lane_refs if r not in lane_ids]
    border = [owner for _, owner in unresolved if any(near_border(p, sectors, size) for p in lane_ids[owner])]
    inner = len(unresolved) - len(border)
    if inner:
        problems.append(f"{inner} lane links resolve to nothing inside the package")

    report = load_json(out / "report.json") if (out / "report.json").exists() else dict(sectors=[])
    above = max((s["road_on_terrain"].get("terrain_above_road_max", 0) for s in report["sectors"]), default=0)
    below = max((s["road_on_terrain"].get("terrain_below_road_max", 0) for s in report["sectors"]), default=0)
    if wrong_faces[0] > 0.001 * max(wrong_faces[1], 1):
        problems.append(f"{wrong_faces[0]} of {wrong_faces[1]} faces turned the wrong way")
    if above > 0.05:
        problems.append(f"terrain stands up to {above:.3f} m over a road")
    overview(out / "previews" / "overview.png", over, sectors)
    summary = dict(sectors=len(sectors), roads=len(road_ids), buildings=len(building_ids), lanes=len(lane_ids), lane_links=len(lane_refs),
                   lane_links_beyond_border=len(border), wrong_faces=wrong_faces, mesh_overhang_max_m=round(overhang[0], 1), seam_max_m=round(seam, 4), height_step_m=round(step, 4),
                   terrain_above_road_max_m=above, road_over_terrain_max_m=below, problems=problems)
    (out / "check.json").write_text(json.dumps(dict(summary=summary, sectors=notes), indent=1))
    log(json.dumps(summary, indent=1))
    return not problems


def triangles_of(doc, blob):
    """{material: (vertices (n, 3) package axes, absolute; triangles (t, 3))} of a package .glb."""
    out = {}
    tx, _, tz = doc["nodes"][0].get("translation", [0, 0, 0])
    for prim in doc.get("meshes", [dict(primitives=[])])[0]["primitives"]:
        acc = doc["accessors"][prim["attributes"]["POSITION"]]; bv = doc["bufferViews"][acc["bufferView"]]
        v = np.frombuffer(blob, "<f4", acc["count"] * 3, bv["byteOffset"]).reshape(-1, 3).astype(np.float64)
        ia = doc["accessors"][prim["indices"]]; ib = doc["bufferViews"][ia["bufferView"]]
        t = np.frombuffer(blob, "<u4", ia["count"], ib["byteOffset"]).reshape(-1, 3).astype(np.int64)
        out[doc["materials"][prim["material"]]["name"]] = (np.c_[v[:, 0] + tx, -(v[:, 2] + tz), v[:, 1]], t)
    return out


def facing(tris_by_material, footprints):
    """Faces turned the wrong way (horizontal faces facing down: roads, paint, roofs; walls facing into their building), of how many."""
    bad, total = 0, 0
    for material, (v, t) in tris_by_material.items():
        n = np.cross(v[t[:, 1]] - v[t[:, 0]], v[t[:, 2]] - v[t[:, 0]])
        n /= np.maximum(np.linalg.norm(n, axis=1), 1e-12)[:, None]
        flat = np.abs(n[:, 2]) > 0.3
        bad += int((flat & (n[:, 2] < 0)).sum()); total += int(flat.sum())
        if material == "wall" and footprints is not None:
            wall = ~flat
            centre = v[t[wall]].mean(axis=1)[:, :2]
            front, back = centre + n[wall, :2] * 0.3, centre - n[wall, :2] * 0.3      # a shared wall has a building on both sides:
            reversed_ = shapely.contains_xy(footprints, front[:, 0], front[:, 1]) & ~shapely.contains_xy(footprints, back[:, 0], back[:, 1])
            bad += int(reversed_.sum()); total += int(wall.sum())                       # only a wall with its back outside is wrong
    return bad, total


def near_border(point, sectors, size, reach=200.0):
    """Is a point within `reach` of the package's outer border (a sector side with no neighbour)?"""
    x, y = point[0], point[1]
    si, sj = int(np.floor((x + 16000) / size)), int(np.floor((y + 16000) / size))
    have = set(sectors)
    fx, fy = (x + 16000) - si * size, (y + 16000) - sj * size
    return ((fx < reach and (si - 1, sj) not in have) or (fx > size - reach and (si + 1, sj) not in have) or
            (fy < reach and (si, sj - 1) not in have) or (fy > size - reach and (si, sj + 1) not in have))


def preview(d, path, h_north, cls_north, roads, blds, water, man, origin, px=1600):
    """Hillshade times class colours, with water, roads and buildings drawn over: one sector at 2 m a pixel."""
    shade = hillshade(h_north[::-1], man["cell"])[::-1]
    palette = np.array([CLASS_COLOURS.get(c, (200, 0, 200)) for c in man["classes"]], float)
    rgb = palette[cls_north] * (0.35 + 0.65 * shade[..., None])
    img = Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8)).resize((px, px), Image.BILINEAR)
    draw = ImageDraw.Draw(img)
    scale = px / man["sector_size"]
    to_px = lambda c: [((x - origin[0]) * scale, (origin[1] + man["sector_size"] - y) * scale) for x, y, *_ in c]
    for f in water:
        g = f["geometry"]
        if g["type"] == "Polygon":
            draw.polygon(to_px(g["coordinates"][0]), fill=(70, 120, 190))
        else:
            draw.line(to_px(g["coordinates"]), fill=(70, 120, 190), width=2)
    for f in roads:
        p = f["properties"]
        width = max(1, int(round(p["width_drawn"] * scale)))
        draw.line(to_px(f["geometry"]["coordinates"]), fill=(225, 80, 60) if p["bridge"] else (30, 30, 30) if not p["dirt"] else (120, 90, 60), width=width)
    for f in blds:
        draw.polygon(to_px(f["geometry"]["coordinates"][0]), fill=(235, 225, 210), outline=(90, 60, 50))
    img.save(path)
    return img.resize((400, 400), Image.BILINEAR)


def overview(path, images, sectors):
    if not images:
        return
    si0, sj0 = min(s[0] for s in sectors), min(s[1] for s in sectors)
    nx, ny = max(s[0] for s in sectors) - si0 + 1, max(s[1] for s in sectors) - sj0 + 1
    tile = 400 if nx * ny <= 64 else max(40, 6400 // max(nx, ny))
    canvas = Image.new("RGB", (nx * tile, ny * tile), (20, 20, 20))
    for (si, sj), img in images.items():
        canvas.paste(img.resize((tile, tile)), ((si - si0) * tile, (ny - 1 - (sj - sj0)) * tile))
    canvas.save(path)
