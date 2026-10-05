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
| A8 | Full `berat70scale` package | done at 2 m and at 1 m with the 0.2 m orthophoto (below) |
| A9 | Transfer to the PC over the LAN | when the PC is reachable (below) |

**`berat70scale` at 2 m** (`package/berat70scale-2m`, 2026-10-05): 484 sectors (4,956 km², Toulouse to the Pyrenean foothills) in
37.6 min at 5 workers after the roads (20.5 min); 13 GB. 752,748 buildings, 9.0 M trees, 231,347 road pieces, 921,390 lanes,
106 M road triangles. `check`: no problem; the terrain under every road edge (2 cm at least), seams identical, 1,568 of 62 M faces
ambiguous (walls between adjoining buildings), retaining walls up to 11 m closed by skirts (bridge abutments in Toulouse).

**`berat70scale` at 1 m** (`package/berat70scale-1m`, 2026-10-05, the one to import): the same, the terrain from the 0.5 m LiDAR
ground (3201 x 3201 vertices a sector, heights 50 to 755 m in 1.1 cm steps) and the 0.2 m orthophoto in every sector (16 tiles of
4000 px). 59.6 min at 5 workers; 38 GB. `check`: no problem. Sources fetched first by `fetch-hires`: 9,650 tiles, 13 GB of ground
and 14 GB of orthophoto, about 8 h at the 4 requests the IGN WMS pool allows.

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
| B1 | Install: Epic Games Launcher, Unreal Engine 5.7, Visual Studio 2022 (C++ game workload), Git + Git LFS | **done 2026-10-05** on **UE 5.8.3** (5.8 was the release when installing). Package `berat70scale-1m` received and verified twice (15,979 files, 0 wrong, the second after the transfer ended). VS 2022 17.14 (NativeGame, NativeDesktop, Win11 SDK 22621), Git LFS 3.7.1, uv. C++ project `unreal/BeratRacer` (no template: the importer makes the World Partition map). Generated content (`Content/Berat`, `__ExternalActors__`) is rebuilt by the importer, not committed. Live Coding off (it blocks command-line builds) |
| B2 | Importer: an editor Python script reads the package (manifest, sectors), converts coordinates, creates the landscape from the tiled heightmaps, the layer weightmaps from `classes.png`, the holes as landscape visibility | **done for 3 x 3 (4..6, 4..6), 156 s end to end.** Offline (`unreal/importer`, uv): grid 9691² (19 x 19 components of 2 x 2 sections of 255 quads; padding from the neighbour sectors), ScaleZ 137.6974, Z 40250.54 cm; heights checked by trace against `height.tif` at 25 points: **0.50 cm max** (the 16-bit step). Landscape import: **87 s with 510-quad components vs 2738 s with 127-quad ones** (5776 tiny textures built one by one). **Decision: one World Partition landscape, 510-quad components, not Nanite landscape** (tried: 1151 s and 75 GB to build for 3 x 3, cracks between proxies); landscape LOD distances pushed out (LOD0 screen size 1, distributions 4) so far terrain does not cut the roads. glTF (Interchange) bakes the sector corner and converts the axes: actors at the origin. Roads drawn without collision; **collision from the drivable surfaces only** (`roads_collision.glb`, normal z > 0.5: the skirts made 5-10 cm walls that stopped wheels dead); roads, openings, water without Nanite; buildings Nanite with a full-detail fallback (collision uses the fallback). Ground colour: `colour.jpg` mosaic as the far albedo. Windows, doors, shutters from the facade layout (339k triangles). The editor is driven through a file-drop channel (`init_unreal.py`, `ue_cmd.py`; Python remote execution did not start on 5.8, `-ExecutePythonScript` closes the editor when the script ends) |
| B3 | Terrain material: one layer per class with Fab / Megascans surfaces, auto-blend by slope, `colour.jpg` as a far tint, `rows.png` for furrows | the look of the countryside comes from here. **2026-10-05**: landscape grass (PN Grass Library meshes, non-Nanite: Nanite grass hung the GPU) at true height (the landscape's Z scale 1.377 compensated), none on roads (prep.py rasterizes the road surfaces as bare before the weightmaps). **Grip per surface**: 9 physical materials (asphalt 0.8 ... grass 0.5, mud 0.38) on the landscape layers and road materials, read natively by the Chaos wheels. **2026-10-06**: Megaplants vegetation drew the default grey material (its foliage master material lives in the engine's ProceduralVegetationEditor plugin, now enabled); furrows along each parcel's real row direction (`prep_rows.py`); no grass on road edges (road mask dilated 2 vertices); streams kept in their channels (31 % of stream levels stood above the lower bank, reported upstream) |
| B4 | Roads: import `roads.glb` as Nanite static meshes, asphalt / gravel / marking materials, collision on | wear, puddles, decals later |
| B5 | Buildings: import `buildings.glb`; later replace by PCG facades and roofs from `buildings.geojson` (use, era, walls) | buildings are the weakest part of the look; PCG is where they improve. **2026-10-05**: 13 finishes from the data (use, wall material, era, roof shape and code), windows / doors / shutters with depth, chimneys, roof overhangs; **garden walls** derived where garden or yard meets a street near a house (no property lines in the package): low walls in the house's finish and colour with a coping, a third with railings, a quarter as hedges, gaps for gates and capped pillars; railings cut by a masked material (0.94 M triangles, 13,048 hedge plants for 3 x 3; geometry bars cost 20 fps); gutters and downpipes (58k parts), world-space weathering and rain streaks, rough render grain; street bins by gates on village streets (999), benches by public buildings (52); plants and props cleared off road surfaces (14,498) |
| B6 | Vegetation: PCG graph spawning tree and shrub assets from `vegetation.json.gz` (kind, height), hedges along their lines, vine rows | **Done for 3 x 3, 2026-10-05**: 330,933 plants (trees by kind and LiDAR height, shrubs, hedge plants every 1.6 m, vines every 1.2 m) as **Quixel Megaplants** (oak, beech, hornbeam, alder, birch, spruce, black poplar, goat willow, hazel, elder; 4 variants each) in `InstancedSkinnedMeshComponent`s per sector, **Nanite Foliage on** (`r.Nanite.Foliage`, assemblies). Measured: ticking skinned components cost 6 ms + 11 ms end-of-frame updates (47 fps at any resolution, CPU-bound); **ticks off: 1440p 8.2 ms (GT 3.0, RT 6.5, GPU 5.7), 4K 108-119 fps, p99 94-112**. Static bakes of the trees lose the leaves (the assembly parts are skinned; a hand-made static assembly asserts in the engine): not used. Shrubs and hedges cull at 450 m, vines 300 m, trees 2.5 km. Landscape grass off until the Megascans grass is in |
| B7 | Water: Water plugin bodies or simple planes at the package levels | |
| B8 | Sky and light: Sky Atmosphere, Volumetric Clouds, Lumen, time of day | **Done 2026-10-05**: sun from the Sun Position plugin at Bérat's latitude, moon, 24 min day; exposure EV100 2..15 so night stays night; 4,938 lanterns along lit / urban roads with a pool of 64 real lights following the player, emissive heads; car head and tail lights (brake lights) |
| B9 | Driving: Chaos Vehicles stock car, chase camera, gamepad | **milestone 1** on the small map. **Drivable 2026-10-05**: Epic's template off-road and sports cars (copies reparented onto `ABeratCar`: camera, lights, keyboard + gamepad, Chaos presets). `-BeratTest` drives a lane-graph autopilot and reports frame times: ~300 m of Bérat streets at 45-50 km/h; **4K offscreen 144-152 fps avg, p99 127-135; 1440p 172-200, p99 155-159** (no vegetation, no traffic yet). Five cars since: hatchback, SUV and pickup built natively from the Vehicle Variety Pack meshes, sports car and off-road buggy. **Off-road 2026-10-05**: wheel puffs per surface (dust, gravel, grass bits, mud; tyre smoke on hard slides on asphalt) from Niagara lightweight emitters, spawned pooled at the contact points; gearbox shift points follow the throttle on Chaos's own gearbox (hatchback 3rd at 38 km/h, 1680 rpm); `-BeratOffroad=x,y,heading` test drive: 136-150 fps across fields with ~900 puffs; tyre tracks (deferred decals from the rear wheels in soft ground, fading after 12 s), dust kept out of the camera (no particles within 2 m, fade within 6 m): 140-152 fps. R resets onto the nearest lane; keys stay mapped after a car change **2026-10-06**: full throttle no longer sticks in 1st (the shift schedule refused every upshift: 53-76 km/h tops; now hatchback 0-100 6.6 s, sport 4.3 s / 218 km/h); brakes from physics for 1.05 g (were 1.3-1.8 g); mesh-built cars get the template grip balance and springs for their mass; arcade handbrake (rear grip 60 %); `-BeratHandling` (launch, braking, circle, lane change, handbrake turn on a flat slab). Crash physics: traffic within 15 m is a Chaos body with its mass, steered by velocity; an impact makes a free wreck (`-BeratCrash`). Map and minimap (stylized block map, `prep_map.py`; minimap with traffic dots, full map on M) |
| B10 | Scale: `berat70scale` with World Partition streaming and HLOD, profiling | **milestone 2**. 3 x 3 at 4K offscreen with plants, lamps and 57 traffic cars (kinematic, IDM on the lane graph): **120-135 fps avg, p99 ~109**; one 400 ms hitch per run (shaders compiled on first sight in the unpackaged game: check with a packaged build and a PSO cache) **Correction 2026-10-06**: off-screen test runs ignore `-ResX/-ResY` (they rendered 1280x720), so the figures above labelled 1080p / 4K were 720p; with `-ExecCmds="r.SetRes WxHw"` and everything on (Megaplants with their real materials, furrows, garden walls, dynamic traffic, minimap, engine sound): **1080p day 131-134 fps (p99 92-108), 4K day ~103 (p99 77-82), 4K night ~87 (p99 69-74)** |
| B11 | Later: custom tyre model (port of the Sim model's forces), traffic on the lane graph, wheel input and force feedback, online play (Unreal replication), UI, Windows packaging | one at a time, after milestone 2 |

## Open questions

- Terrain resolution: the package can be built at 2, 1 or 0.5 m (the IGN serves the LiDAR ground at 0.5 m, cached in centimetres).
  1 m over 70 km is ~70k samples a side: the landscape choice of B2 decides what Unreal takes; a coarser landscape can always be
  made from the finer package.
- B2's landscape choice decides how far one Unreal world can reach (70 km today, a département next, France later).
- ODbL: what an OSM-derived package means for a commercial release (see the licences in `docs/map-package.md`).
