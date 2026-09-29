"""Download IGN orthophoto (2 m/px) over the same 6.4 km box, used for ground/roof colours."""
import io, sys
from concurrent.futures import ThreadPoolExecutor
import numpy as np, requests
from PIL import Image
from fetch import X0, Y0, N, TILE, WMS

PX = TILE // 2   # 2 m per pixel

def tile(job):
    i, j = job
    x, y = X0 + i * TILE, Y0 + j * TILE
    for _ in range(4):
        r = requests.get(WMS, params=dict(SERVICE="WMS", VERSION="1.3.0", REQUEST="GetMap",
            LAYERS="ORTHOIMAGERY.ORTHOPHOTOS", STYLES="", CRS="EPSG:2154",
            BBOX=f"{x},{y},{x+TILE},{y+TILE}", WIDTH=PX, HEIGHT=PX, FORMAT="image/jpeg"), timeout=180)
        if r.ok and r.headers.get("content-type", "").startswith("image/"):
            return i, j, np.asarray(Image.open(io.BytesIO(r.content)).convert("RGB"))
    raise RuntimeError(f"ortho tile failed {job}: {r.status_code} {r.text[:200]}")

size = N * PX
out = np.zeros((size, size, 3), np.uint8)
jobs = [(i, j) for i in range(N) for j in range(N)]
with ThreadPoolExecutor(4) as ex:
    for n, (i, j, a) in enumerate(ex.map(tile, jobs), 1):
        r0 = size - (j + 1) * PX
        out[r0:r0 + PX, i * PX:(i + 1) * PX] = a
        print(f"\r{n}/{len(jobs)}", end="", file=sys.stderr)
np.save("data/ortho.npy", out)
Image.fromarray(out).resize((1024, 1024)).save("data/ortho_preview.jpg")
print("\northo", out.shape, out.mean(axis=(0, 1)))
