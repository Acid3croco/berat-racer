"""Download the CC0 surfaces the Unreal materials use (Poly Haven, https://polyhaven.com, CC0: no attribution required,
credited anyway). Colour, DirectX normal and ARM (AO, roughness, metal) at 2k, into <out>/<name>/.

    uv run python fetch_textures.py C:/Users/jack/berat-cache/textures
"""

import json
import sys
import urllib.request
from pathlib import Path

# role -> Poly Haven asset. Roles are what the importer and the materials refer to.
SURFACES = {
    # terrain classes
    "meadow": "leafy_grass",
    "meadow_b": "sparse_grass",
    "cereal": "dry_mud_field_001",
    "row_crop": "farm_soil",
    "row_crop_b": "raked_dirt",
    "vineyard": "dry_ground_01",
    "fallow": "withered_grass",
    "garden": "grass_path_2",
    "yard": "gravel_ground_01",
    "forest": "forest_leaves_02",
    "forest_b": "forest_ground_04",
    "cemetery": "gravelly_sand",
    "scrub": "dry_ground_rocks",
    "bare": "brown_mud_dry",
    "rock": "rock_face",
    "mud": "brown_mud_02",
    # roads
    "asphalt": "asphalt_02",
    "asphalt_worn": "worn_asphalt",
    "dirt_road": "gravel_road",
    "concrete": "concrete_wall_004",
    "parking": "concrete_pavement",
    # buildings: Toulouse brick, ochre / beige render, stone farmhouses; canal tiles, slate, sheet roofs
    "wall_brick": "red_brick_03",
    "wall_brick_b": "medieval_red_brick",
    "wall_plaster": "beige_wall_001",
    "wall_plaster_b": "yellow_plaster",
    "wall_plaster_c": "white_rough_plaster",
    "wall_stone": "plastered_stone_wall",
    "wall_concrete": "concrete_block_wall",
    "roof_canal": "clay_roof_tiles",
    "roof_canal_b": "clay_roof_tiles_02",
    "roof_slate": "grey_roof_tiles",
    "roof_sheet": "roof_07",
    # industrial and farm buildings: metal cladding, corrugated iron, fibre-cement sheets (the grey wavy barn roofs of the
    # French countryside), timber siding; flat roofs
    "wall_metal": "box_profile_metal_sheet",
    "wall_wood": "weathered_plank_siding",
    "roof_metal": "corrugated_iron_02",
    "roof_fibre": "asbestos_sheet",
    "roof_flat": "tarred_gravel",
    "roof_canal_c": "clay_roof_tiles_03",
    "wall_stone_b": "stone_wall",
}
MAPS = {"Diffuse": "color", "nor_dx": "normal", "arm": "arm"}
RES = "2k"


def get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "berat-racer-importer"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def main() -> None:
    out = Path(sys.argv[1])
    credits = []
    for role, asset in SURFACES.items():
        files = json.loads(get(f"https://api.polyhaven.com/files/{asset}"))
        d = out / role
        d.mkdir(parents=True, exist_ok=True)
        for key, name in MAPS.items():
            if key not in files:
                print(f"{role}: {asset} has no {key}")
                continue
            fmt = "png" if key == "nor_dx" else "jpg"
            entry = files[key][RES].get(fmt) or files[key][RES]["jpg"]
            ext = entry["url"].rsplit(".", 1)[1]
            dst = d / f"{role}_{name}.{ext}"
            if not dst.exists():
                dst.write_bytes(get(entry["url"]))
        credits.append({"role": role, "asset": asset, "url": f"https://polyhaven.com/a/{asset}", "licence": "CC0"})
        print(role, asset)
    (out / "credits.json").write_text(json.dumps(credits, indent=1))


if __name__ == "__main__":
    main()
