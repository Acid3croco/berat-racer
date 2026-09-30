"""Read vector files written by fetch_hg.py: {base}.json.gz (new downloads) or {base}.json (older ones). base = path without extension."""
import gzip, json
from pathlib import Path

def vec_file(base):
    """The file of a vector layer (None when there is none)."""
    for ext in (".json.gz", ".json"):
        p = Path(str(base) + ext)
        if p.exists(): return p
    return None

def exists_vec(base) -> bool:
    return vec_file(base) is not None

def read_vec(base) -> list:
    p = vec_file(base)
    if p is None: raise FileNotFoundError(f"{base}.json[.gz]")
    if p.suffix == ".gz":
        with gzip.open(p, "rt", encoding="utf-8") as fh: return json.load(fh)
    return json.loads(p.read_text())
