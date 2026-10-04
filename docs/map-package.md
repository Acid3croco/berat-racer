# Map package

The map is the product. Everything else (rendering, lighting, LOD, streaming, physics, camera, UI) comes from the engine: Unreal
Engine 5 on Windows. The pipeline in `tools/` stops at a **map package**: the world as plain, documented data in standard formats,
which any engine, Blender or QGIS can open. The engine side is a thin importer.

The package holds *what the world is* (heights, classes, road geometry and rules, building measures, tree positions), plus meshes
only where the shape itself is the work of the pipeline (road surfaces and junctions, buildings and roofs). Textures, materials,
tree models and the look are chosen in the engine; the package's materials are names with a neutral colour.

```sh
cd tools
uv run python -m roads   --list data/big/berat70scale_sectors.json build            # the road pipeline (roads/)
uv run python -m package --list data/big/berat70scale_sectors.json fetch-hires      # LiDAR ground 0.5 m, orthophoto 0.2 m (optional)
uv run python -m package --list data/big/berat70scale_sectors.json build --cell 1   # -> ../package/berat70scale
uv run python -m package --out ../package/berat70scale check                        # decode, measure, previews
```

Code: `tools/package/` (`export.py` the writer, `terrain.py` the carved terrain, `roads.py`, `buildings.py`, `gltf.py` a numpy
glTF writer, `hires.py` the fine sources, `check.py`). Each sector is computed by `build_world.compute_sector`, the same terrain,
water, buildings, plants and ground classes as the Unity world build.

## Coordinates

| | |
|---|---|
| Projection | Lambert-93, EPSG:2154 |
| Origin | `CX, CY = 551972, 6254819` (Bérat), in `manifest.json` (`origin`) |
| Package axes | metres: **x east, y north, z up**, z in metres NGF (IGN69). Lambert-93 of a point = `(CX + x, CY + y)` |
| Grid | sectors of 3200 m; sector `(si, sj)` covers x in `[3200 si - 16000, +3200)`, y the same with `sj` (the grid of `tools/sources.py`) |
| glTF | glTF's own axes (+Y up, right-handed): package `(x, y, z)` is glTF `(x, z, -y)`; each file's node is translated to its sector's south-west corner, vertices are relative to it |
| Unreal | `UE.X = x * 100`, `UE.Y = -y * 100`, `UE.Z = z * 100` (cm, left-handed); the glTF importer converts glTF axes itself |

## Layout

```
package/<tag>/
  manifest.json          what the package is (below)
  report.json            per sector: timings, counts, the road-on-terrain measures
  check.json             the last `check`
  previews/              <si>_<sj>.png (hillshade x classes, water, roads, buildings), overview.png
  sectors/<si>_<sj>/
    height.png           16-bit grey, n x n (n = 3200 / cell + 1), row 0 north: the terrain carved under the roads
    height.tif           the same heights as float32 GeoTIFF, EPSG:2154, pixel centres on the vertices (QGIS, checks)
    holes.png            8-bit, n x n: 255 on vertices of cells cut away (tunnel portals)
    classes.png          8-bit, n x n: land-cover class per vertex (table below)
    rows.png             8-bit, n x n: row direction of rowed crops, 1 + 254 * angle / pi (angle from east, counter-clockwise), 0 none
    colour.jpg           801 x 801, 4 m a pixel: bare-ground colour (orthophoto with canopy and roads replaced, graded)
    ortho.jpg            800 x 800, 4 m: the orthophoto as served
    ortho20/<a>_<b>.jpg  4 x 4 tiles of 800 m, 4000 x 4000, 0.2 m: the orthophoto (a east, b north from the sector's south-west), when fetched
    roads.glb            carriageways, junctions, car parks, skirts, bridge deck sides, markings
    roads.geojson        road pieces: centreline LineString Z and attributes
    lanes.json.gz        the lane graph of the sector
    buildings.glb        walls and roofs
    buildings.geojson    footprints and every measure, facade layout
    vegetation.json.gz   trees, shrubs, hedges, vine rows
    water.geojson        water areas (with the level at every outline point), streams, aqueduct troughs
    places.json          place names, points of interest, parking bays
    stats.json           what the sector's export measured
```

Rasters share their edge row and column with the next sector (n = cells + 1), and the shared vertices are **identical** in both
sectors (`check`: seams 0.000 m). Ownership of everything else: a road segment belongs to the sector holding its midpoint, a
junction to the one holding its centre, a car park, building or tree to the one holding its centre / mean point, a lane to the one
holding its first point, a road piece's attributes to the one holding its first drawn segment; water areas are cut at the sector
edge. Ids are global, so a lane continued in the next sector is found by id.

## manifest.json

```json
{
  "format": "berat-map-package", "version": 1, "tag": "berat70scale",
  "crs": "EPSG:2154", "origin": [551972, 6254819], "z_datum": "NGF-IGN69", "axes": "x east, y north, z up, metres from origin",
  "sector_size": 3200, "sector_origin": "x = 3200 si - 16000, y = 3200 sj - 16000",
  "cell": 1.0, "samples": 3201, "sectors": [[-6, -6], ...], "files": ["buildings.geojson", ...],
  "ortho20": {"cell": 0.2, "tiles": "4 x 4 per sector, ...", "sectors": 484},
  "height": {"min": 95.0, "max": 1150.0, "png": "z = min + v / 65535 * (max - min)"},
  "classes": ["none", "meadow", ...], "tree_kinds": ["unknown", "broadleaf", ...], "openings": ["window", "door", ...],
  "materials": {"roads": ["asphalt", "deck", "dirt", "paint"], "buildings": ["roof", "wall"]},
  "road_width_scale": 1.33, "spawn": {"x": 54.94, "y": -33.74, "z": 245.72, "heading": 31.7, "road": "Route de Gratens"},
  "sources": [{"name": "LiDAR HD (MNT, MNH)", "by": "IGN", "licence": "Licence Ouverte 2.0", "use": "..."}, ...],
  "built": {"commit": "...", "date": "...", "seconds": 0, "jobs": 5}
}
```

The height range is the package's own: one 16-bit step is `(max - min) / 65535` (1.2 mm on the small map). Unreal imports 16-bit
PNG heightmaps directly; its Z scale follows from the range. `spawn.heading` is in degrees clockwise from north.

## Terrain: `height.png`, `height.tif`, `holes.png`

`--cell 2` samples the 2 m LiDAR HD tiles; `--cell 1` and `--cell 0.5` sample the 0.5 m ones (`fetch-hires`; the 2 m tiles,
themselves filled from RGE ALTI, where the LiDAR has no ground). Measured at Bérat: 1 m holds 8 cm rms (34 cm p99) more than 2 m,
0.5 m another 5.7 cm (25 cm p99): ditches, banks, kerbs.

1. **Ground**: the LiDAR at the vertices, plus the world build's changes on its 4 m grid (water beds scooped under the water level,
   stream beds cut 0.45 m, ground dropped up to 1.4 m under bridge decks), carried over as a difference.
2. **Roads**: under every ground-level paved surface (carriageways, junctions, car parks; not bridges, not tunnels) the ground is
   the surface less 5 cm; around them the `stitch.py` edge field blends from the paved edges back to the ground (smoothstep, 3 to 14 m).
3. **Shoulder**: a vertex of a cell the paved surface touches, outside it, takes the nearest edge's height less 5 cm.
4. **Cap**: wherever the bilinear terrain stands within 2 cm of a paved edge (sampled every half cell), the corners of that cell are
   lowered by the excess, round after round. **The terrain never stands over a road**: measured on every edge and junction vertex,
   it lies 2 cm or more under it; where two roads at different levels are closer than a cell (a retaining wall) the gap under the
   upper road's edge is closed by its skirt (below).
5. **Holes**: the 4 m world's tunnel-portal cells.

`check` and `report.json` give, per sector, the terrain under the drawn road edges: small map at 2 m, never above, 7.5 cm at the 99th
percentile (5 cm is the design), 0.74 m at most (the retaining walls).

## Land cover: `classes.png`, `rows.png`, `colour.jpg`, `ortho*.jpg`

One class per vertex, from `tools/ground.py` (`ground.areas`, `ground.rasterize`): specific OSM / BD TOPO vegetation and transport
areas first, then the RPG crop of the field, then generic OSM land use; car parks are `parking`.

| id | class | | id | class |
|---|---|---|---|---|
| 0 | none (bare / unknown) | | 7 | parking |
| 1 | meadow | | 8 | garden |
| 2 | cereal | | 9 | yard |
| 3 | row crop | | 10 | forest |
| 4 | vineyard | | 11 | cemetery |
| 5 | orchard | | 12 | pitch |
| 6 | fallow | | 13 | scrub |

The manifest carries the list (`classes`), so a new class does not break an importer. The engine maps each class to a landscape
layer (a material) and blends them; `rows.png` turns furrows, vine and orchard rows along the real ones. `colour.jpg` is the real
ground colour for the far view and tints; `ortho20` is the close-up reference (and a far texture), never a substitute for materials.

## Roads: `roads.glb`, `roads.geojson`, `lanes.json.gz`

`roads.glb`, one primitive per material:

| material | what | UV0 | UV1 |
|---|---|---|---|
| `asphalt`, `dirt` | carriageway strips (vertices shared along a road), junctions, car parks; skirts | carriageway: (m from the left edge, m along the link); junctions, car parks: plan metres (x, y); skirts: (m along, m down) | carriageway: (fraction of the width, m along) |
| `deck` | both sides of a bridge deck, 1.2 m deep, double-sided | (m along, m down) | |
| `paint` | French markings, 2 cm over the surface | plan metres | |

- Faces wind counter-clockwise seen from outside: surfaces and paint face up, skirts and deck sides face out (`check` measures it).
- **Skirts**: a 1.2 m strip hangs under every edge of a ground-level carriageway and every kerb of a junction, so no gap shows
  where the terrain dips under an edge.
- **Widths**: drawn carriageway = surveyed width x 1.33 (`road_width_scale`), at least 5.0 m for a paved two-way road; the
  surveyed width stays in the attributes.
- **Markings**, from `roads/lanes.py` (the lanes of every point) by the game's rules (Unity `ChunkMeshes.cs`): centre line 3 m dashes
  every 9 m, solid where overtaking is forbidden both ways, solid beside dashed where one way may (the solid on the side it stops);
  lane dividers 3 m every 6 m (13 m on dual carriageways); edge lines 0.22 m inside the edge, solid or 3 m every 6.5 m by the drawn
  width; give-way blocks; turn arrows. Dashes are laid out by distance along the whole link, so they stay in step across sectors.
  Tracks and dirt roads are not painted.

`roads.geojson`, one feature per road piece (a link between junctions, cut where its level changes), `LineString Z` (the profiled
centreline, 1.3 to 2 m apart):

| property | meaning |
|---|---|
| `id` | `<link>@<distance it starts at>`; `link` is the first BD TOPO section's `cleabs` and `+` / `-` |
| `bdtopo`, `osm` | BD TOPO `cleabs`, OSM way id |
| `class` | motorway, ramp, main, collector, local, street, roundabout, track (`roads/config.py` `CLASSES`) |
| `nature`, `importance`, `urban` | BD TOPO |
| `width_real`, `width_surveyed`, `width_drawn` | surveyed carriageway width (m) and whether it was surveyed; mean drawn width |
| `lanes_forward`, `lanes_backward`, `oneway` | lanes along / against the piece; oneway 0 both ways, 1 along, 2 against |
| `limit_forward`, `limit_backward` | km/h |
| `surface`, `dirt`, `lit`, `name`, `number` | OSM / BD TOPO |
| `bridge`, `tunnel` | the piece is carried / underground |
| `s`, `half_width`, `tilt` | per vertex: distance along the link (m), drawn half width (m), cross slope (rise over run, + = left up) |
| `drawn_from`, `drawn_to` | the vertices the carriageway is drawn between (the rest lies in junctions) |

`lanes.json.gz`: `{"controls": [...], "elements": [...]}`, each element a lane, lane change, junction connector or U-turn
(`roads/lanegraph.py`): `id`, `kind` (0 lane, 1 change, 2 connector, 3 U-turn), `points` (x, y, z), `speed` per point (m/s the
curvature and the limit allow), `next` (ids), `left`, `right` (the lane beside it, same direction, -1 none), `control` (index into
`controls`: priority, give_way, stop, signals, right = priority to the right), `yields` (connectors this one gives way to), `turn`
(degrees, left positive), `junction`, `limit` (km/h), `road` (kind, importance, rank, half width, dirt). Traffic and an autopilot
need nothing else.

## Buildings: `buildings.glb`, `buildings.geojson`

`buildings.glb`: `wall` (walls from `base` to the eave, gable ends of the roofs; UV0 m along the wall, m up) and `roof` (UV0 plan
metres), vertex colours from the data (roof: orthophoto; wall: a palette by seed). Walls face out, roofs up. Openings are data, not
geometry: the engine cuts or decals them.

`buildings.geojson`, `Polygon` (outline counter-clockwise, courtyards as holes), properties:

| property | meaning |
|---|---|
| `id` | BD TOPO `cleabs` |
| `use` | house, shed, barn, industrial, shop, church, chapel, silo, greenhouse; from a point of interest inside: pharmacy, townhall, school, post, restaurant, bar, library, hall, clinic, bakery, grocery |
| `name` | from the point of interest |
| `ground`, `eave`, `ridge` | absolute z (m): lowest ground, eave, ridge. Heights: BD TOPO roof altitudes when within 2.5 m of the LiDAR surface model, else the LiDAR |
| `wall_height`, `roof_rise`, `roof`, `pitch` | roof: flat, hipped, gabled (straight skeleton, or OSM `roof:shape`); pitch in degrees |
| `floors`, `floor_height` | BD TOPO floors, OSM `building:levels`, else from the height |
| `era` | before_1950, 1950_1970, after_1970, unknown (BD TOPO `date_d_apparition`) |
| `wall_material`, `roof_material_code` | BD TOPO `materiaux_des_murs` (stone, millstone, concrete, brick, breeze_block, wood, other), `materiaux_de_la_toiture` (first digit, raw) |
| `roof_colour`, `wall_colour` | sRGB 0-255 |
| `front_wall` | index of the outline edge facing the nearest road |
| `tower` | church tower: `x`, `y`, `height` above the ground (LiDAR), or null |
| `walls` | the facade layout (`tools/facades.py`): per wall `first` outline point, `count` edges, `front`, `party` (shared), `blind`, `back`, `ground` at both ends, `openings`: `at` (m along the wall), `floor`, `type` (window, door, garage, shopfront, balcony), `width`, `height`, `sill` (m over the local ground) |
| `seed` | stable per building: the same building keeps its look |

## Vegetation: `vegetation.json.gz`

```json
{"trees":  {"columns": ["x", "y", "z", "height", "kind"], "kinds": ["unknown", "broadleaf", "conifer", "poplar", "fruit"], "rows": [...]},
 "shrubs": {"columns": ["x", "y", "z", "height"], "rows": [...]},
 "hedges": [{"height": 2.5, "points": [[x, y, z], ...]}, ...],
 "vines":  {"columns": ["x0", "y0", "z0", "x1", "y1", "z1"], "rows": [...]}}
```

A tree is a canopy maximum of the LiDAR `mnh` (2.5 to 32 m), its height measured; kind from BD TOPO vegetation and orchards. Shrubs
are the 0.9 to 2.5 m maxima (thinned to ~17k per sector, none in vineyards or on hedges). `z` is the carved terrain. The engine picks
a model per kind and scales it to the height.

## Water: `water.geojson`

Areas: `Polygon` whose coordinates carry the water level as z at every point, `kind` water or carried (an aqueduct trough), `level`
(mean), `area`. Streams: `LineString Z` (the level never runs uphill), `kind` stream, `width`. Levels come from the LiDAR (the mean
ground over ~120 m of water, held under the banks, voids under a canal on an embankment left out).

## Places: `places.json`

`places` (name, kind, rank, population, x, y), `pois` (OSM id, amenity, shop, name, kind, x, y), `parking_bays` (x, y, angle,
occupied).

## Checks (`python -m package check`)

Heights decode within one 16-bit step and seams match; rasters have the right size and class ids; every .glb parses (the Khronos
glTF validator also reports 0 errors, 0 warnings), has no NaN and stays by its sector; faces turned the wrong way (under 0.1 %:
small map 13 of 811,078, walls between adjoining buildings the probe cannot tell); ids unique; lane links resolve (except to lanes
beyond the package's border); the terrain never above a road. `check.json` holds the numbers.

## Licences

The package is derived from IGN data (Licence Ouverte 2.0: attribution) and OpenStreetMap (ODbL: attribution, and a derived
*database* must stay under ODbL). The manifest lists every source; the game credits them. Check the ODbL share-alike point before
selling anything built on OSM attributes.

## Not in the package

Textures, materials, tree and prop models, lighting, sky, LOD, collision, streaming: the engine's job. Engine-specific encodings
(axis swaps, per-chunk quantisation, byte planes) are the importer's job, never the package's.
