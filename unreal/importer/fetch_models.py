"""Download the CC0 models the world uses (Poly Haven, https://polyhaven.com, CC0), glTF with 1k textures, into <out>/<role>/.
Stand-ins until the Fab assets (Megaplants trees, vehicle packs) are added to the project; kept after that for what Fab lacks.

    uv run python fetch_models.py C:/Users/jack/berat-cache/models
"""

import json
import sys
import urllib.request
from pathlib import Path

# role -> Poly Haven model
MODELS = {
    "lamp_a": "street_lamp_01",
    "lamp_b": "street_lamp_02",
    "poles": "modular_electricity_poles",
    "conifer_a": "pine_tree_01",
    "conifer_b": "fir_tree_01",
    "broadleaf_small": "tree_small_02",
    "shrub_a": "shrub_02",
    "shrub_b": "shrub_03",
    "shrub_c": "shrub_04",
    "grass_a": "grass_medium_01",
    "grass_b": "grass_medium_02",
    "fence": "modular_chainlink_fence",
}
RES = "1k"


def get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "berat-racer-importer"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return r.read()


def main() -> None:
    out = Path(sys.argv[1])
    credits = []
    for role, asset in MODELS.items():
        files = json.loads(get(f"https://api.polyhaven.com/files/{asset}"))
        entry = files["gltf"][RES]["gltf"]
        d = out / role
        d.mkdir(parents=True, exist_ok=True)
        main_file = d / f"{asset}.gltf"
        if not main_file.exists():
            main_file.write_bytes(get(entry["url"]))
        for rel, inc in entry.get("include", {}).items():
            dst = d / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            if not dst.exists():
                dst.write_bytes(get(inc["url"]))
        credits.append({"role": role, "asset": asset, "url": f"https://polyhaven.com/a/{asset}", "licence": "CC0"})
        print(role, asset)
    (out / "credits.json").write_text(json.dumps(credits, indent=1))


if __name__ == "__main__":
    main()
