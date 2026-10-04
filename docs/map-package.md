# Map package

The map is the product. Everything else (rendering, lighting, LOD, streaming, physics, camera, UI) comes from the engine: Unreal
Engine 5 on Windows. The pipeline in `tools/` stops at a **map package**: the world as plain, documented data in standard formats,
which any engine, Blender or QGIS can open. The engine side is a thin importer.

The package holds *what the world is* (heights, classes, road geometry and rules, building measures, tree positions), plus meshes
only where the shape itself is the work of the pipeline (road surfaces and junctions, buildings and roofs). Textures, materials,
tree models and the look are chosen in the engine.

## Coordinates

| | |
|---|---|
| Projection | Lambert-93, EPSG:2154 |
| Origin | `CX, CY = 551972, 6254819` (Bérat), stored in `manifest.json` |
| Package axes | metres: **x east, y north, z up**, z in metres NGF (IGN69) |
| Grid | sectors of 3200 m; sector `(si, sj)` covers x in `[3200·si − 16000, +3200)`, y the same with `sj` (the grid of `tools/sources.py`) |
| Unreal | `UE.X = x·100`, `UE.Y = −y·100`, `UE.Z = z·100` (cm, left-handed), done by the importer only |

Every file is in package coordinates. The Lambert-93 position of a point is `(CX + x, CY + y)`.

## Layout

```
package/<tag>/
  manifest.json
  sectors/<si>_<sj>/
    height.png          16-bit grey, 1601 x 1601, 2 m, the ground carved under the roads
    holes.png           8-bit, 1601 x 1601: 255 where the terrain is cut away (tunnel portals, under road decks it must not poke through)
    classes.png         8-bit, 1601 x 1601: land-cover class per cell (table below)
    rows.png            8-bit, 1601 x 1601: row direction of rowed crops, 1 + 254·angle/π, 0 = none
    colour.jpg          800 x 800, 4 m: the bare-ground colour (orthophoto with canopy and roads removed), for the far view and tints
    roads.glb           road pieces and junction surfaces, one mesh per material
    roads.geojson       road centrelines (LineString Z) with their attributes
    buildings.glb       walls and roofs
    buildings.geojson   footprints (Polygon) with their measures and use
    vegetation.json.gz  trees, shrubs, hedges, vine rows
    water.geojson       water areas (Polygon with level) and streams (LineString Z with width)
    lanes.json.gz       the lane graph of the sector
    places.json         place names, points of interest, car-park bays, spawn points
```

Rasters share their edge row and column with the next sector (1601 = 1600 cells + 1), row 0 is the **north** edge.
A line or polygon belongs to the sector that holds its first point; meshes are cut at the sector edge. Ids are global, so a lane
or road continued in the next sector is found by id.

## manifest.json

```json
{
  "format": "berat-map-package", "version": 1, "tag": "berat70scale",
  "crs": "EPSG:2154", "origin": [551972, 6254819], "z_datum": "NGF-IGN69",
  "sector_size": 3200, "cell": 2.0, "samples": 1601,
  "sectors": [[-6, -6], ...],
  "height": {"min": 95.0, "max": 1150.0},
  "classes": ["none", "meadow", "cereal", ...],
  "materials": {"roads": ["asphalt", "gravel", "dirt", "marking"], "buildings": ["wall", "roof_tile", ...]},
  "sources": [{"name": "LiDAR HD", "by": "IGN", "licence": "Licence Ouverte 2.0", "date": "..."}, ...],
  "built": {"commit": "...", "date": "...", "seconds": 0}
}
```

`height.png` decodes as `z = min + v / 65535 · (max − min)`. The range is the package's own, so the step stays under 2 cm (a
1000 m range gives 1.5 cm). Unreal imports 16-bit PNG landscapes directly; its Z scale follows from the range.

## Layers

### Terrain: `height.png`, `holes.png`

- Ground = LiDAR HD `mnt` at 2 m (RGE ALTI where the LiDAR is missing).
- **Carved under the roads in the raster itself**: under every paved piece and junction the ground takes the road surface minus
  `ROAD_SINK` (0.05 m), then blends back to the LiDAR over the shoulders (the `stitch.EdgeField` smoothstep). The road mesh then
  always lies on the terrain, with no seam mesh. *Today this exists only inside the BM08 chunks; writing it out is new work.*
- Water beds scooped below the water level, stream beds cut (`WATER_DEPTH`), ground dropped under bridge decks as today.
- `holes.png` marks cells the engine must not draw: tunnel portals, terrain that would show through a deck.

### Land cover: `classes.png`, `rows.png`, `colour.jpg`

One class per 2 m cell, from `tools/ground.py` (`ground.areas`, `ground.rasterize`), in this priority: specific OSM / BD TOPO
vegetation and transport areas, then the RPG crop of the parcel, then generic OSM land use.

| id | class | | id | class |
|---|---|---|---|---|
| 0 | none (bare / unknown) | | 7 | parking |
| 1 | meadow | | 8 | garden |
| 2 | cereal | | 9 | yard |
| 3 | row crop | | 10 | forest |
| 4 | vineyard | | 11 | cemetery |
| 5 | orchard | | 12 | pitch |
| 6 | fallow | | 13 | scrub |

The ids are `ground.NAMES`; the manifest carries the list, so adding a class does not break an importer. The engine maps each
class to a landscape layer (a material) and blends them. `rows.png` lets a material turn its furrows along the real rows.
`colour.jpg` is the real colour for the far view and for a tint, never the close-up texture.

### Roads: `roads.glb`, `roads.geojson`

- `roads.glb`: the drawn surfaces from the surface stage (`surface.Piece` left/right 3D edges, junction triangles), with UVs:
  `u` across the carriageway (0 left edge, 1 right edge), `v` along it in metres, so markings and textures run along the road.
  One mesh per material (asphalt, gravel, dirt). Lane markings as a separate thin mesh with the `marking` material, from
  `line_offsets` / `line_kinds`.
- `roads.geojson`: one feature per link, `LineString Z` (the profiled centreline), properties:

| property | meaning |
|---|---|
| `id`, `bdtopo`, `osm` | link id, BD TOPO `cleabs`, OSM way id |
| `class` | motorway, ramp, main, collector, local, street, roundabout, track (`config.CLASSES`) |
| `width`, `width_measured` | carriageway width (m), and whether it was surveyed |
| `lanes_forward`, `lanes_backward`, `oneway` | lane counts |
| `limit_forward`, `limit_backward` | km/h |
| `surface`, `lit`, `name`, `number` | as in OSM / BD TOPO |
| `bridge`, `tunnel`, `level` | per link; a link changes level only at a split |
| `tilt` | cross slope per vertex (array, same length as the coordinates) |

### Buildings: `buildings.glb`, `buildings.geojson`

- `buildings.glb`: walls and roofs as built today (`roofs.building_roof`, `facades.py`), one mesh per material, UVs in metres.
  A first look; the engine may later dress them itself from the attributes.
- `buildings.geojson`: footprint `Polygon`, properties `id` (BD TOPO), `use` (house, shed, barn, industrial, shop, church, chapel,
  silo, greenhouse), `name`, `base` (ground z), `eave`, `ridge` (absolute z), `roof` (flat, hipped, gabled), `pitch`, `floors`,
  `era`, `wall`, `roof_colour` (sRGB from the orthophoto), `party_walls`.

### Vegetation: `vegetation.json.gz`

```json
{"trees":  {"columns": ["x", "y", "z", "height", "kind"], "rows": [[...], ...]},
 "shrubs": {"columns": ["x", "y", "z", "height"], "rows": [...]},
 "hedges": [{"height": 2.5, "points": [[x, y, z], ...]}, ...],
 "vines":  [[x0, y0, x1, y1], ...]}
```

Tree kinds: 0 unknown, 1 broadleaf, 2 conifer, 3 poplar, 4 fruit. A tree is a canopy maximum of the LiDAR `mnh`; its height is
measured. The engine picks a model per kind and scales it to the height.

### Water: `water.geojson`

Areas: `Polygon` with `level` (z of the surface) and `kind` (river, lake, canal, reservoir). Streams: `LineString Z` (the level per
vertex never runs uphill) with `width`. Aqueduct troughs carry `carried: true`.

### Lane graph: `lanes.json.gz`

The `lanegraph.Element`s of the sector: `id`, `kind` (lane, change, connector, uturn), `points` (x, y, z), `speed` per point,
`next` (ids), `left`, `right`, `control` (priority, give_way, stop, signals, right), `yields` (ids), `turn`, `junction`, `limit`,
`road` (the road link id). Traffic and the autopilot need nothing else.

### Places: `places.json`

Place names (`name`, `kind`, `population`, position), points of interest (amenity / shop, name, position), car-park bays
(`x, y, angle, occupied`), spawn points.

## Licences

The package is derived from IGN data (Licence Ouverte 2.0: attribution) and OpenStreetMap (ODbL: attribution, and a derived
*database* must stay under ODbL). The manifest lists every source; the game credits them. Check the ODbL share-alike point before
selling anything built on OSM attributes.

## Not in the package

Textures, materials, tree and prop models, lighting, sky, LOD, collision, streaming: the engine's job. Engine-specific encodings
(axis swaps, per-chunk quantisation, triangle winding, byte planes) are the importer's job, never the package's.
