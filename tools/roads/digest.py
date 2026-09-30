"""Hashes of the inputs of a build step. A step whose inputs hash as they did last time is skipped and its stored result reused
(tiles of the height solve, sectors of the world build).

A key is made of three things: the data the step is handed (`digest`), the data files it reads (`rasters.stamp`, `rasters.file_stamp`:
name, size, modification time) and the code and tuning it runs with (`code_stamp`).
"""
import dataclasses
import hashlib
import inspect
import re

import numpy as np
import shapely

from . import config

PLAIN = (str, bytes, int, float, bool, type(None), np.generic)


def digest(*values, skip=()):
    """Hash of nested values: arrays, dataclasses, lists, tuples, dicts, geometries, numbers, strings. `skip`: dataclass field names left out."""
    h = hashlib.blake2b(digest_size=16)
    for value in values:
        _feed(h, value, frozenset(skip))
    return h.hexdigest()


def _feed(h, value, skip):
    if isinstance(value, np.ndarray) and value.dtype != object:
        h.update(f"<{value.dtype.str} {value.shape}>".encode())
        h.update(np.ascontiguousarray(value).tobytes())
    elif dataclasses.is_dataclass(value) and not isinstance(value, type):
        h.update(f"<{type(value).__name__}>".encode())
        for f in dataclasses.fields(value):
            if f.name not in skip:
                h.update(f.name.encode())
                _feed(h, getattr(value, f.name), skip)
    elif isinstance(value, dict):
        h.update(b"{")
        for key in sorted(value, key=repr):
            _feed(h, key, skip)
            _feed(h, value[key], skip)
        h.update(b"}")
    elif isinstance(value, (list, tuple, np.ndarray)):
        h.update(b"[")
        for item in value:
            _feed(h, item, skip)
        h.update(b"]")
    elif isinstance(value, shapely.Geometry):
        h.update(value.wkb)
    elif isinstance(value, PLAIN):
        h.update(f"<{type(value).__name__}>{value!r}".encode())
    else:
        raise TypeError(f"cannot hash a {type(value).__name__}: teach roads.digest about it")


def code_stamp(*modules):
    """Hash of the source of `modules`, of the tuning values (`config.NAME`) that source names, of the road classes and of the
    versions of the libraries that do the arithmetic. Any edit of those modules changes it: safe, at the price of a full rebuild."""
    sources = [inspect.getsource(module) for module in modules]
    names = sorted({name for source in sources for name in re.findall(r"config\.([A-Z][A-Z0-9_]*)", source)})
    tuning = {name: getattr(config, name) for name in names if name != "CLASSES"}
    import scipy
    return digest(sources, tuning, list(config.CLASSES.values()), [np.__version__, scipy.__version__, shapely.__version__, shapely.geos_version_string])
