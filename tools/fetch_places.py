"""Place names (cities, towns, villages, hamlets, suburbs) from OpenStreetMap for the game map -> data/big/places.json (+ copies next to the generated worlds).

Local coordinates as everywhere: x east, z north, metres from the Berat centre (tools/fetch.py).
"""
import json, sys, time
from pathlib import Path
import requests
from pyproj import Transformer
from fetch import CX, CY

S, W, N, E = 42.35, -0.25, 44.0, 2.35                     # generous box around Haute-Garonne (and the rest of the former Midi-Pyrenees, cut off below)
QUERY = f"""[out:json][timeout:180];
node["place"~"^(city|town|village|hamlet|suburb)$"]["name"]({S},{W},{N},{E});
out;"""
RANK = {"city": 0, "town": 1, "village": 2, "suburb": 2, "hamlet": 3}

def main():
    for attempt in range(5):
        r = requests.post("https://overpass-api.de/api/interpreter", data={"data": QUERY}, timeout=300, headers={"User-Agent": "berat-racer/1.0 (hobby game, non-commercial)"})
        if r.ok and r.text.lstrip().startswith("{"): break
        print(f"attempt {attempt}: HTTP {r.status_code}", file=sys.stderr); time.sleep(15)
    else: sys.exit("Overpass did not answer")
    tr = Transformer.from_crs(4326, 2154, always_xy=True)
    out = []
    for e in r.json()["elements"]:
        t = e["tags"]; k = t.get("place")
        if k not in RANK: continue
        x, y = tr.transform(e["lon"], e["lat"])
        try: pop = int(str(t.get("population", "0")).replace(" ", "").split(";")[0])
        except ValueError: pop = 0
        out.append(dict(n=t["name"], k=k, r=RANK[k], pop=pop, x=round(x - CX, 1), z=round(y - CY, 1)))
    out.sort(key=lambda p: (p["r"], -p["pop"]))
    Path("data/big/places.json").write_text(json.dumps(out, ensure_ascii=False))
    for d in ("../world", "../world_hg"):
        if Path(d).is_dir(): Path(d, "places.json").write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
    from collections import Counter
    print(len(out), "places", dict(Counter(p["k"] for p in out)), "biggest:", [(p["n"], p["pop"]) for p in out[:5]])

if __name__ == "__main__":
    main()
