"""Stage 1: read the surveyed roads (BD TOPO `troncon_de_route`) of an area and turn each one into an `Edge` with normalised attributes.

BD TOPO is the authority for geometry, width, lanes, class and bridges. OpenStreetMap (see `osm.py`) fills in what it knows better:
surface, posted speed limits, street lighting. `overrides.toml` has the last word, per road.
"""
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

import numpy as np

from fetch import CX, CY
from rasters import BIG, HALF, SECTOR
from vec_io import exists_vec, read_vec

from . import config

OVERRIDES = Path(__file__).with_name("overrides.toml")

SKIP_NATURE = {"Sentier", "Escalier", "Bac ou liaison maritime"}
DIRT_NATURE = {"Chemin", "Route empierrée"}
KIND = {"Rond-point": 1, "Route à 2 chaussées": 2, "Type autoroutier": 3, "Bretelle": 4, "Chemin": 5, "Route empierrée": 5}     # 0 = ordinary road
ONEWAY = {"Sens direct": 1, "Sens inverse": 2}          # 0 = both ways; 1 = along the digitised direction; 2 = against it


@dataclass
class Edge:
    """One surveyed road section between two nodes of the network."""
    cleabs: str
    xy: np.ndarray                # (n, 2) local metres, digitised direction
    nature: str
    importance: str
    urban: bool
    kind: int
    klass: str                    # key of config.CLASSES
    width_real: float             # surveyed carriageway width (or the best guess)
    width_surveyed: bool
    width: float                  # drawn carriageway width
    lanes: int
    oneway: int
    limit: int                    # km/h
    avg: int                      # BD TOPO average speed of light vehicles, 0 = unknown
    dirt: bool
    surface: str
    bridge: bool
    tunnel: bool
    lit: bool = False
    name: str = ""
    number: str = ""
    osm_id: int = 0
    a: int = -1                   # node at xy[0]
    b: int = -1                   # node at xy[-1]
    tags: dict = field(default_factory=dict)

    @property
    def length(self):
        return float(np.hypot(*np.diff(self.xy, axis=0).T).sum())

    @property
    def road_class(self):
        return config.CLASSES[self.klass]


def _number(value, default=0.0):
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def real_width(p):
    """Surveyed carriageway width, or the best guess from the lane count, the nature and the importance of the road."""
    w = _number(p.get("largeur_de_chaussee"))
    if w > 0:
        return max(w, 2.5)
    nature = p["nature"]
    if nature in config.WIDTH_BY_NATURE:
        return config.WIDTH_BY_NATURE[nature]
    lanes = int(_number(p.get("nombre_de_voies")))
    if lanes > 0:
        return config.WIDTH_BY_LANES.get(lanes, config.LANE_WIDTH * lanes)
    return config.WIDTH_BY_IMPORTANCE.get(str(p.get("importance")), config.WIDTH_DEFAULT)


def drawn_width(real, dirt):
    return max(real * config.WIDTH_SCALE, config.MIN_WIDTH_DIRT if dirt else config.MIN_WIDTH_PAVED)


def legal_limit(p):
    """French speed limit for a BD TOPO road: motorway 130, dual carriageway 110, built-up areas 50, other roads 80, tracks 30, slip roads 70."""
    nature, urban = p.get("nature"), str(p.get("urbain")).lower() == "true"
    if nature == "Type autoroutier":
        return 130
    if nature == "Route à 2 chaussées":
        return 70 if urban else 110
    if nature == "Bretelle":
        return 70
    if nature in DIRT_NATURE:
        return config.LIMIT_TRACK
    if nature == "Rond-point":
        return 30 if urban else 50
    return config.LIMIT_URBAN if urban else config.LIMIT_RURAL


def classify(p):
    nature, importance = p.get("nature"), str(p.get("importance"))
    if nature == "Rond-point":
        return "roundabout"
    if nature in DIRT_NATURE:
        return "track"
    if importance in ("1", "2", "3") or nature in ("Type autoroutier", "Route à 2 chaussées", "Bretelle"):
        return "main"
    if importance == "4":
        return "collector"
    return "street" if str(p.get("urbain")).lower() == "true" else "local"


def edge_from_feature(feature):
    p = feature["properties"]
    nature = p["nature"]
    position = str(p.get("position_par_rapport_au_sol") or "0")
    dirt = nature in DIRT_NATURE
    real = real_width(p)
    xy = np.asarray(feature["geometry"]["coordinates"], float)[:, :2] - np.array([CX, CY], float)
    return Edge(
        cleabs=p["cleabs"], xy=xy, nature=nature, importance=str(p.get("importance")), urban=str(p.get("urbain")).lower() == "true",
        kind=KIND.get(nature, 0), klass=classify(p), width_real=real, width_surveyed=_number(p.get("largeur_de_chaussee")) > 0,
        width=drawn_width(real, dirt),
        lanes=int(min(15, max(0, _number(p.get("nombre_de_voies"))))), oneway=ONEWAY.get(p.get("sens_de_circulation"), 0),
        limit=legal_limit(p), avg=int(min(255, max(0, _number(p.get("vitesse_moyenne_vl"))))),
        dirt=dirt, surface="dirt" if dirt else "asphalt",
        bridge=position not in ("0", "") and not position.startswith("-") and position[0].isdigit(),
        tunnel=position.startswith("-"),
        name=next((p[k] for k in ("nom_1_gauche", "nom_1_droite", "nom_voie_ban_gauche", "nom_voie_ban_droite") if p.get(k)), ""),
        number=p.get("cpx_numero") or "")


def area_of(sectors, margin):
    """Bounding box (x0, z0, x1, z1) of a sector list plus a margin, in local metres."""
    si = [s[0] for s in sectors]
    sj = [s[1] for s in sectors]
    return (-HALF + SECTOR * min(si) - margin, -HALF + SECTOR * min(sj) - margin,
            -HALF + SECTOR * (max(si) + 1) + margin, -HALF + SECTOR * (max(sj) + 1) + margin)


def load_features(sectors):
    """Raw BD TOPO road features of the sectors and their neighbours, de-duplicated on `cleabs`."""
    seen = {}
    wanted = {(i + di, j + dj) for i, j in sectors for di in (-1, 0, 1) for dj in (-1, 0, 1)}
    for i, j in sorted(wanted):
        base = BIG / "vec" / f"roads_{i}_{j}"
        if not exists_vec(base):
            continue
        for feature in read_vec(base):
            seen.setdefault(feature["properties"]["cleabs"], feature)
    return list(seen.values())


def load_overrides(path=OVERRIDES):
    if not path.exists():
        return {}
    return tomllib.loads(path.read_text())


def apply_overrides(edges, overrides):
    """Per-road hand corrections: `skip = true` drops the road, any other key replaces the `Edge` field of that name."""
    known = {f.name for f in fields(Edge)}
    out = []
    for e in edges:
        o = overrides.get(e.cleabs)
        if o is None:
            out.append(e)
            continue
        if o.get("skip"):
            continue
        for key, value in o.items():
            if key not in known:
                raise KeyError(f"overrides.toml: {e.cleabs}: unknown field {key!r}")
            setattr(e, key, value)
        if "width_real" in o and "width" not in o:
            e.width = drawn_width(e.width_real, e.dirt)
        out.append(e)
    return out


def load_edges(sectors, margin):
    """Edges of every drivable road that touches the area of the sector list (grown by `margin`)."""
    x0, z0, x1, z1 = area_of(sectors, margin)
    edges = []
    for feature in load_features(sectors):
        p = feature["properties"]
        if p["nature"] in SKIP_NATURE or p.get("etat_de_l_objet") not in (None, "En service"):
            continue
        e = edge_from_feature(feature)
        lo, hi = e.xy.min(axis=0), e.xy.max(axis=0)
        if hi[0] < x0 or lo[0] > x1 or hi[1] < z0 or lo[1] > z1 or e.length < 1e-3:
            continue
        edges.append(e)
    return edges
