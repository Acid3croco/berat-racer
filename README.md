# Berat Racer

A low-poly, sim-arcade driving game set on the **real roads and terrain of Bérat (31370), Haute-Garonne**, built from French open geodata: IGN **LiDAR HD** elevation, **BD TOPO** roads and buildings, IGN orthophotos and OpenStreetMap. About 41 km² are playable, in Unity 6 (macOS / Apple Silicon tested).

![village overview](docs/shots/view_village_overview.png)

| | |
|---|---|
| ![chase](docs/shots/view_chase_spawn.png) | ![church](docs/shots/church_Église_Saint-Pierre.png) |
| ![pharmacy](docs/shots/pharmacy_Pharmacie_Vert_Nature.png) | ![tyre smoke](docs/shots/tyre_smoke_3.png) |

## What is in it

- **Real world**: the terrain follows the LiDAR bare-earth model; roads are the BD TOPO centrelines draped on it with real widths, level bridge decks (from the LiDAR *surface* model), a network-consistent height profile, painted centre and edge lines, gravel verges.
- **Procedural buildings**: 3,500 BD TOPO footprints (cut out of every road corridor) with facades chosen per building type (house, barn, shop, pharmacy, town hall, school, church...), gable roofs only where the roof truly fits, regional colours (crépi, brique foraine, canal tiles). Types come from BD TOPO plus OSM places (Pharmacie Vert Nature, Mairie, Église Saint-Pierre with its octagonal tower...). See `docs/berat-style-guide.md`.
- **Nature and street furniture**: 55,000 trees and 21,000 hedges / shrubs at the LiDAR canopy maxima, lamp posts in the village, utility poles with wires in the country. Nothing solid stands on a road.
- **Three cars, three characters** (`CarSpec.cs`): a Hot Hatch (RWD), a **Peugeot 406 V6** (FWD saloon, soft ride) and a **Porsche 911 GT3 (992)** (rear-engine, 9000 rpm, big wing and downforce). `F` switches car.
- **Vehicle dynamics** (`CarController`, `Tyre`, `Drivetrain`): per-wheel angular velocity with slip ratio and slip angle from the contact-patch velocity; a combined-slip tyre curve with load sensitivity and lateral relaxation; engine torque curve, clutch (solved exactly against the driven axle each step), automatic gearbox, open / limited-slip differential; ABS, traction control and slip-angle-limited steering; springs, dampers and anti-roll bars over a contact envelope rolled across the heightfield; the whole hull collides with the ground.
- **Look**: real sun with cascaded soft shadows, sky-gradient ambient, a procedural sky with clouds, haze, glossy paint and glass, HDR + MSAA + bloom + ACES tone mapping, fine grain on fields / asphalt / walls, far-terrain level of detail.
- **Speed feel** (`docs/speed-feel-research.md`): FOV 70 -> 100 deg with speed, look-ahead, acceleration pull-back, shake, radial blur + vignette, procedural engine / wind / tyre audio, tyre smoke and skid marks, controller rumble.
- **Cameras**: chase, close chase, hood, bumper, far chase; free look that orbits the car; rear view.
- **Map mode**: 10 m ... 1 km zoom (3 steps per decade), pan clamped to the map border, 5 m grid overlay when zoomed in, teleport to the exact terrain height under the crosshair.
- **Bug-report coordinates**: the top-right box shows the 5 x 5 m cell, local metres, elevation and Lambert-93. `K` copies the spot, `J` jumps to coordinates on the clipboard, `-goto x,z` starts there.
- **Autopilot** that follows the real road network (demo and test driver).

## Physics results (headless test suite, `docs/physics-*.txt`)

| | Hot Hatch | Peugeot 406 V6 | Porsche 911 GT3 |
|---|---|---|---|
| 0-100 km/h | 6.8 s | 9.1 s | 3.8 s |
| top speed | 246 km/h | 236 km/h | 305 km/h |
| 100-0 km/h | 34 m | 39 m | 27 m |
| skidpad, R = 30 m | ~1.0 g | 0.86 g | 1.35 g |

Each car passes 13 checks (acceleration, braking distance and stability, skidpad grip, handbrake turn, hands-off stability at speed, step-steer gain and overshoot). The previous model passed 5 of 13.
Road benchmark (autopilot, 150 s on the real roads): invisible stops 3 -> 0, vertical harshness 0.15 m/s2 (terrain-mesh level), no wheel hops. Whole-hull collision is tested by dropping each car on its side, roof, nose and tail.

## Controls

| | Keyboard / mouse | DualSense / gamepad |
|---|---|---|
| Drive | W A S D / arrows | R2 gas, L2 brake / reverse, left stick |
| Handbrake | Space | Square or R1 |
| Reset car | R | Triangle |
| Car | F | D-pad right |
| Camera | C, hold B = rear view, hold right mouse = look | D-pad up, right stick = look, R3 = rear view |
| Map | M (Enter / click = teleport, Q E zoom, WASD pan) | Select (Cross = teleport, L1 R1 zoom, left stick pan) |
| Autopilot | P | Circle |
| Spot | K copy, J jump to clipboard coordinates | |
| Assists / v-sync / debug | T / V / F3 | |
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

The player has scripted test modes (run with `-batchmode`): `-phystest -car N` (13 vehicle-dynamics checks), `-roadtest` (drive the real roads and measure jolts, wheel hops and phantom stops), `-hulltest` (whole-car ground collision), `-bridgetest`, `-audit` (obstacle clearances), `-gridtest`, `-inputtest`, `-maptest`, `-camtest`, `-shots`, `-carshots`, `-smokeshots`, `-audiotest`, `-smoke -smokeSeconds N [-autoKmh K]`. `-autopilot` starts the game driving itself, `-car N` picks the car, `-vsync` caps the frame rate.

## Data & attribution

The map is derived from open data. Please keep the attribution if you reuse it:

- **IGN LiDAR HD, BD TOPO®, orthophotos**: © IGN, published under the *Licence Ouverte / Open Licence 2.0* (Etalab). Check the current terms on <https://geoservices.ign.fr>.
- **OpenStreetMap**: © OpenStreetMap contributors, ODbL <https://www.openstreetmap.org/copyright>.
- Source code: MIT (see `LICENSE`).

No game assets were copied from other games; everything is generated in code at runtime. The Bérat style guide was written from public photographs (Wikimedia Commons and street-level pages); the URLs are listed in `docs/berat-style-guide.md`.
