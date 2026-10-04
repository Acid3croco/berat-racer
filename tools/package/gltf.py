"""A minimal binary glTF 2.0 (.glb) writer: one node per file, one primitive per material, positions, normals, two UV sets and
vertex colours. Nothing but numpy, so the package needs no 3D library.

glTF axes: +Y up, right-handed, metres. The package's (x east, y north, z up) maps to glTF (x, z, -y). The node carries the sector's
south-west corner as its translation, so vertices stay small (float32 keeps 0.2 mm at 3.2 km) and the whole file lands in place.
"""
import json
import struct

import numpy as np

ARRAY_BUFFER, ELEMENT_ARRAY_BUFFER = 34962, 34963
FLOAT, UNSIGNED_INT = 5126, 5125


class Mesh:
    """Triangles collected per material: positions (n, 3) in package axes, optional uv0, uv1 (n, 2), colour (n, 3) 0..1.
    Front faces wind counter-clockwise seen from outside (from above for a road)."""

    def __init__(self):
        self.parts = {}

    def add(self, material, positions, triangles, uv0=None, uv1=None, colour=None):
        positions = np.asarray(positions, np.float64).reshape(-1, 3)
        triangles = np.asarray(triangles, np.int64).reshape(-1, 3)
        if not len(triangles):
            return
        n = len(positions)
        part = self.parts.setdefault(material, dict(p=[], t=[], uv0=[], uv1=[], c=[], n=0, has_uv1=False, has_colour=False))
        part["has_uv1"] |= uv1 is not None
        part["has_colour"] |= colour is not None
        part["p"].append(positions)
        part["t"].append(triangles + part["n"])
        part["uv0"].append(np.zeros((n, 2)) if uv0 is None else np.asarray(uv0, float).reshape(n, 2))
        part["uv1"].append(np.zeros((n, 2)) if uv1 is None else np.asarray(uv1, float).reshape(n, 2))
        part["c"].append(np.ones((n, 3)) if colour is None else np.asarray(colour, float).reshape(n, 3))
        part["n"] += n

    def __len__(self):
        return sum(sum(len(t) for t in part["t"]) for part in self.parts.values())

    def merged(self, material):
        """(positions, triangles, uv0, uv1 or None, colour or None): uv1 and colour only when some part of the material gave them."""
        part = self.parts[material]
        return (np.vstack(part["p"]), np.vstack(part["t"]), np.vstack(part["uv0"]),
                np.vstack(part["uv1"]) if part["has_uv1"] else None, np.vstack(part["c"]) if part["has_colour"] else None)


def vertex_normals(positions, triangles):
    """Area-weighted vertex normals (package axes), up where a vertex has no area."""
    a, b, c = (positions[triangles[:, k]] for k in range(3))
    face = np.cross(b - a, c - a)
    normals = np.zeros_like(positions)
    for k in range(3):
        np.add.at(normals, triangles[:, k], face)
    length = np.linalg.norm(normals, axis=1)
    normals[length < 1e-12] = (0.0, 0.0, 1.0)
    return normals / np.maximum(np.linalg.norm(normals, axis=1), 1e-12)[:, None]


def to_gltf(v):
    """Package axes (x east, y north, z up) to glTF (x, y up, z = -north)."""
    v = np.asarray(v, np.float64)
    return np.c_[v[:, 0], v[:, 2], -v[:, 1]]


def write_glb(path, mesh, origin, materials, name):
    """Write `mesh` (package axes, absolute) to `path`, vertices relative to `origin` (x, y) and the node translated there.
    `materials`: {name: dict(colour=(r, g, b), roughness=, metallic=)} for every material the mesh uses (a neutral look: the engine
    replaces the materials by name)."""
    ox, oy = origin
    blob = bytearray()
    accessors, views, primitives, used = [], [], [], []

    def view(data, target):
        while len(blob) % 4:
            blob.append(0)
        views.append(dict(buffer=0, byteOffset=len(blob), byteLength=len(data), target=target))
        blob.extend(data)
        return len(views) - 1

    def accessor(array, kind, component, target, bounds=False):
        a = dict(bufferView=view(array.tobytes(), target), componentType=component, count=len(array), type=kind)
        if bounds:
            a["min"], a["max"] = array.min(axis=0).tolist(), array.max(axis=0).tolist()
        accessors.append(a)
        return len(accessors) - 1

    for material in sorted(mesh.parts):
        p, t, uv0, uv1, c = mesh.merged(material)
        local = p - (ox, oy, 0.0)
        normals = vertex_normals(local, t)
        attributes = dict(POSITION=accessor(to_gltf(local).astype("<f4"), "VEC3", FLOAT, ARRAY_BUFFER, bounds=True),
                          NORMAL=accessor(to_gltf(normals).astype("<f4"), "VEC3", FLOAT, ARRAY_BUFFER),
                          TEXCOORD_0=accessor(uv0.astype("<f4"), "VEC2", FLOAT, ARRAY_BUFFER))
        if uv1 is not None:
            attributes["TEXCOORD_1"] = accessor(uv1.astype("<f4"), "VEC2", FLOAT, ARRAY_BUFFER)
        if c is not None:
            attributes["COLOR_0"] = accessor(np.clip(c, 0, 1).astype("<f4"), "VEC3", FLOAT, ARRAY_BUFFER)
        indices = accessor(t.astype("<u4").ravel(), "SCALAR", UNSIGNED_INT, ELEMENT_ARRAY_BUFFER)      # (x, z, -y) is a rotation: windings keep
        primitives.append(dict(attributes=attributes, indices=indices, material=len(used)))
        used.append(material)

    gltf = dict(asset=dict(version="2.0", generator="berat-racer map package"), scene=0, scenes=[dict(nodes=[0])],
                nodes=[dict(name=name, mesh=0, translation=[float(ox), 0.0, float(-oy)])],
                meshes=[dict(name=name, primitives=primitives)],
                materials=[dict(name=m, pbrMetallicRoughness=dict(baseColorFactor=[*materials[m]["colour"], 1.0],
                                                                  metallicFactor=materials[m].get("metallic", 0.0),
                                                                  roughnessFactor=materials[m].get("roughness", 0.9)))
                           for m in used],
                accessors=accessors, bufferViews=views, buffers=[dict(byteLength=len(blob))])
    if not primitives:
        gltf.pop("meshes"); gltf["nodes"][0].pop("mesh")
    while len(blob) % 4:
        blob.append(0)
    text = json.dumps(gltf, separators=(",", ":")).encode()
    text += b" " * (-len(text) % 4)
    with open(path, "wb") as f:
        f.write(struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(text) + (8 + len(blob) if blob else 0)))
        f.write(struct.pack("<II", len(text), 0x4E4F534A)); f.write(text)
        if blob:
            f.write(struct.pack("<II", len(blob), 0x004E4942)); f.write(bytes(blob))


def read_glb(path):
    """(json, binary chunk) of a .glb: for the checks."""
    data = open(path, "rb").read()
    magic, version, length = struct.unpack_from("<III", data, 0)
    assert magic == 0x46546C67 and version == 2 and length == len(data), f"{path}: not a glb 2.0"
    n, kind = struct.unpack_from("<II", data, 12)
    assert kind == 0x4E4F534A
    doc = json.loads(data[20:20 + n])
    rest = 20 + n
    blob = data[rest + 8:] if rest < len(data) else b""
    return doc, blob


def positions_of(doc, blob):
    """Every primitive's positions back in package axes (absolute), per material name."""
    out = {}
    node = doc["nodes"][0]
    tx, _, tz = node.get("translation", [0, 0, 0])
    for prim in doc.get("meshes", [dict(primitives=[])])[0]["primitives"]:
        acc = doc["accessors"][prim["attributes"]["POSITION"]]
        bv = doc["bufferViews"][acc["bufferView"]]
        v = np.frombuffer(blob, "<f4", acc["count"] * 3, bv["byteOffset"]).reshape(-1, 3).astype(np.float64)
        out[doc["materials"][prim["material"]]["name"]] = np.c_[v[:, 0] + tx, -(v[:, 2] + tz), v[:, 1]]
    return out
