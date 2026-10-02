# A world build that scales

Goal (docs/build-scaling-goal.md): berat70new (484 sectors, ~4,950 km²) rebuilt from cached sources in 10 minutes or less, output
and intermediates in 20 GB or less, sources in 1 MB/km² or less, and every stage able to grow to the whole of France with a bounded
RAM per worker. Machine: M1 Max, 10 cores (one is taken by a VM running all day: 9 usable), 32 GB.

Measuring: `tools/profile_build.py` runs a build command, samples the whole process tree (resident memory per process and in
total, CPU time), echoes every output line with its time and keeps the record in `data/big/profile/<name>.json`. The world builder
now reports per sector its CPU time, the peak resident memory of its worker and the wall time of each part (`parts`).

## Step 1: where the time, the memory and the disk go (master bd66c736)

Two areas: the small map (9 sectors, rural) and `b70s20`, a 5 x 4 block of berat70new (si 7..11, sj 3..6) chosen because its
density of buildings and roads (BD TOPO file sizes) is the mean of the 484 sectors. Builds from scratch (`--fresh`), 9 workers.

| | small: roads | small: world | b70s20: roads | b70s20: world |
|---|---|---|---|---|
| wall | 30.7 s | 209 s | 77 s | 653 s |
| CPU (whole tree) | 54 s | 830 s | 155 s | 3,160 s |
| CPU per sector | 6.0 s | 92 s | 7.8 s | 158 s (41 - 590) |
| peak resident, one process | 584 MB (main) | 1,758 MB (a worker) | 1,000 MB (main) | 2,688 MB (a worker) |
| disk written | 87 MB sector files + 67 MB network + 22 MB cache | 90 MB | 246 + 187 + 57 MB | 242 MB |

World build, b70s20, mean wall time per sector and part (9 sectors in parallel):

| part | s / sector | share |
|---|---|---|
| stitch_fill (the ground around the paved surfaces, `stitch.fill`) | 154.4 | 83.6 % |
| ground_areas (land use polygons, car parks) | 8.8 | 4.8 % |
| buildings (footprints cut from the roads, roofs) | 8.0 | 4.3 % |
| field (the paved-edge height field, cut cells) | 6.1 | 3.3 % |
| write, ground, far_mesh, facades, terrain, colour, trees, ... | 7.3 | 4.0 % |

Inside `stitch.fill` (cProfile, one sector): 23 s of 50 in `PavedHeight.__call__` (a Python loop per point over the triangles
near it), 9 s in `dwithin` against the window-wide paved outline (not prepared), 5 s of box differences against it, ~8 s
iterating triangles one by one in Python.

Road build, b70s20, fresh: source 3.3 s, graph 0.4, alignment 6.9, junction 5.6, crossing 0.7, profile 20.4 (the only tiled
stage), lanes 26.4, lanegraph 8.6, report + save ~5 s. Everything but the profile runs in one process over the whole area.

### Per-km² model (baseline)

| | per km² | berat70new (4,956 km²) | France (550,000 km²) |
|---|---|---|---|
| world build CPU | 15.4 s | 21 h CPU, 2 h 20 min on 9 cores | 2,350 h CPU, ~11 days on 9 cores |
| road build CPU | 0.76 s (but ~70 % in one process) | 1 h CPU, ~30 min wall (measured: 89 min for berat70 before build-speed) | not feasible in one process |
| world output | 1.18 MB | 5.9 GB | 650 GB |
| road intermediates (sector pickles, network pickle, caches) | 2.4 MB | 11.9 GB (on disk today: 7.7 GB for berat70new) | 1.3 TB |
| sources (LiDAR mnt / mnh, ortho, BD TOPO, OSM) | 0.51 MB | 2.5 GB | 280 GB |
| RAM | worker 1.8 - 2.7 GB; road main process grows with the area (2.5 GB network pickle for berat70new) | | |

CPU per sector follows the road surface: a least-squares fit gives `cpu ≈ 0.50 s x road pieces - 79 s` on b70s20 (the fill
works along every paved edge); buildings hardly count.

Sources per km² (whole region on disk, 4,837 sectors): LiDAR mnt 0.253 MB (uint16 5 cm, npz), mnh 0.149 MB (uint16 0.1 m, npz),
ortho 0.011 MB (JPEG 4 m), BD TOPO 0.085 MB (gzip GeoJSON), ground layers ~0.03 MB, OSM ~0.004 MB. Total 0.51 MB/km²: already
under the 1 MB/km² target; what is missing is the layout (step 2).

First-time fetch of the b70s20 sources not on disk (42 sectors of window): OSM roads + junction control (mace, Overpass, WFS) 4.3 s;
OSM roof tags (Overpass) 19 s; ground layers (5 WFS layers x 42 sectors, Overpass land use, 3,539 orthophoto row measurements) 5 min 16 s.

### Global (non-tiled) steps

Road pipeline (`tools/roads`), all in one process over the whole area unless said:
- `source.load_edges` reads every BD TOPO road file of the area; `osm.load` / `controls.load` read one OSM file per area
  (`osm/roads_<tag>.json.gz`, `controls_<tag>.json.gz`) and match against an STRtree of all ways.
- `graph.build`, `strokes`, `links`: the whole network; `alignment` smooths every stroke in one process, its cache
  (`<tag>.cache/alignment.pkl`, 651 MB for berat70new) is one pickle.
- junctions (forked workers but the whole network in memory), crossing, lanes, lanegraph, the report: whole network.
- profile: tiled (3.2 km tiles, checkerboard rounds), but the network is in memory and the solve runs inside the same process tree.
- `save`: the whole `Network` pickle (2.5 GB for berat70new) and per-sector pickles cut from the whole lists.

World build (`tools/build_world.py`):
- per process, whole-area files: `osm_ground(tag)` (OSM land use of the area), `row_directions(tag)`, `roofs.osm_buildings(tag)`
  (and a linear scan of it per building), `load_pois()` (every POI file of the region), `places.json` (copied whole).
- the far terrain: `far.bin` and its cache (`far_<tag>.npz`) are whole-map arrays, rewritten on every run.
- `world.json`, `spawn.json`: small, written whole.

Fetchers: one file per area for OSM roads, junction control, OSM land use, OSM roof tags, orthophoto row measurements; POIs per
departement; rasters and BD TOPO per tile already.

## Step 2: source tiles, fetched on demand (`tools/sources.py`)

One layout for every source, one file per tile and kind under `data/big/src/<kind>/`: the LiDAR rasters per 1.6 km tile, the
orthophoto per 3.2 km tile, and everything else per sector tile (3.2 km): BD TOPO layers (roads, buildings, water, vegetation,
transport, structures, `non_communication`), RPG, BD Haie, OSM from the massif-extractor database on mace (drivable ways, control
nodes) and from Overpass (land use, car parks, roof tags, turn restrictions, points of interest, place names: one query per tile,
the five answers separated by `out count`), and the orthophoto row measurements of the rowed parcels. A line or area is kept in
every tile its box meets (readers de-duplicate on its id), a point in the tile holding it.

A build asks for the tiles of its sectors and their neighbours (`sources.ensure`, called by `python -m roads build` and
`build_world.py`); whatever is missing is fetched for those tiles only, written atomically (re-running resumes), each service with
its own pool (IGN WMS 4, WFS 3, mace 1 ssh session per 40 tiles, Overpass 1 query at a time 2 s apart, orthophoto crops 6). The
fetch report (requests, MB and seconds per service) goes to `data/big/profile/fetch_<tag>.json`. `uv run python sources.py status
<list>` says what a list has and lacks.

Rasters are stored lossless and leaner: the uint16 codes (5 cm ground, 0.1 m canopy) as the difference along each row, zstd level
19 (decoded in 2.5 ms a tile; xz was 31 ms); the float32 tiles of the original 10 x 10 block with their bytes shuffled, zstd.
The keys of the incremental builds now hash the content of every tile a sector or a height tile read (`sources.stamp`), not
file names and times.

Conversion of today's caches (`convert_sources.py`, once, nothing downloaded): rasters re-encoded and checked bit for bit before
the old file was removed; BD TOPO files moved (the Haute-Garonne ones gzipped); the per-area OSM files split into the tiles they
cover wholly (a tile only partly covered is left to be fetched); POIs and place names split by tile.

| source | before | after |
|---|---|---|
| LiDAR ground `mnt` (region, 19,348 tiles) | 12.3 GB npz + 1.0 GB float32 .npy (original block) | 5.97 GB |
| LiDAR height `mnh` | 7.2 GB npz + 1.0 GB .npy | 6.39 GB |
| orthophoto | 0.53 GB JPEG + 0.19 GB .npy | 0.61 GB (JPEG as served, PNG for the original block) |
| BD TOPO roads, buildings, water | 4.2 GB (the Haute-Garonne GeoJSON uncompressed) | 1.09 GB |
| ground layers, OSM | (per-area files) | 0.07 GB |
| **total** | **~26 GB, 0.53 MB/km²** | **14.1 GB, 0.29 MB/km²** (4,837 sectors, 49,500 km²) |

100 tiles of the original block had been downloaded a second time by the region fetch; the old reader always took the original
float32 file there, and so does the new layout (the second copies were dropped).

Verified: the small map built from the tiles (roads with `python -m roads build`, then `build_world.py`) gives road sector files
and all 1,152 chunk files and `far.bin` byte for byte identical to master; `places.json` now holds the place names of the map's
tiles and their neighbours instead of the whole region.

## Step 3: the road pipeline per tile (`tools/roads/tiled.py`)

No stage holds the whole network any more. The tiles are the sectors; four passes run over them, each tile reading the source
tiles and what earlier passes wrote for the tiles around it, and writing only what it owns
(`data/big/roads/<tag>/<pass>/<si>_<sj>.pkl`):

1. **align**: four rounds, like the colours of a 2 x 2 checkerboard (neighbours are never in the same round). A tile reads the
   sections within 1.6 km, cuts them where they cross a tile border (the two ends of a cut are one node of their own), and smooths
   its strokes. What a tile of an earlier round smoothed is fixed; a stroke continuing it is solved in the runs between fixed
   sections, pinned to the last 150 m of the fixed curve on each side, so it continues smoothly. A tile owns the section parts lying
   in it and the nodes that no tile of an earlier round touches.
2. **network**: the sections put back together; graph, links, junctions (only those within 400 m of the tile or of the ends of its
   links are built), crossings and tunnels. A link belongs to the tile holding its middle, a junction to the tile holding its
   centre; a tile loads its 3 x 3 neighbourhood and more when a link it needs is not whole there. Records refer to each other by
   key and owner tile.
3. **profile**: the existing checkerboard height solve, now one process per tile, the neighbourhood assembled from the records.
4. **surface**: lanes, the lane graph, road pieces and junction meshes of what the tile owns. Lane graph elements are numbered
   (tile code << 15) + rank, references to another tile's elements are resolved in a last short pass by what they belong to.

The world builder reads a sector's roads from the 3 x 3 tiles around it (`build.load_sector`). The whole-network pickle, the
alignment cache of the whole area and `inspect` / `gallery` / `compare_roads` on the whole network are gone (they work on the
tiles around a spot now).

**The smoothing order.** A tile cannot know the whole length of a stroke, and smoothing results depended on the order strokes
are smoothed in and on the end they are walked from (the solve is not direction-symmetric, and the stroke list order came from the
order of the BD TOPO files). The rule is now window-independent: by class, then length up to 1,600 m (the alignment halo), then the
smallest section id; each stroke walked along that section's digitised direction. Against master that alone moves 35 % of the
sections by more than 1 cm (p99 1.2 m, within the 1.2 - 2 m bound of the survey); it changes which road places a shared node, not
the quality (worst bound ratio 1.02 in both). With the same rule, the tiled build reproduces a whole-area build:

| small map, tiled vs whole area (same rules) | |
|---|---|
| road pieces, sections, junctions | 2,794 / 2,794, same sections, 1,315 / 1,315 junctions |
| plan, Hausdorff per piece | p90 0.003 m, p99 0.63 m, 3.4 % over 0.1 m (almost all within 200 m of a tile border) |
| heights, max per piece | p90 0.000 m, p99 0.017 m, max 0.12 m |
| junction planes | height p99 3 mm |

Small map, tiled build: `check_roads` OK (no terrain above a road or deck, every road end meets its junction, every lane leads
on: 12,224 elements, 2 without exit as on master); 2 invalid junctions (master 1; the whole-area build with the new rules has the
same 2); `check_gaps` 7 cm-level gaps (master 6, the same problem area; a 0.5 mm sliver the ground fill dropped was fixed).

Cost, b70s20 (42 tiles with the ring), from scratch, 5 workers: 65 s wall, 274 s CPU (6.5 s a tile), largest process 916 MB,
whole tree 3.9 GB (the whole-area build: 77 s wall with 9 workers, 155 s CPU, main process 1.0 GB growing with the area).

## Step 5: far terrain, places, world files

The far terrain is a patch per sector (`<world>/far/<si>_<sj>.npz`, the sector's 64 m heights and colours), written by the sector's
own build, so an unchanged sector keeps its patch: the whole-map `far_<tag>.npz` cache and `--far-cache` / `--skip-existing` are
gone (the content keys decide what to rebuild). `far.bin`, which the game reads as one file, is written from the patches one row of
sectors at a time (two passes: heights, then colours); a vertex no sector covers takes the nearest covered height of its band.
Small map: `far.bin` byte for byte the same as before; a second run rebuilds 0 of 9 sectors. `places.json` comes from the place
tiles of the map (step 2); `world.json` and `spawn.json` are a few hundred bytes.

## Step 6: disk

**Chunks BM08.** Three quarters of a chunk's bytes were the ground around the paved surfaces (BM07: every triangle as nine
float32 and three weights). BM08 is BM07 with that list indexed and laid out to compress: the shared vertices as four columns (x,
height, north, weight), each written as its four byte planes, then the corners as differences from the previous index (int16 when
they fit). The vertices are shared as written (float32), so the game gets exactly BM07's triangles: `WorldData.cs` expands them into
the same arrays (BM05 - BM07 still load). Small map: 92 -> 76 MB, `check_roads` / `check_gaps` unchanged, the roadtest gives the
same figures on the BM07 and the BM08 build of the same roads. **Online play: hosts and players need the new build and the new map
together** (an older player cannot read BM08).

**Road intermediates** are zstd-compressed pickles per tile and pass (`align`, `network`, `profile`, `lanes`; the `surface` files
are removed once numbered): b70s20 474 -> 290 MB (6.9 MB a tile). The whole-network pickle (2.5 GB for berat70new) and the
per-sector copies (3.9 GB) are gone.

| per km² | before | after |
|---|---|---|
| world output | 1.18 MB | 0.97 MB (b70s20) |
| road intermediates | 2.4 MB | 0.68 MB (b70s20 incl. its ring of tiles) |
| world keys, far patches | - | ~0.01 MB |

## Sources from a Geofabrik extract

The public Overpass servers answered the heavy per-tile query (land use, roof tags, POIs) in 80 - 110 s, or not at all (504):
~0.7 tiles a minute for berat70new's 402 missing tiles, about 10 hours. `osm_extract.py` reads a Geofabrik extract instead (the
Midi-Pyrénées `.osm.pbf`, 362 MB, 50 s to download) with pyosmium in two passes and writes the same tiles through the same
converters: 402 tiles in 3 min 13 s. Against tiles fetched from Overpass: roof tags, restrictions, POIs and places identical; land
use the same up to the dates of the two snapshots, plus the multipolygons that cover a tile without a member node inside it (Overpass
misses those). `sources.ensure` uses any extract in `data/big/src/extract/` first and Overpass only for tiles outside it. (This is
one regional file, read once and split into tiles; the builds still only read tiles.)

## Step 7: berat70new from scratch

`berat70scale` = the 484 sectors of berat70new, roads and world built from scratch from the cached sources, **at most half the
machine** (5 worker processes of 10 cores; whole process tree within 16 GB of 32), on a quiet machine. World written to
`../world_berat70scale`, next to `world_berat70new`.

| | before (step 1 model) | now |
|---|---|---|
| roads | whole-area process, ~30 min+ (89 min measured for berat70 before build-speed) | **22.8 min** wall, 6,660 CPU-s (576 tiles) |
| world | ~2 h 20 min on 9 cores (15.4 CPU-s/km²) | **33.9 min** wall, 9,974 CPU-s (20.6 CPU-s a sector, 2.0 / km²) |
| total | ~3 h | **56.7 min** (target 10 min: **not met**) |
| CPU per km² | 16.2 s | 3.36 s |
| largest process | road main process growing with the area (network pickle 2.5 GB) | roads 2.1 GB (any tile; dense Toulouse tile 1.8 GB alone), world 5.3 GB (the densest sector, Toulouse centre, 22,700 buildings; a rural one 1.9 GB) |
| whole process tree | | roads 9.5 GB, world 14.5 GB (the world builder holds new sectors back above 16 GB - 5.5 GB) |
| world output | 1.18 MB/km² | **4.42 GB, 0.89 MB/km²** (BM08) |
| road intermediates | 2.4 MB/km² (7.7 GB on disk for berat70new) | 6.6 GB, 1.33 MB/km² |
| output + intermediates | ~14 GB | **11.0 GB** (target 20 GB: met) |
| sources | 0.53 MB/km² | **0.29 MB/km²** (target 1 MB/km²: met) |

RAM per worker does not grow with the map: a sector needs what its own content needs. The same sector measured alone, built as
part of areas of different sizes:

| sector | 9-sector build | 20 | 100 | 484 |
|---|---|---|---|---|
| (5, 5), small map | 1,796 MB | | 1,800 MB | 1,800 MB |
| (9, 4) | | 2,024 MB | 2,013 MB | 2,020 MB |

The largest worker of a build is therefore its densest sector's: 2.5 GB on the small map, 4.7 GB on the 100-sector block (near
Toulouse), 5.3 GB on berat70new (Toulouse's centre, 22,700 buildings; a rural sector 1.9 GB). Road workers: at most 2.3 GB (a
dense tile alone 1.8 GB). Road worker pools are renewed every 4 tiles, world workers every 40 sectors, so nothing piles up, and the
world builder starts no new sector while the process tree is within 5.5 GB of the 16 GB budget (berat70new: tree peak 13.6 - 14.5 GB).

Incremental: an unchanged rebuild reuses every tile of every pass (content hashes of the inputs and of each pass's code) and rebuilds
no world sector (small map: 1.6 s); one hand correction recomputes the tiles whose windows see it (9 of 25 alignment tiles on the
small map) and the 3 x 3 world sectors around it. On berat70new, a change of the surface pass (junction mouths) rebuilt 36 world
sectors of 484 in 619 s.

First-time fetch of berat70new (not part of the 10 min; rasters and BD TOPO roads / buildings / water were already on disk from the
region download): the 5 ground layers and BD TOPO `non_communication` (WFS), OSM ways and control nodes (mace) for 576 tiles: 3,000
jobs in 227 s; the Overpass kinds from the extract: 50 s download + 193 s; the orthophoto row measurements: 25,910 WMS crops,
203 MB, about 50 min at 3 tiles at a time (the IGN service answers 429 when pushed harder; requests back off and retry).

Quality on berat70new (whole area, against the old whole-area berat70new report of 30 Sep):

| | old | tiled |
|---|---|---|
| links / junctions | 194,192 / 93,235 | 194,186 / 93,244 |
| overlap pairs / m² | 432 / 11,721 | 434 / 12,236 |
| folded ribbons, invalid junctions | 116, 125 | 136, 126 |
| class figures (grade, crest, cut / fill) | | within a few % |

`check_roads` on the full world: no terrain above a 4 m road or a deck, no lane without its successor, no lane turned back at a
junction; 62 points where the 16 m distant terrain stands above a road (worst 1.69 m, at a motorway interchange in Toulouse where
two junctions with 250 m slip-road arms overlap by 674 m²), 25 lane joins inside a link that jump one lane width where the lane count
changes (the lane graph's own handling of lane drops, all within one tile). Road ends not exactly on their junction's vertices were
51 (42 of them within 1 cm: a junction outlined by its owner from its copy of a link another tile owns); the surface pass now puts
every mouth corner on the arm link's own edge point: **0**.

Playtest on the full world (`Build/scaling`, the BM08 player): road benchmark 1.4 km, harshness 0.10 m/s², 0 wheel hops, 0
respawns; autotest 2,236 m, 25 cells, 0 stuck, 0 exceptions; ready in 3.2 s, 476 MB managed.

Shots outside the small map (old `world_berat70new` left, new right): [Herbettes ring-road tunnel](shots/scaling/b70_herbettes.jpg),
[Toulouse centre](shots/scaling/b70_toulouse_centre.jpg), [west, rural](shots/scaling/b70_west_rural.jpg), [a tile corner](shots/scaling/b70_tile_corner.jpg)
(the old world predates the data-inventory work: no tunnels, roofs, facades, ground classes). Small map against master:
[road seam](shots/scaling/small_road_seam.jpg), [roundabout](shots/scaling/small_roundabout.jpg), [village car park](shots/scaling/small_parking_village.jpg),
[village](shots/scaling/small_facades_village.jpg), [main road](shots/scaling/small_main_road.jpg), [banked bend](shots/scaling/small_banked_bend.jpg),
[pond bank](shots/scaling/small_pond_bank.jpg), [vineyard](shots/scaling/small_vineyard.jpg): the same scenes; roads within a metre
where the smoothing order changed (a footprint cut by a road moves with it, and a front wall with it).

## Step 8: 90 % of the machine, a second speed pass

The user then allowed 90 % of the machine (`BERAT_MACHINE_SHARE=0.9`: 9 workers, 28.8 GB; the default stays half). The same build
at 9 workers took **43.5 min** (roads 1,191 s, world 1,420 s): the world stage scaled, the road passes hardly did (align 161 s
against 168 at 5 workers). `run_batched` started a fresh pool for every jobs x 4 tiles and waited for the slowest tile of each batch:
3 of the 9 workers stood idle most of the time. Profiles of a Toulouse sector / tile (cProfile) then gave the second pass:

| change | where | effect | output |
|---|---|---|---|
| one pool, workers renewed every 4 tiles, at most jobs + 1 queued, heaviest tiles first (by their surveyed roads' size); world sectors with most buildings first | `roads/tiled.py`, `build_world.py` | no idle worker until a round's end | the same (tiles of a round are independent) |
| straight skeleton: the next event worked out for every vertex / edge pair at once (numpy) instead of a Python double loop | `roofs.py` | 2.2x on all footprints of sector (10, 11); buildings 101 -> 43 s there | identical (175,244 roofs compared; chunks byte-identical) |
| junction mouths: `make_valid` once per piece, the mouth's length inside the others as the union of its pieces in each (no union of the polygons) | `roads/junction.py` | network tile 35 -> 22 s | identical tile; 124 invalid junctions of 93,244 instead of 126 over the map |
| sideways clearances: every scan offset sampled in one call | `roads/lanes.py` | surface tile 42 -> 34 s | identical |
| fill: a 2 m box less the paved surface of its 32 m block, not the chunk's; fill points made unique as complex numbers | `stitch.py` | fill 44 -> 24 s on sector (10, 11) | 10 - 50 bytes per changed chunk (last-bit floats) |
| car-park aisles: one bulk query instead of a union of every other car park per car park | `ground.py` | ground areas 20 -> 6 s on sector (10, 11) | the same aisles (buffer of a union = union of buffers) |
| `check_gaps`: every n-th whole column (a chunk's neighbours stay cached), 16 chunks cached, progress; triangles by STRtree (below) | `check_gaps.py` | 8 GB and no result after an hour -> 15 min, 1 GB | the same check |

berat70new from scratch, 9 workers:

| | before (9 workers) | after |
|---|---|---|
| roads | 1,191 s (7,782 CPU-s): align 161, network 256, profile 422, surface 314 | **846 s** (7,091 CPU-s, x8.4): align 113, network 120, profile 332, surface 246 |
| world | 1,420 s (11,238 CPU-s) | **1,120 s** (9,339 CPU-s, x8.3): fill 3,395 -> 2,135 CPU-s, buildings 2,890 -> 2,289, ground areas 1,399 -> 1,117 |
| total | 43.5 min | **32.8 min** |
| peak memory | 18.5 GB tree, 4.6 GB a worker | 19.0 GB tree, 5.0 GB a worker (Toulouse's centre); within 28.8 |

CPU-s per stage are higher at 9 workers than at 5 (world 11,238 against 9,974 for the same code): the M1 Max has 8 performance and
2 efficiency cores, and the ninth worker shares memory bandwidth; 9 workers give ~7.5 workers' worth.

Checks on the new full world: `check_roads` the same as step 7 (above4 0, above16 62, loose 0, 923,715 lane elements, 0 turned back);
Playtest (`Build/scaling`): roadtest 1.4 km, harshness 0.10 m/s², 0 wheel hops, 0 respawns; autotest 2,296 m, 25 cells, 0 stuck,
0 exceptions. Small map rebuilt with the same code: `check_roads` OK (12,096 elements, 1 without exit), `check_gaps` 7 cm-level gaps
(as before), `check_buildings` 10,595 buildings, nothing outside.

**The full-world gap check**, which step 7 could not finish, now runs: `check_gaps.Surface` looked triangles up in a KD-tree with the
radius of the largest triangle around, so one long road or seam triangle made every query return thousands (19.5 GB, 77 min for
every 8th column). An STRtree of the triangles' boxes tests the same ones: 15 min, 1 GB; the small map 8:52 -> 1:55, the same result.
On every 8th column of berat70new (3,872 chunks) it found 981 see-through gaps (199 holes, 351 cracks, 431 steps). The same sectors
built with the step-7 code give the same gaps, so they predate this work, but they are what the map has:

- **Car parks (fixed).** `parking_mesh` kept a Delaunay triangle when its centroid and edge midpoints were inside the outline; an edge
  passing a notch of the outline (0.6 m deep, near a corner) stuck out of it, the ground fill followed the outline, and the car park
  dipped up to 0.67 m below the ground's edge. A triangle is now tested whole against the outline (+5 cm). Small map 7 -> 4 gaps;
  berat70new 981 -> **684** (steps 431 -> 242, cracks 351 -> 237), over 10 cm 175 -> 116. Playtests unchanged.
- **Junctions over a bridge's end (not fixed).** The largest remaining steps (up to 6 m, ~100 spots over 10 cm) are slip-road forks
  whose junction arms (up to 60 m) reach over a bridge span: the junction mesh is ground in the world builder (its kerbs pull the
  ground field, its area is cut out of the terrain) while part of it stands on the deck, so the ground rises to the deck there or a
  wall opens under it. Treating junctions met only by bridge pieces as decks did not catch these (their arms continue on embankments)
  and made a test block slightly worse; it needs the road pipeline to cut such junctions at the span (or mark their deck part).
- **Holes** (205) are mostly millimetre artefacts (the check probing 2 cm across a 2 mm junction edge) and, at sector borders, strips
  of about a metre where one sector's fill leaves room for a junction drawn by the neighbouring sector's chunk.

What remains is flat: the profile pass is three Clarabel solves (the robust re-weighting) of ~300,000 unknowns a dense tile with its
600 m halo (no solver setting gains more than 10 % without moving heights by centimetres); a rural world sector (10 CPU-s) has no
part above 3 s; a Toulouse sector is per-building Python (raster samples, footprint tests, facades) more than any one call.

## Per-km² model and France

Measured on berat70new (4,956 km²), the build costs, per km² (step 8 code; CPU at 9 workers, which counts ~15 % more than at 5):

| | per km² | berat70new | France (550,000 km², x111) |
|---|---|---|---|
| CPU, roads | 1.43 s | 7,091 s | 219 h |
| CPU, world | 1.88 s | 9,339 s | 288 h |
| wall on 9 workers (90 % of this machine) | 0.40 s | 32.8 min | **2.5 days** |
| wall on 5 workers (half), step 7 code | 0.69 s | 57 min | 4.3 days |
| world output (BM08) | 0.89 MB | 4.4 GB | **490 GB** |
| road intermediates | 1.33 MB | 6.6 GB | 730 GB (only needed for incremental rebuilds; a tile's files can go once its world sectors are written) |
| sources | 0.29 MB | 1.4 GB | **160 GB** (+ the extracts: France's `.osm.pbf` ~4.5 GB) |
| RAM | | worker <= 2.8 GB (roads) / 5.0 GB (world, densest sector); whole build <= 19 GB at 9 workers, 16 GB at 5 | the same: a worker's need is its tile's content, the build's is its number of workers |

Nothing in the build holds the whole map any more: sources, road passes, world sectors and the far terrain are per tile; what grows
with the map is the number of files, `far.bin` (one file the game reads: 7 bytes a 64 m vertex, 0.9 GB for France; the game would
need it tiled too), the build report and the list of tiles. The world's density varies 10x between a rural sector (14 CPU-s) and
Toulouse's centre (168 CPU-s), so a map's cost follows its towns more than its area.

## Against the targets

| target | result |
|---|---|
| berat70new rebuilt in 10 min or less | **not met: 32.8 min** at 90 % of the machine (9 workers; 57 min at half). 10 min at 9 workers (x8.4 in use) means ~5,000 CPU-s, about 10 CPU-s a sector for roads and world together; the build now needs 34 (roads 14.6 a sector, 12.3 a tile; world 19.3 a sector on average, ~150 for Toulouse's centre) |
| output + intermediates <= 20 GB, output ~1 MB/km² | met: 11.0 GB; 0.89 MB/km² |
| sources <= 1 MB/km², per tile, leanest lossless | met: 0.29 MB/km² |
| RAM per worker bounded, the same on 9, 100, 484 sectors | met: the same sector needs the same memory in a 9-, 20-, 100- or 484-sector build (1.8 / 2.0 GB); a worker's peak is its densest tile's (roads <= 2.3 GB, world <= 5.3 GB), the whole build within 16 GB |
| small map: no regression | `check_roads` OK (12,096 lane elements, 1 without exit, as master); `check_gaps` 7 cm-level gaps (master 6, the same problem area); roadtest better (harshness 0.10 vs 0.28 m/s², 0 wheel hops vs 14, 0 respawns vs 1); autotest 2,166 m, one stop at a village junction corner where a building stands 0.35 m from the junction (master has the same building and junction; its random route did not pass there); world 71 MB vs 92; shots: the same scenes |
| chunks only, no global state, incremental by content | met for every stage (far terrain as patches, `far.bin` streamed) |

## What is left

- **Speed.** The remaining 3.3x (at 9 workers) is spread over many stages: the height QP (three Clarabel solves a tile, ~3,000
  CPU-s over the map, the largest single item; a smaller halo or fewer robust rounds would change the profiles), the ground fill
  (~2,100 CPU-s: small GEOS differences and triangulations), buildings (~2,300: per-building Python around the roofs), the
  paved-edge field and car parks (~2,300), the surface pass (sight distances). Each of these needs compiled code (numba / Rust for
  the skeleton, the fill and the sight lines) or a different algorithm rather than more tuning; roofs could also be cached by
  footprint content across builds.
- `far.bin` is still one file the game reads; for France it should be tiled like the chunks (a game change).
- The road intermediates (6.6 GB) are kept for incremental rebuilds; a "no-incremental" mode could drop each tile once used.
- Smoothing-order differences against master (step 3): roads move up to ~2 m where shared nodes are placed by another stroke; 2
  invalid junctions on the small map instead of 1, the same 2 in a whole-area build with the new order.
- berat70new gaps (`check_gaps`, every 8th column: 684): junctions reaching over a bridge's end (steps up to 6 m, see step 8),
  metre-wide holes at sector borders next to junctions drawn by the neighbour sector, cm-level cracks.
- berat70new: 62 points where the 16 m distant terrain stands above a road and a 674 m² overlap of two junctions, both at one motorway
  interchange in Toulouse (junctions with 250 m slip-road arms); 24 lane joins jumping a lane width where the lane count changes;
  459 lanes without exit at the map's border or one-way ends. Not caused by the tiling (all inside one tile), not fixed.
- The Geofabrik extract is a regional file; for other regions the matching extract has to be put in `data/big/src/extract/`
  (Overpass remains the fallback).
- A small change rebuilds the 3 x 3 world sectors around it because lane-graph numbers in the changed tile shift; numbering
  elements by what they belong to (not by rank) would keep the others identical.
