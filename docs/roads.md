# Roads

How the game's roads are made from survey data, how to rebuild them, and how to check the result.
Code: `tools/roads/` (one module per stage), consumed by `tools/build_world.py`, rendered and driven by `ChunkMeshes.cs` / `RoadIndex.cs`.

## Rebuild loop

```sh
cd tools
uv run python -m roads fetch          # optional: the source tiles of the area (build does it too; sources.py)
uv run python -m roads build          # every stage, tile by tile (roads/tiled.py), report in data/big/roads/<area>/report.json
uv run python build_world.py --list data/big/small_sectors.json --out ../world_small
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

Both steps only redo what changed. The road pipeline runs tile by tile (3.2 km, the sectors) in four passes (alignment, network,
heights, surface; docs/build-scaling.md): every tile's result is kept in `data/big/roads/<area>/<pass>/` with a hash of what it was made
from (the content of its inputs, the tuning and the code of its pass); the world builder leaves the same kind of key per sector in
`<world>/keys`. A tile or sector whose key is unchanged is skipped. After a local change (one override, one road) the tiles whose
windows see it are made again and the sectors around them rebuilt; after a change of tuning or code everything is. `--fresh` on either
command ignores the keys. `compare_roads.py A B` and `compare_worlds.py DIR_A DIR_B` compare two builds (heights, sector files; chunk contents).

## Data

| What | Source | Used for |
|---|---|---|
| Centrelines, connectivity | BD TOPO `troncon_de_route` | geometry (planimetric accuracy 2.5 m) |
| Carriageway width, lanes, one-way, class, urban flag, bridge / tunnel level | BD TOPO | width, markings, traffic, decks |
| Posted speed limit (per direction, French zone codes), one-way, surface / track grade, lit, missing names, lanes, width, levels (bridge / tunnel / layer / cutting / covered) | OpenStreetMap (`planet_osm_line` of the massif-extractor database) | matched onto each BD TOPO section by position and direction |
| Ground and surface heights | IGN LiDAR HD (2 m) | height profile, bridge decks |

In the 10 x 10 km around Bérat BD TOPO is the richer source (surveyed widths on 72 % of the sections, lane counts, full connectivity);
OSM adds 138 posted limits, 10 surface corrections, 99 names. OSM widths exist on 7 ways only. Both stay in the pipeline: the merge is per attribute.

One-way: an explicit OSM `oneway` (`yes`, `-1`, `no`) wins over BD TOPO. On the small map the two disagree on 24 sections, all single-lane
village streets where the OSM way is coherent along the street and BD TOPO is not (Rue du Château: one section of the loop
one-way, three two-way); 20 become one-way, 4 change direction. Rings (roundabouts) keep BD TOPO's sense: it is counter-clockwise on all
61 sections where the shape shows it, and the direction of a closed OSM way is ambiguous where it is projected.
Speed limits: a numeric `maxspeed`, else a French code in `maxspeed`, `zone:maxspeed`, `maxspeed:type` or `source:maxspeed`
(`FR:urban` 50, `FR:rural` 80, `FR:30` / `FR:zone30` 30, `FR:living_street` 20, ...); `maxspeed:forward` / `:backward` give a limit
per direction (none on the small map; ~30 ways over berat70). The surface string (asphalt, concrete, gravel, sett, ...) is exported.

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
5. **crossing**: where two drawn links cross without a junction, one passes over the other. The LiDAR decides where it can (below),
   then the OSM levels (`layer`, else `bridge` 1, `tunnel` / `covered` -1, a `cutting` half a level lower; the span is the OSM bridge),
   then BD TOPO's bridge flag, which is nearly always right. In the Herbettes sector of berat70 (53 crossings) the LiDAR decides 39,
   OSM 9 (all agreeing with the survey), the survey 3; 2 are tunnels. The road passing over is the one whose ground climbs
   3 m above the crossing on both sides, steeply (within 15 m: a trench wall or an abutment, not a valley side), within 60 m of it,
   while the other road's ground stays down. Its span becomes a bridge, bank top to bank top; a surveyed bridge of the road below
   within 15 m of the crossing, lying flat on the floor, is dropped. Bridge and tunnel flags are therefore per link segment, and a
   surveyed section is cut into several pieces where a span starts or ends. On berat70 this changes the two crossings of the
   Canal du Midi over the ring road at Herbettes / Rangueil (towpath and avenue on the aqueduct side surveyed as on the ground,
   the ring road in its trench surveyed as the bridge) and nothing else.
   Tunnels: a section is a tunnel where BD TOPO puts it below ground or OSM tags `tunnel=yes` (or a service way at `layer<0`: a
   ramp into an underground car park; `building_passage` and `covered` stay at ground level). The LiDAR keeps it only where the
   ground stands at least 3 m above the straight line between its two portals: on the small map both BD TOPO tunnels (187 m of
   the Route de Carbonne) run at grade under tree canopy and are dropped; on the Herbettes sector the two ring-road bores
   (70 and 72 m) and a 43 m street are kept, 4 other runs dropped.
6. **profile** (vertical): a quadratic programme (Clarabel, an interior-point solver: a tile converges in about 17 iterations to
   the optimum, where OSQP at a practical tolerance stopped up to decimetres short on tiles with fixed neighbours). Unknowns: the height of every sample outside junctions and one plane per
   junction. Objective: stay on the LiDAR ground (robust: samples far from the solution are down-weighted), minimise the third
   derivative (grade changes become parabolas). Constraints: maximum grade and minimum crest / sag radius per class.
   Every arm lies on its junction's plane up to its mouth. The cross slope of every sample is an unknown of the same QP
   (superelevation): it follows a design value, half the lateral demand v^2 / (127 R) at the class's design speed up to 7 %, banked
   towards the inside of the bend, on motorways, ramps, main, collector and local roads (streets, rings and tracks stay flat),
   smoothly (changes shorter than 10 m are smoothed away), never beyond 10 %; on a junction plane it is the plane's slope across
   the road, so each arm meets its junction without a step. Small map: 89 - 91 % of the length in bends under 300 m radius is
   banked at least 1 % towards the inside (local 16.0 / 18.0 km, collector 12.7 / 14.0, main 1.1 / 1.2), at most 8.4 %; the
   steepest junction plane went from 10.2 to 8.4 %.
   Bridges aim at their deck (surface model), not at the ground below.
   The area is solved in 3.2 km tiles, in four rounds like the colours of a 2 x 2 checkerboard (tiles of one round never touch and
   run in parallel). Each tile is solved with a 600 m halo of its neighbours: what a neighbour already solved is fixed and continued
   smoothly, what is not solved yet is solved along and thrown away. A road crossing a tile border is one continuous profile.
   Against one solve of the whole small map the tiled result differs by 2 - 8 mm on average (a few cm at the 99th percentile).
7. **lanes** (`roads/lanes.py`): lanes each way at every sample, the lines between them, where overtaking is forbidden, edge
   lines and turn arrows. Counts come from BD TOPO (OSM `lanes:forward/backward` and `turn:lanes` per direction where tagged and
   the lanes fit in the surveyed width). A two-way road the survey gives one lane is unmarked unless it is a collector: on the
   20 cm orthophoto a centre line shows on most two-lane roads (8 / 10) and one-lane collectors (21 / 37), on almost no one-lane
   street (1 / 9) or local road (0 / 11). Where width or lanes change between two sections of a link, the change is a transition
   zone centred on their boundary, as long as a car at the design speed needs to shift sideways by the change at 1 m/s (1:25 at
   90 km/h), at least 12 m; width and lane count ease through it with a smooth-step (this replaced the moving average), so a
   dropped lane narrows to nothing and its divider runs into the edge line. Overtaking is forbidden where the sight distance is
   under 2.8 s at the limit: over our own height profile (eye and object 1 m up), and round bends as far sideways as the LiDAR
   surface model shows nothing above eye level (hedges, woods, cuttings, houses, scanned 40 m out). The threshold is calibrated on
   the orthophoto: solid lines are rare here (about 1 in 45 visible marked points; dashed even where we compute 44 - 60 m), and
   the MUTCD passing sight distances would have marked 38 % of the length. Edge lines on marked roads only, dashed from 7.0 m
   drawn width and solid from 8.3 m, the majority over 40 m so a section boundary does not flip them.
8. **lane graph** (`roads/lanegraph.py`): what traffic drives on, built offline. Lanes run at each lane's local centre (offset from
   the smoothed centreline by the eased lane layout), split where a zone changes the lane count (a dropped lane merges across the
   zone into the inner one, an added one branches off the outer one). Junction connectors are cubic Béziers whose handles make a
   circular arc between the arriving and leaving tangents (round the island, counter-clockwise, where a roundabout is swallowed
   into one junction). Movements: no U-turn on the same arm and nothing sharper than 160° unless it is the only way out;
   `turn:lanes` says which lane may turn where, else the leftmost lane turns left and the rightmost right; OSM restriction
   relations (Overpass) and BD TOPO `non_communication` remove more (sources.py caches them per tile with the OSM stop /
   give-way / lights nodes in `data/big/osm/controls_<tag>.json.gz`). Control per arm: an OSM sign or lights on the arm (the
   unsigned arms of a signed junction have the priority), else entering a roundabout gives way, the lower-ranked arm gives way,
   equal ranks give priority to the right. Each connector lists the connectors it gives way to (paths that cross or merge, of
   higher priority; on equal terms the left turn yields). Speed per point from the curvature (2.4 m/s² lateral) under the limit.
   Dead ends of two-way roads get a U-turn. Small map: 13,647 elements, 0 joins turning more than 10° (worst 6.6°), 0 gaps,
   2 lanes without exit (one at the build margin, one where a two-way section turns one-way against it).
9. **surface**: pieces (centreline + both edges + drawn flags per section and bridge span), junction meshes, footprints and tangent planes for the terrain.

`build_world.py` then shapes the terrain around that surface (`roads/terrain.py`):

- *ribbons*: along every road edge and junction kerb an embankment ribbon is computed with the road's own samples: a 1.5 m
  shoulder 4 cm under the edge, a 1:2 slope (fill or cut) to the LiDAR ground, the ground out to at least 6.5 m from the edge, never
  closer than a metre to another road and never past 70 % of the radius on the inside of a bend (it would fold); then a 6 m apron
  that redraws the terrain as it was before it was lowered (a lowered 4 m triangle reaches that far). The game draws them with the
  terrain (gravel strip, ground colour, a lip down at the end) and the car drives on them: the 4 m grid's steps lie under a surface
  that follows the road. This replaced the 6 m blend, which the 4 m grid could only follow as a staircase on diagonal roads.
  Verge roughness beside paved roads (second difference of the visible ground along lines 1 - 14 m from the edge, every 4th chunk):
  p99 0.162 -> 0.112 m, steps over 0.25 m 0.43 % -> 0.22 %; at 4 - 8 m from the edge p99 0.21 - 0.24 -> 0.11 - 0.14 m. A conforming
  triangulation was not needed for that.
- *drape*: every 4 m vertex under a ribbon is lowered 6 cm under the lowest ribbon there.
- *bench*: every corner of a terrain cell touched by a road is lowered below the tangent planes of the road points that can share
  that cell, less cell^2 / 2R over a crest (the road falls away from its tangent plane there).
- the 16 m terrain, drawn far away without ribbons, keeps the old blend (ground eased to the road over 6 m), benched under the road;
- the ground colour of vertices within 2 m of a road comes from the nearest bare ground, not the photo's asphalt (luminance near
  the roads was 4.5 above the ground 10 - 14 m away, now 0.2).
- tunnels are not terrain inputs: the hill stays over them. The 4 m cells over a tunnel road where the ground is less than 5 m above
  it (the portals) are cut out (BM07 hole list); the game draws the tube (dark inside, concrete outside), a concrete headwall at
  each portal, and treats the tunnel road like a deck: it carries whatever is under its ceiling, not the hill above. A triangle whose corners are all below the road's tangent planes is below the road, so this holds for the 4 m mesh
  and for the 16 m mesh drawn beyond 1.6 km alike. The game no longer cuts terrain at run time.

Buildings are cut out of the real surface polygons and plants keep clear of them (a 1 m raster of the surface). Their roofs
(`tools/roofs.py`) are straight skeletons of the footprint and its courtyards: hipped by default, an end becomes a gable where the
LiDAR surface model stands within 40 % of the ridge just inside it (or OSM tags `roof:shape`), flat under 0.5 m of rise; eave and
ridge from BD TOPO's roof altitudes, checked against the LiDAR (it wins beyond 2.5 m); the colour from BD TOPO's roof material
(1 tiles, 2 slate, 3 metal, 4 concrete). A wall shared with a neighbour is a gable, so a terrace keeps one ridge. Facades
(`tools/facades.py`) are laid out offline: nearly collinear outline edges are one wall, shared walls are blind, each wall has the
ground at both ends, floors come from BD TOPO / OSM / the eave height, and every free wall gets at least one opening per floor in
aligned columns (door on the road side, a garage after 1970, shopfronts, balconies on blocks of flats); the era and wall material
choose shutters and finish, the seed comes from `cleabs`. A BM07 building record ends with the roof material, the roof mesh
(vertices, triangles, which are gable walls), then the facade: seed, floors, era, wall material, floor height, and per wall its
first outline point, edge count, flags (front, shared, blind, back), ground at both ends and openings (floor, kind, along, width,
height, sill); its collision rings carry the courtyards.

## Chunk format (BM07)

After the terrain grids a chunk now carries the 16 m heights (26 x 26 floats), then per road piece: flags, class data, surveyed width,
mean half width, distance along its link, name, per point the centre and both edges (x, y, z), a drawn flag per segment and the
points where a give-way line is painted;
then the junction meshes (vertices, triangles, outline edges with a mouth flag), the water areas and stream lines, and (BM06) the
outlines of the water carried by a structure: a canal on an aqueduct over a road. The builder finds them (`carried_water` in
`build_world.py`: water standing 1.5 m or more above the ground, with a road on the ground passing under it, carried on until the
ground under the water is back at the depth of a bed, so the ends rest on the banks); the game draws their
concrete channel (walls where the water stops, a floor 1.8 m under the surface) and keeps the ground under them dry. A BM05 chunk
is a BM06 chunk without that list; a BM07 road record adds, after the give-way lines, the OSM surface string, the limit against the
piece's direction (the header byte is the limit along it), the lane lines (kind, then per point the fraction of the way from the
left edge to the right one), per segment the paint flags (painted, no overtaking along / against, edge style) and the turn arrows;
and after the carried water a BM07 chunk lists the lane graph elements passing through it (id, kind, control, limit, road
attributes, points with their speed, successors, the lanes beside, the connectors it gives way to), then the terrain cells cut away
(tunnel portals). The road flags byte has bit 8 for a tunnel piece. Road records end with the ribbon profile of each point and
side (shoulder, toe distance / height, outer distance / height, apron heights and width), junctions with each vertex's kerb normal
and profile. Then (BM07) the ground around the paved surfaces: one height field blending the paved edges into the terrain (`tools/stitch.py`), as triangles (x, y, z) and per vertex its weight (1 on a paved edge, 0 where it is the terrain); the 4 m cells it reaches are in the hole list. Road and junction records carry no ribbon any more. The 4 m terrain has skirts against its neighbours (not on the world's border). `WorldData.cs` reads BM05 - BM07, `LegacyChunk.cs` adapts older worlds (BM02 - BM04) so they still
load with their old look.

Near files (`n_*.bin.gz`) are BN02: after the trees and shrubs (BN01), the kind of every tree (unknown, broadleaf, conifer,
poplar, fruit), the ground class and row direction of every 4 m vertex (`tools/ground.py`), the vine rows (x0, z0, x1, z1), the
parking bays (x, z, heading, parked car), the hedges (height, then the points) and the car-park surfaces (vertices x, y, z, triangles: drawn with the road material and driven as asphalt). The terrain mesh carries class and direction in
its second vertex channel, flat over each triangle, for the shader's ground materials; the embankment ribbons take them from
their toe outward. BN01 near files still load (no ground classes).

The physics surface is the drawn one: `RoadIndex` hashes the very triangles that are rendered.

## Traffic

`Traffic.cs` and the autopilot drive a `Follower`. On a BM07 world it is `LaneFollower`: the route is a list of lane graph element
ids extended at random among the successors (paved first), the path is the elements' own polylines (no search for the next piece,
no pivot at a node, no offset stepping at piece boundaries), speeds come from the precomputed curvature speeds braking ahead in
time, and at a connector from its control and the connectors it must let through (taken when a car is on them or about to enter
within ~4 s; after 5 s of everyone waiting, go). Cars overtake on the left on multi-lane roads and move back right when the lane is
free. Older worlds (and `-roadfollower`) use `RoadFollower`, which rediscovers the network from the road pieces at run time.

The traffic log reports *kinks*: a car's path heading jumping faster than 90°/s while it drives. Small map, same build, 400 s:
18.5 kinks / km with the road pieces, 1.0 / km on the lane graph (0.7 / km in a 240 s run); the mean traffic speed falls from about
36 to 28 - 33 km/h because cars now stop at stops, give way and take junction corners at their radius.

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

- Each road object is computed by the tile that owns it, from a window around the tile; the smoothing order (class, length up to
  1.6 km, smallest section id) is the same in a tile and in a whole area, so the tiles reproduce a whole-area build except within
  ~200 m of a tile border, where a stroke continues the curve an earlier round fixed (docs/build-scaling.md, step 3).
- Two roads closer than a terrain cell at different heights (a village terrace) cannot both sit on a 4 m terrain grid: the upper
  one gets a retaining wall along its edge.
- Streets, rings and tracks are not banked; there is no crown (cross-sections are planes).
- No pavements; lighting (`lit`) is carried in the data but not used yet. Give-way lines are painted where a paved road meets a
  junction as the lower-ranked arm (equal ranks get none: priority to the right); stop signs are not in the data.
- Turn lanes that do not fit in the surveyed width are not drawn (Herbettes sector: 39 arrow sets skipped, 29 arrows drawn); the
  small map has no `turn:lanes` tags.
