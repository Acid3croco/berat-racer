# Roads

How the game's roads are made from survey data, how to rebuild them, and how to check the result.
Code: `tools/roads/` (one module per stage), consumed by `tools/build_world.py`, rendered and driven by `ChunkMeshes.cs` / `RoadIndex.cs`.

## Rebuild loop

```sh
cd tools
uv run python -m roads fetch-osm      # once: OpenStreetMap ways of the area (ssh to the OSM database host, read-only)
uv run python -m roads build          # ~1.5 min for the small map: every stage, report, artefact in data/big/roads/
uv run python build_world.py --list data/big/small_sectors.json --out ../world_small --far-cache data/big/far_small_v2.npz
uv run python check_roads.py ../world_small      # terrain never above a road nor a bridge deck, every road end meets its junction
BERAT_WORLD=$PWD/../world_small ../Build/dev/BeratRacer.app/Contents/MacOS/*      # play it
```

Looking at a spot without starting the game:

```sh
uv run python -m roads inspect --at 149,332      # plan view + height profiles + the surveyed sections there (the game's K key copies coordinates)
uv run python -m roads gallery                   # every junction, 48 per sheet, the complicated ones first
uv run python -m roads report                    # the numbers of the last build again
../tools/shot.sh fork 149 332                    # three in-game screenshots of the spot (needs Build/dev/BeratRacer.app)
```

Tuning lives in two files:

- `tools/roads/config.py`: every number (width scale, smoothing, corner radii, grade and vertical-radius limits per road class).
- `tools/roads/overrides.toml`: per-section corrections keyed by the BD TOPO id that `inspect` prints (`skip`, `width_real`, `dirt`, `limit`, ...).

`--list` selects another area (default `data/big/small_sectors.json`); it goes before the command: `python -m roads --list L build`.

Both steps only redo what changed. The height solve keeps the result of every tile with a hash of what it was solved from (its samples,
the heights its neighbours fixed, the raster files under it, the tuning values and the solver code) in `data/big/roads/<area>.cache`;
the world builder leaves the same kind of key per sector in `<world>/keys` (road surface, raster, vector and POI files, builder code and
tuning). A tile or sector whose key is unchanged is skipped. After a local change (one override, one road) the tiles around it are solved
again and the sectors whose roads moved are rebuilt; after a change of tuning or code everything is. `--fresh` on either command ignores
the keys. `compare_roads.py A B` and `compare_worlds.py DIR_A DIR_B` compare two builds (heights, sector files; chunk contents).

## Data

| What | Source | Used for |
|---|---|---|
| Centrelines, connectivity | BD TOPO `troncon_de_route` | geometry (planimetric accuracy 2.5 m) |
| Carriageway width, lanes, one-way, class, urban flag, bridge / tunnel level | BD TOPO | width, markings, traffic, decks |
| Posted speed limit, surface / track grade, lit, missing names, lanes, width | OpenStreetMap (`planet_osm_line` of the massif-extractor database) | matched onto each BD TOPO section by position and direction |
| Ground and surface heights | IGN LiDAR HD (2 m) | height profile, bridge decks |

In the 10 x 10 km around Bérat BD TOPO is the richer source (surveyed widths on 72 % of the sections, lane counts, full connectivity);
OSM adds 138 posted limits, 10 surface corrections, 99 names. OSM widths exist on 7 ways only. Both stay in the pipeline: the merge is per attribute.

Drawn width = surveyed width x 1.27 (the 1.15 the game had, plus 10 %), at least 5.0 m for a paved two-way road (two cars must
pass), 3.6 m for a paved one-way road, 3.2 m for a track. Where BD TOPO has no surveyed width (28 % of the sections, mostly tracks
and new streets) the width is estimated, in this order: by nature (roundabout 6 m, track 3 m, gravel road 3.2 m), by lane count
(1 lane 3 m, 2 lanes 5 m, 2.8 m per lane beyond), by importance class (7.2 m for class 1 down to 4.4 m for class 6), else 5.2 m;
an OSM `width` tag replaces the estimate when there is one. Traffic always keeps to the right (France); the data only says
two-way / one-way and the direction of a one-way road.

## Stages

1. **source**: sections become `Edge`s with normalised attributes and a road class (main, collector, local, street, roundabout, track).
2. **graph**: nodes where ends meet; dead-end stubs under 8 m dropped. At each node the arms that continue each other are paired
   (deflection under 40 degrees, same class and road number preferred). Chains of pairs are *strokes* (through roads);
   the parts of a stroke between junctions are *links*.
3. **alignment** (horizontal): each stroke is one smooth curve: least squares against the surveyed line with a penalty on the third
   difference (the change of curvature), so it runs in straights, arcs and spiral-like transitions. Weights are raised until the curve
   stays within 1.2 - 2 m of the surveyed line (class dependent). Important strokes first; the nodes they pass move with them and pin
   the later ones. Roundabout rings become true circles.
4. **junction**: around a node the arms are sorted; each is trimmed back to where its kerbs are clear of its neighbours, plus the
   length of the corner curve (radius by class). The junction surface is the union of the arm stubs, a rounded wedge per corner and
   the area under a smooth curve where kerbs never cross. Links that would have nothing left between their two trims are swallowed:
   their junctions merge (tiny roundabouts, slip-road triangles; islands of 12 m2 or more stay as holes).
   Junctions do not depend on each other: each pass of the merge loop, and the outlines, run across `--jobs` forked workers.
   Roads end exactly on the junction outline and share those vertices: nothing overlaps, nothing flickers.
5. **crossing**: where two drawn links cross without a junction, one passes over the other. BD TOPO's bridge flag says which and
   is nearly always right; the LiDAR overrules it where the survey is wrong. The road passing over is the one whose ground climbs
   3 m above the crossing on both sides, steeply (within 15 m: a trench wall or an abutment, not a valley side), within 60 m of it,
   while the other road's ground stays down. Its span becomes a bridge, bank top to bank top; a surveyed bridge of the road below
   within 15 m of the crossing, lying flat on the floor, is dropped. Bridge and tunnel flags are therefore per link segment, and a
   surveyed section is cut into several pieces where a span starts or ends. On berat70 this changes the two crossings of the
   Canal du Midi over the ring road at Herbettes / Rangueil (towpath and avenue on the aqueduct side surveyed as on the ground,
   the ring road in its trench surveyed as the bridge) and nothing else.
6. **profile** (vertical): a quadratic programme (Clarabel, an interior-point solver: a tile converges in about 17 iterations to
   the optimum, where OSQP at a practical tolerance stopped up to decimetres short on tiles with fixed neighbours). Unknowns: the height of every sample outside junctions and one plane per
   junction. Objective: stay on the LiDAR ground (robust: samples far from the solution are down-weighted), minimise the third
   derivative (grade changes become parabolas). Constraints: maximum grade and minimum crest / sag radius per class.
   Every arm lies on its junction's plane up to its mouth; its cross slope there is the plane's and is unwound over 14 m.
   Bridges aim at their deck (surface model), not at the ground below.
   The area is solved in 3.2 km tiles, in four rounds like the colours of a 2 x 2 checkerboard (tiles of one round never touch and
   run in parallel). Each tile is solved with a 600 m halo of its neighbours: what a neighbour already solved is fixed and continued
   smoothly, what is not solved yet is solved along and thrown away. A road crossing a tile border is one continuous profile.
   Against one solve of the whole small map the tiled result differs by 2 - 8 mm on average (a few cm at the 99th percentile).
7. **surface**: pieces (centreline + both edges + drawn flags per section and bridge span), junction meshes, footprints and tangent planes for the terrain.

`build_world.py` then shapes the terrain around that surface (`roads/terrain.py`):

- *blend*: under the road and on a 1.5 m shoulder the ground sits 5 cm below the road, easing back to natural ground over 6 m;
- *bench*: every corner of a terrain cell touched by a road is lowered below the tangent planes of the road points that can share
  that cell. A triangle whose corners are all below the road's tangent planes is below the road, so this holds for the 4 m mesh
  and for the 16 m mesh drawn beyond 1.6 km alike. The game no longer cuts terrain at run time.

Buildings are cut out of the real surface polygons and plants keep clear of them (a 1 m raster of the surface).

## Chunk format (BM06)

After the terrain grids a chunk now carries the 16 m heights (26 x 26 floats), then per road piece: flags, class data, surveyed width,
mean half width, distance along its link, name, per point the centre and both edges (x, y, z), a drawn flag per segment and the
points where a give-way line is painted;
then the junction meshes (vertices, triangles, outline edges with a mouth flag), the water areas and stream lines, and (BM06) the
outlines of the water carried by a structure: a canal on an aqueduct over a road. The builder finds them (`carried_water` in
`build_world.py`: water standing 1.5 m or more above the ground, with a road on the ground passing under it, carried on until the
ground under the water is back at the depth of a bed, so the ends rest on the banks); the game draws their
concrete channel (walls where the water stops, a floor 1.8 m under the surface) and keeps the ground under them dry. A BM05 chunk
is a BM06 chunk without that list; `WorldData.cs` reads both, `LegacyChunk.cs` adapts older worlds (BM02 - BM04) so they still
load with their old look.

The physics surface is the drawn one: `RoadIndex` hashes the very triangles that are rendered.

## What the build report says (small map, 447 km of roads)

| | before | now |
|---|---|---|
| overlapping road surface | every junction | 13 m2 in total (two unconnected parallel tracks) |
| terrain above a road (3 million sample points, both meshes) | visible at forks and at distance | 0 |
| speed at which a car leaves the ground on the tightest crest | 29 - 51 km/h if draped on the raw ground | 124 km/h on tracks, 195 - 391 km/h on paved classes |
| road within 10 cm of the LiDAR ground | | 90 % of the length (99 %: within 0.2 - 1.1 m by class) |
| road benchmark (`-roadtest`, same player): ground roughness under a wheel, RMS | 35 m/s2 | 10 m/s2 |
| same: wheel-hop steps / phantom stops / respawns | 101 / 9 / 3 | 12 / 3 / 1 |

## Known limits

- Graph and horizontal smoothing still run over the whole build area in one process (minutes for 5,000 km2, all in memory) and are
  redone on every build; junctions run in parallel but are also redone; only the height solve is tiled and kept between builds. The region-sized `world/` is still in the old format and loads through `LegacyChunk`.
- Two roads closer than a terrain cell at different heights (a village terrace) cannot both sit on a 4 m terrain grid: the upper
  one gets a retaining wall along its edge.
- Cross-sections are flat except near junctions; no superelevation in curves yet (the data and the physics already carry a cross slope).
- No pavements; lighting (`lit`) is carried in the data but not used yet. Give-way lines are painted where a paved road meets a
  junction as the lower-ranked arm (equal ranks get none: priority to the right); stop signs are not in the data.
