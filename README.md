# Berat Racer

A low-poly, sim-arcade driving game set on the **real roads and terrain of Bérat (31370), Haute-Garonne**, built from French open geodata: IGN **LiDAR HD** elevation, **BD TOPO** roads and buildings, IGN orthophotos and OpenStreetMap. About 41 km² are playable, in Unity 6 (macOS / Apple Silicon tested).

![village overview](docs/shots/view_village_overview.png)

| | |
|---|---|
| ![chase](docs/shots/view_chase_spawn.png) | ![church](docs/shots/church_Église_Saint-Pierre.png) |
| ![pharmacy](docs/shots/pharmacy_Pharmacie_Vert_Nature.png) | ![tyre smoke](docs/shots/tyre_smoke_3.png) |

## What is in it

- **Real world**: the terrain follows the LiDAR bare-earth model; roads are the BD TOPO centrelines draped on that terrain with their real widths, bridges, junction levelling and painted centre lines.
- **Procedural buildings**: 3,500 BD TOPO footprints extruded to their height, gable roofs inferred from the LiDAR canopy model, and per-building facades (windows, shutters, doors, shopfronts, awnings, signs, chimneys) chosen by building type: house, barn, shop, pharmacy, town hall, school, church... Types come from BD TOPO attributes plus OSM places (Pharmacie Vert Nature, Mairie, Église Saint-Pierre, Vival...). The church tower is placed and sized from the LiDAR height maximum. Colours follow local architecture (crépi walls, brique foraine, canal tiles, light-blue shutters); see `docs/berat-style-guide.md`.
- **Trees** at the LiDAR canopy maxima (55,000), in round / columnar / conifer shapes.
- **Car**: rigid body + four spring-damper wheels on the heightfield, slip-angle tyre model with post-peak falloff (it drifts), friction circle, handbrake, speed-sensitive steering, downforce.
- **Speed feel** (see `docs/speed-feel-research.md`): FOV 70→100° with speed, look-ahead, acceleration pull-back, shake, radial speed blur + vignette, procedural engine / wind / tyre audio, tyre smoke and skid marks.
- **Cameras**: chase, close chase, hood, bumper, far chase; free look; rear view.
- **Map mode**: 10 m … 1 km zoom (3 steps per decade), pan clamped to the map border, teleport to the exact terrain height under the crosshair.
- **Autopilot** that follows the real road network (demo / test driver).

## Controls

| | Keyboard / mouse | DualSense / gamepad |
|---|---|---|
| Drive | W A S D / arrows | R2 gas, L2 brake / reverse, left stick |
| Handbrake | Space | Square or R1 |
| Reset car | R | Triangle |
| Camera | C, hold B = rear view, hold right mouse = look | D-pad up, right stick = look, R3 = rear view |
| Map | M (Enter / click = teleport, Q E zoom, WASD pan) | Select (Cross = teleport, L1 R1 zoom, left stick pan) |
| Autopilot | P | Circle |
| Volume / mute | `[` `]` / N | |
| Quit | Esc | |

## Run it

1. Install Unity **6000.0.84f1** (Apple Silicon build), open the `unity/` folder, run *Berat ▸ Build macOS* (or `BuildTools.BuildMac` in batch mode). The exported map data is already committed under `unity/Assets/StreamingAssets/berat`, so nothing else is needed.
2. Launch `Build/BeratRacer.app`. Logs: `~/Library/Logs/BeratRacer/berat.log`.

## Rebuild the map data

```sh
uv sync
uv run python tools/fetch.py          # LiDAR HD MNT/MNH rasters + BD TOPO roads/buildings (IGN Géoplateforme)
uv run python tools/fetch_ortho.py    # orthophoto for ground and roof colours
uv run python tools/fetch_osm.py      # OSM points of interest
uv run python tools/export_world.py   # -> unity/Assets/StreamingAssets/berat
```

`tools/fetch.py` has the area (`CX`, `CY`, `HALF`, Lambert-93): change them to build a different place in France.

## Headless tests

The player has scripted test modes (run with `-batchmode`): `-inputtest` (injected keyboard/pad), `-maptest` (pan/zoom/border/teleport), `-shots` and `-smokeshots` (renders landmarks and tyre smoke to `docs/shots`), `-audiotest` (renders the synth to `docs/audio/*.wav`), `-smoke -smokeSeconds N [-autoKmh K]` (autopilot run). `-autopilot` starts the game driving itself.

## Data & attribution

The map is derived from open data. Please keep the attribution if you reuse it:

- **IGN LiDAR HD, BD TOPO®, orthophotos**: © IGN, published under the *Licence Ouverte / Open Licence 2.0* (Etalab). Check the current terms on <https://geoservices.ign.fr>.
- **OpenStreetMap**: © OpenStreetMap contributors, ODbL <https://www.openstreetmap.org/copyright>.
- Source code: MIT (see `LICENSE`).

No game assets were copied from other games; everything is generated in code at runtime. The Bérat style guide was written from public photographs (Wikimedia Commons and street-level pages); the URLs are listed in `docs/berat-style-guide.md`.
