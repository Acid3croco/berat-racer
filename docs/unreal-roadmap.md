# Roadmap: map package, then Unreal

The map is what is ours; the rest comes from Unreal Engine 5 on Windows (see `docs/map-package.md`). Two phases:

- **Phase A, on the Mac, in this repo:** the pipeline writes the map package. The data is already here (`tools/data/big/src`,
  49,500 km² of sources; the road stages of `berat70scale` in `tools/data/big/roads/berat70scale`).
- **Phase B, on the Windows PC** (RTX 5090, 96 GB, 9800X3D): Unreal imports the package and becomes the game.

The Unity player stays as it is (v0.6.0) until Unreal drives better; no new work goes into it.

## Phase A: the map package (Mac)

Every step ran first on the **small map** (`tools/data/big/small_sectors.json`, 9 sectors), then on `berat70scale` (484 sectors).
Code: `tools/package/`; commands in `docs/map-package.md`.

| # | Step | Status |
|---|---|---|
| A0 | Small map's road stages rebuilt (`uv run python -m roads build`) | done, 34 s |
| A1 | `build_world.compute_sector` apart from the Unity chunk writer; package skeleton, manifest, writers (numpy glTF writer, no new dependency) | done; the Unity chunks are unchanged by the split (decompressed, file for file) |
| A2 | **Carved terrain** at 2, 1 or 0.5 m: LiDAR ground, roads burnt in, shoulders, cap, water beds, holes | done: the terrain is never above a road edge (2 cm under at least), seams between sectors 0.000 m |
| A3 | Land cover classes and row directions per vertex, bare-ground colour, orthophoto 4 m and 0.2 m | done |
| A4 | Roads: strips with road UVs, junctions, car parks, skirts, bridge deck sides, French markings; attributes; lane graph | done; Khronos validator 0 errors 0 warnings |
| A5 | Buildings: walls and roofs (faces checked outward), every measure and the facade layout | done |
| A6 | Vegetation, water (levels per outline point), places | done |
| A7 | `check`: decode, seams, ids, lane links, face orientation, road on terrain, previews | done, small map passes |
| A8 | Full `berat70scale` package | see below |
| A9 | Transfer to the PC over the LAN | when the PC is reachable (below) |

Fixed on the way, in the shared code: the water level of a canal depended on the sector's window (up to 0.4 m between sectors;
the Unity world had the same seams), now a function of position only; the drawn road width is x 1.33 (was 1.27).

### Transfer to the PC

Same local network. On the PC, once: Settings > System > Optional features > add *OpenSSH Server*, then in an admin
PowerShell `Start-Service sshd; Set-Service sshd -StartupType Automatic`. From the Mac:

```sh
tools/package/send_to_pc.sh <user>@<pc-address> package/berat70scale-2m          # -> C:/berat/package/berat70scale-2m
```

It writes `SHA256SUMS` and streams the folder as one tar over ssh (Windows has `tar` and no `rsync`). On the PC:
`powershell -ExecutionPolicy Bypass -File C:\berat\package\berat70scale-2m\verify_on_pc.ps1 C:\berat\package\berat70scale-2m`.
Gigabit Ethernet moves about 100 MB/s (the 13 GB package in ~2-3 minutes); Wi-Fi is several times slower. A Windows SMB share
mounted in Finder works too.

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

- Terrain resolution: the package can be built at 2, 1 or 0.5 m (the IGN serves the LiDAR ground at 0.5 m, cached in centimetres).
  1 m over 70 km is ~70k samples a side: the landscape choice of B2 decides what Unreal takes; a coarser landscape can always be
  made from the finer package.
- B2's landscape choice decides how far one Unreal world can reach (70 km today, a département next, France later).
- ODbL: what an OSM-derived package means for a commercial release (see the licences in `docs/map-package.md`).
