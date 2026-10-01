# Data sources

The world is made from three sources. Each is the best at something and wrong somewhere; the generator should take every
dimension from the source that knows it best, and use the others to fill gaps or to catch errors. This page is the reference
for that split: what each source is, which one leads on which dimension, what the code does today, and what a revamp should change.

In one line: **BD TOPO says where things are and how big, OSM says what they are and the rules on them, the LiDAR says how
high everything is, and settles any conflict about shape or level.**

## The three sources

| | BD TOPO (IGN) | OpenStreetMap | LiDAR HD (IGN) |
|---|---|---|---|
| What | National topographic vector database, surveyed | Volunteer map, mapped from the ground and from imagery | Airborne laser scan; we use its 2 m rasters |
| We read | roads (`troncon_de_route`), buildings (`batiment`), water (`surface_hydrographique`, `troncon_hydrographique`) | drivable highways (`planet_osm_line`), points of interest | `mnt`: bare-ground height; `mnh`: height above ground of whatever stands there (canopy, roofs, decks) |
| Accuracy | 2.5 m planimetric on roads, footprints from the land registry | Varies with the mapper; very good in towns, thin on small roads | ~10 cm in height, 2 m cells (the raw point cloud is 10+ points/m2) |
| Coverage | Complete and uniform over France | Uneven: rich attributes in towns, gaps in the countryside | Complete where flown (the whole map today) |
| Weak at | Relations it codes by hand: bridge / tunnel level can be inverted; widths missing on 28 % of sections; no traffic rules | Geometry and connectivity less consistent; widths, lanes rarely tagged | Says nothing about what a thing is or its rules; bridges and water surfaces are interpolated in `mnt` |
| How we get it | `tools/fetch_*.py` into `tools/data/big/vec` | `python -m roads fetch-osm` (massif-extractor database, read-only) | `tools/fetch_hg.py` into `tools/data/big/hg` |

## Who leads on what

**Primary** decides. **Second** fills in where the primary has nothing. **LiDAR** is the arbiter whenever a height, a level or an
outline is in question.

### Roads

| Dimension | Primary | Second | LiDAR's role | Today |
|---|---|---|---|---|
| Centreline, connectivity | BD TOPO | OSM for roads BD TOPO lacks | Snapping a centreline to the real carriageway (flat strip in `mnt`) | BD TOPO only |
| Carriageway width | BD TOPO surveyed width | OSM `width`; estimate by nature / lanes / class | The flat strip in `mnt` gives a measured width where both lack one | BD TOPO, OSM `width` on 7 ways |
| Lanes, one-way, class, urban | BD TOPO | OSM `lanes` | | BD TOPO, OSM `lanes` fills in |
| Speed limit | OSM `maxspeed` | French legal default by class | | OSM (138 limits on the small map) |
| Surface (paved, gravel), lighting, names | OSM | BD TOPO nature, names | | OSM wins per attribute |
| Height profile | **LiDAR `mnt`** | | Everything: the QP stays on the ground within grade and curvature limits | `roads/profile.py` |
| Which road passes over which | **LiDAR** | OSM `bridge` / `layer` / `cutting` / `maxheight`, then BD TOPO `position_par_rapport_au_sol` | The road whose ground climbs steeply on both sides of the crossing is the upper one; `mnh` gives the deck | `roads/crossing.py` (LiDAR over BD TOPO; OSM tags not read yet) |
| Bridge deck height | **LiDAR `mnt` + `mnh`** (surface model) | | | `roads/profile.py` targets |
| Paths, towpaths, cycleways | OSM | BD TOPO | | not fetched |
| Junction control: signals, stop, give-way, turn restrictions | OSM | rank of the roads (priority to the right) | | give-way lines from ranks only |

Example of why the LiDAR arbitrates levels: at the Pont-canal des Herbettes (Toulouse) BD TOPO surveys the ring road as the
bridge and the towpath on the aqueduct as lying on the ground. OSM has it right (`cutting=yes` and `maxheight` on the ring road,
the ramp over it `bridge=yes layer=1`), and the LiDAR proves it: canal at 145.6 m on both sides, trench floor at 136.3 m.

### Buildings

| Dimension | Primary | Second | LiDAR's role | Today |
|---|---|---|---|---|
| Footprint | BD TOPO (land registry outlines) | OSM `building` for buildings BD TOPO lacks | **Edges**: roof edges are sharp steps in `mnh`; they correct footprints that are offset or out of date, and find new or demolished buildings | BD TOPO, cut out of road corridors |
| Height | **LiDAR `mnh`** at the eaves | BD TOPO `hauteur` (itself LiDAR-derived), `nombre_d_etages` x 3 m, OSM `building:levels` | Measured on every building, not only those with an attribute | BD TOPO `hauteur`, then floors, then `mnh` at the edge (clamped 3 - 12 m) |
| Roof shape, ridge direction | **LiDAR `mnh`** (centre vs edge, line of the maximum) | OSM `roof:shape` | | `mnh`: pitched when the centre stands 0.8 m above the edge |
| Use (house, shop, industry, church...) | OSM `building=*`, `amenity`, `shop` | BD TOPO `usage_1`, `nature` | | BD TOPO `nature` / `usage_1` |
| Names, entrances, addresses | OSM | BD TOPO | | not used |

### Vegetation

| Dimension | Primary | Second | LiDAR's role | Today |
|---|---|---|---|---|
| Where trees stand, how tall | **LiDAR `mnh`** (canopy maxima) | | Everything: each local maximum of the canopy is a tree, its height the tree's | `build_world.py` trees and shrubs |
| Tree concentration (forest, hedge, isolated tree, orchard) | **LiDAR `mnh`** canopy cover and spacing of maxima | OSM `landuse=forest`, `natural=wood`, `leaf_type`; BD TOPO `zone_de_vegetation` | Cover fraction per cell gives density without any vector data | maxima only, no density classes |
| Species / leaf type | OSM `leaf_type`, `leaf_cycle` | BD TOPO vegetation nature | Crown shape (wide, narrow) from the canopy | not used |
| Bare ground colour | Orthophoto | | `mnh` > 1.3 m marks covered ground, removed from the bare colour | `ground_colour` |

### Water

| Dimension | Primary | Second | LiDAR's role | Today |
|---|---|---|---|---|
| Outline of rivers, lakes, canals | BD TOPO | OSM `natural=water`, `waterway` | | BD TOPO |
| Water level | **LiDAR `mnt`** (the surface of water is flat in the scan) | | The level, and where water is carried over a void | mean ground over ~120 m, held below the banks; ground far below the level left out |
| Aqueducts, culverts, weirs | **LiDAR** (water over a void, a road under it) | OSM `bridge=aqueduct`, `tunnel=culvert`, `waterway=weir` | | `carried_water()`: BM06 troughs |
| Streams | BD TOPO lines with width class | OSM `waterway=stream` | Bed height from the lowest ground across | BD TOPO |

### Terrain

| Dimension | Primary | Second | LiDAR's role | Today |
|---|---|---|---|---|
| Ground height | **LiDAR `mnt`** | | Everything (4 m and 16 m meshes, far 64 m mesh) | `build_world.py`, then shaped around the roads |
| Ground colour | Orthophoto | | | 4 m ortho |
| Land use (fields, vineyards, quarries) | OSM `landuse` | BD TOPO | Texture of `mnh` (rows of vines, orchards) | not used |

## Rules for merging

- **Per attribute, not per object.** A road takes its geometry from BD TOPO, its limit from OSM and its height from the LiDAR;
  `roads/osm.py` already matches OSM ways onto BD TOPO sections by position and direction and merges field by field.
- **The LiDAR wins every geometric conflict it can see**: heights, levels at crossings, bridge decks, water levels, roof heights,
  building edges, tree positions. The vector sources keep what the LiDAR cannot see: names, classes, rules, use.
- **When the vector sources disagree on a level** (bridge, tunnel, layer), trust OSM over BD TOPO as the hint, and let the LiDAR decide.
- **Hand corrections** go in `tools/roads/overrides.toml` (per BD TOPO section), never in the code.

## For the revamp

1. Widen the OSM download beyond drivable highways: paths and cycleways, `bridge` / `layer` / `cutting` / `maxheight` on every way,
   waterways (`bridge=aqueduct`, culverts, weirs), `building=*` with `building:levels` and `roof:shape`, `landuse` / `natural` / `leaf_type`.
2. Read OSM levels in `roads/crossing.py` as the first hint before the LiDAR test.
3. Buildings: correct footprints against the edges in `mnh`; take the height from `mnh` for every building; roof shape from OSM where tagged.
4. Vegetation: classify canopy cover per cell (forest, hedge, isolated trees) and use OSM / BD TOPO vegetation for the kind.
5. Consider the classified LiDAR HD point cloud (class 6 buildings, 3 - 5 vegetation, 9 water, 17 bridge decks) where the 2 m rasters blur
   edges: it separates roofs from trees and decks from the ground directly.
