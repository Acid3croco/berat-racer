"""Sector list for one or more departements (INSEE codes, default 31). Same grid as fetch_vectors.py (3200 m, origin CX-16000, CY-16000).

  uv run python hg_sectors.py            -> data/big/hg_sectors.json          (Haute-Garonne)
  uv run python hg_sectors.py 09 12      -> data/big/hg_sectors_09_12.json    (other departements; boundaries cached in hg_boundary_{code}.json)
Sectors are the ones intersecting the boundary buffered by 500 m. "new_sectors" excludes the existing 10 x 10 block.
"""
import json, math, sys
from pathlib import Path
import requests
from shapely.geometry import shape, box
from shapely.ops import unary_union
from shapely.prepared import prep
from fetch import CX, CY

SECTOR, X0, Y0 = 3200, CX - 16000, CY - 16000
BIG = Path("data/big")

def boundary(code):
    f = BIG / ("hg_boundary.json" if code == "31" else f"hg_boundary_{code}.json")
    if not f.exists():
        r = requests.get("https://data.geopf.fr/wfs/ows", params=dict(SERVICE="WFS", VERSION="2.0.0", REQUEST="GetFeature", TYPENAMES="ADMINEXPRESS-COG.LATEST:departement",
            OUTPUTFORMAT="application/json", SRSNAME="EPSG:2154", CQL_FILTER=f"code_insee='{code}'"), timeout=120)
        r.raise_for_status(); f.write_text(r.text)
    return shape(json.load(open(f))["features"][0]["geometry"])

def sectors(codes):
    poly = unary_union([boundary(c) for c in codes]).buffer(500)
    pp = prep(poly)
    x0, y0, x1, y1 = poly.bounds
    si0, si1 = math.floor((x0 - X0) / SECTOR), math.floor((x1 - X0) / SECTOR)
    sj0, sj1 = math.floor((y0 - Y0) / SECTOR), math.floor((y1 - Y0) / SECTOR)
    secs = [[si, sj] for sj in range(sj0, sj1 + 1) for si in range(si0, si1 + 1)
            if pp.intersects(box(X0 + si * SECTOR, Y0 + sj * SECTOR, X0 + (si + 1) * SECTOR, Y0 + (sj + 1) * SECTOR))]
    return poly, secs, (si0, si1, sj0, sj1), (x0, y0, x1, y1)

if __name__ == "__main__":
    codes = sys.argv[1:] or ["31"]
    poly, secs, (si0, si1, sj0, sj1), bbox = sectors(codes)
    new = [s for s in secs if not (0 <= s[0] < 10 and 0 <= s[1] < 10)]
    tag = "" if codes == ["31"] else "_" + "_".join(codes)
    out = dict(depts=codes, tag=tag, sector=SECTOR, X0=X0, Y0=Y0, si_range=[si0, si1], sj_range=[sj0, sj1], bbox_l93=list(bbox),
               count=len(secs), existing=len(secs) - len(new), new=len(new), sectors=secs, new_sectors=new)
    json.dump(out, open(BIG / f"hg_sectors{tag}.json", "w"))
    print({k: v for k, v in out.items() if "sectors" not in k})
