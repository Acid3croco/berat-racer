# Roadmap: map package, then Unreal

The map is what is ours; the rest comes from Unreal Engine 5 on Windows (see `docs/map-package.md`). Two phases:

- **Phase A, on the Mac, in this repo:** the pipeline writes the map package. The data is already here (`tools/data/big/src`,
  49,500 km² of sources; the road stages of `berat70scale` in `tools/data/big/roads/berat70scale`).
- **Phase B, on the Windows PC** (RTX 5090, 96 GB, 9800X3D): Unreal imports the package and becomes the game.

The Unity player stays as it is (v0.6.0) until Unreal drives better; no new work goes into it.

## Phase A: the map package (Mac)

Every step runs first on the **small map** (`tools/data/big/small_sectors.json`, 9 sectors, minutes per build), then on
`berat70scale` (484 sectors). Code goes in `tools/package/`, run with `uv run python -m package <sectors.json> --tag <tag>`.

| # | Step | Output | Done when |
|---|---|---|---|
| A0 | Rebuild the small map's road stages (deleted in the cleanup): `uv run python -m roads build` (the small map is the default list) | `tools/data/big/roads/small/` | the stages exist for the 9 sectors |
| A1 | Package skeleton: grid, sector folders, `manifest.json`, writers (PNG 16-bit, glTF via `uv add`, GeoJSON) | empty package with a valid manifest | a reader lists the sectors and the origin |
| A2 | **Carved terrain**: the 2 m ground, roads burnt in (surface − 0.05 m, blended shoulders), water beds, holes. Move the carving out of `build_world.process_sector` into a function both can call | `height.png`, `holes.png` | road mesh never under or floating over the terrain (max gap measured per sector, < 5 cm) |
| A3 | Land cover: `ground.rasterize` at 2 m, row directions, bare-ground colour | `classes.png`, `rows.png`, `colour.jpg` | class preview image per sector looks right against the orthophoto |
| A4 | Roads: pieces and junctions to glTF with road UVs; markings mesh; centrelines with attributes | `roads.glb`, `roads.geojson` | opens in Blender on top of the terrain; attributes readable in QGIS |
| A5 | Buildings: today's walls and roofs to glTF; attributes to GeoJSON | `buildings.glb`, `buildings.geojson` | opens in Blender; heights match the LiDAR |
| A6 | Vegetation, water, lane graph, places | `vegetation.json.gz`, `water.geojson`, `lanes.json.gz`, `places.json` | counts match today's BN02 chunks |
| A7 | `check` command: decode every file, compare to the sources (heights vs LiDAR, roads on terrain, ids resolve across sectors), write one preview PNG per sector | report + previews | clean report on the small map |
| A8 | Full `berat70scale` package, timed and measured | `package/berat70scale/` | size and build time in this file |
| A9 | Transfer to the PC over the LAN (below) | package on the PC | checksums match |

A2 is the one real piece of new work: today the carved terrain exists only in memory and inside the Unity chunks. The rest is
writing out what the pipeline already computes.

### Transfer to the PC

Same local network: enable the OpenSSH server on Windows (Settings, Optional features), then from the Mac
`rsync -av --progress package/berat70scale/ user@<pc-ip>:/C:/berat/package/berat70scale/`. Gigabit Ethernet moves about
100 MB/s (10 GB in under 2 minutes); Wi-Fi is several times slower. A Windows SMB share mounted in Finder works too.
Re-runs only send what changed.

## Phase B: Unreal (Windows)

Milestone 1 is **driving a stock car on the small map in Unreal**; milestone 2 is the same on `berat70scale`.

| # | Step | Notes |
|---|---|---|
| B1 | Install: Epic Games Launcher, Unreal Engine 5.7, Visual Studio 2022 (C++ game workload), Git + Git LFS | new C++ project, *Open World* template (World Partition on) |
| B2 | Importer: an editor Python script reads the package (manifest, sectors), converts coordinates, creates the landscape from the tiled heightmaps, the layer weightmaps from `classes.png`, the holes as landscape visibility | decide here: one World Partition landscape (2 m, 70 km is ~35k samples a side) vs several landscapes vs Nanite terrain meshes, by measuring import and frame time |
| B3 | Terrain material: one layer per class with Fab / Megascans surfaces, auto-blend by slope, `colour.jpg` as a far tint, `rows.png` for furrows | the look of the countryside comes from here |
| B4 | Roads: import `roads.glb` as Nanite static meshes, asphalt / gravel / marking materials, collision on | wear, puddles, decals later |
| B5 | Buildings: import `buildings.glb`; later replace by PCG facades and roofs from `buildings.geojson` (use, era, walls) | buildings are the weakest part of the look; PCG is where they improve |
| B6 | Vegetation: PCG graph spawning tree and shrub assets from `vegetation.json.gz` (kind, height), hedges along their lines, vine rows | Nanite foliage (5.7) |
| B7 | Water: Water plugin bodies or simple planes at the package levels | |
| B8 | Sky and light: Sky Atmosphere, Volumetric Clouds, Lumen, time of day | |
| B9 | Driving: Chaos Vehicles stock car, chase camera, gamepad | **milestone 1** on the small map |
| B10 | Scale: `berat70scale` with World Partition streaming and HLOD, profiling | **milestone 2** |
| B11 | Later: custom tyre model (port of the Sim model's forces), traffic on the lane graph, wheel input and force feedback, online play (Unreal replication), UI, Windows packaging | one at a time, after milestone 2 |

## Open questions

- Terrain resolution: 2 m is the LiDAR raster we hold; 1 m would need the point cloud (only one tile on disk).
- B2's landscape choice decides how far one Unreal world can reach (70 km today, a département next, France later).
- ODbL: what an OSM-derived package means for a commercial release (see the licences in `docs/map-package.md`).
