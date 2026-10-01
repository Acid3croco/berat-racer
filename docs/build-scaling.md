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
