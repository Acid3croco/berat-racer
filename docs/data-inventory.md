# Data inventory: current -> target

What the world is made of today, what it should be made of, and the work in between, in priority order:

1. **Roads**: fidelity to the real road, attributes, lane changes, traffic paths.
2. **Terrain**: already right in height; the work is where it meets roads.
3. **Buildings**: outlines and, above all, roofs.
4. **Ground types**: crops, vineyards, parking lots, and everything else a low-poly asset can dress.

The source split (BD TOPO for position and size, OSM for meaning and rules, LiDAR for every height) is in
[data-sources.md](data-sources.md). This page adds what each subsystem reads today, what it ignores, the data
that exists but is not fetched, and the bugs found in the code (file:line, as of `6cb8cd9b`).

## Progress

Work in the order of the goal set on 2026-10-01 (branch `data-inventory`), each step verified before the next: road build report,
`check_roads.py`, a player built into its own `Build/` folder, `tools/playtest.sh` (`-roadtest`, `-autotest`), and before / after shots
(`tools/shot_compare.py`, in [shots/datainv](shots/datainv)). Baseline = master `136bc6a9`.

| # | Step | Status | Measured (small map) |
|---|---|---|---|
| 1 | OSM tags already cached: oneway, levels in `crossing.py`, French / directional limits, surface string | done | 24 one-way conflicts settled by OSM (20 to one-way, 4 reversed), rings kept on BD TOPO (61 / 61 counter-clockwise); 0 crossings on the small map, Herbettes sector: LiDAR 39, OSM 9, survey 3, same spans as master; overlap 0 m², terrain above road 0, LiDAR fit unchanged; roadtest 9.9 m/s² (9.8 - 9.9 before) |
| 2 | Lanes per sample with transition zones; markings from lanes (centre / dividers, edge style, no overtaking, turn arrows) | done | 101 transition zones (8 lane changes), smooth-step replaces the 12 m moving average; centre line on 107 km instead of 243 km (one-lane locals and streets unmarked, measured on the ortho); no overtaking 0.9 km (2.8 s sight, calibrated on the ortho); edge-style changes 0.02 / km (same as before); arrows 0 on the small map, 29 on the Herbettes sector; overlap 0, terrain above road 0, LiDAR fit unchanged; roadtest 9.9 m/s² |

Legend for the *Status* column: **used**: read and has an effect · **dropped**: fetched or present, then ignored ·
**absent**: not fetched at all.

---

## 0. What we hold today

| Layer | Source | Fetched by | Cache | Status |
|---|---|---|---|---|
| Road sections | BD TOPO `troncon_de_route` | `fetch_vectors.py` | `data/big/vec/roads_*` (1.5 GB) | used |
| Buildings | BD TOPO `batiment` | `fetch_vectors.py` | `data/big/vec/buildings_*` (1.8 GB) | used (5 of ~30 fields) |
| Water areas, stream lines | BD TOPO hydro | `fetch_vectors.py` | `data/big/vec/hydro_*` | used |
| Drivable highways | OSM `planet_osm_line` (mace) | `python -m roads fetch-osm` | `data/big/osm/` | used (7 tags) |
| POIs (amenity, shop) | OSM | `fetch_vectors.py:43-53` | `vec/osm_poi*.json` | used (building kind, names) |
| Bare ground `mnt`, height above ground `mnh` | LiDAR HD 2 m rasters | `fetch_hg.py` | `data/big/hg/` (19 GB) | used |
| Orthophoto | IGN, sampled to 4 m | `fetch_hg.py` | `hg/ortho_*.jpg` | used (vertex colour) |

Everything else (land use, parcels, vegetation zones, parkings, walls, lanes, signs) is **absent**.

---

## 1. Roads (priority 1)

### 1.1 Attributes

| Attribute | Current | Target | Source to add |
|---|---|---|---|
| Centreline | BD TOPO, smoothed (3rd-difference least squares, within 0.8 - 2 m) | Same, then **snapped to the real carriageway** | LiDAR `mnt` flat strip; LiDAR **intensity** (asphalt is dark, paint is bright); BD ORTHO 20 cm |
| Width | BD TOPO x 1.27; 28 % estimated from nature / lanes / class | Measured everywhere | LiDAR flat strip + intensity edges; OSM `width`; PCRS kerbs where a city publishes one (Toulouse Métropole) |
| Lane count | **Per direction, per sample**, with transition zones; BD TOPO `nombre_de_voies`, OSM `lanes:forward/backward` and `turn:lanes` per direction (**done**, step 2) | Same | OSM `lanes:forward/backward`, `turn:lanes`, `change:lanes`, `placement`; painted dividers counted in LiDAR intensity |
| One-way | BD TOPO `sens_de_circulation`, cross-checked: an explicit OSM `oneway` wins, except on rings (**done**, step 1) | Same | |
| Speed limit | OSM `maxspeed`, numeric or French code (`FR:urban`, `zone:maxspeed=FR:30`, `maxspeed:type`, `source:maxspeed`), per direction (`:forward/:backward`), else legal default (**done**, step 1) | Same | |
| Surface (paved/gravel) | OSM `surface`, `tracktype` -> `dirt` bool; the string is exported (BM07) (**done**, step 1) | Surface class for physics + look | add `smoothness` |
| Bridge / tunnel / level | LiDAR first, then OSM levels (`bridge`, `tunnel`, `covered`, `layer`, `cutting`), then BD TOPO (**done**, step 1) | Same | OSM `maxheight` |
| Junction control | Give-way lines from rank only | Signals, stops, give-ways, zebras, speed bumps, turn restrictions | OSM nodes `highway=traffic_signals/stop/give_way/crossing`, `traffic_calming`; BD TOPO `non_communication` (turn restrictions); Panoramax sign detections |
| Lighting | OSM `lit` exported, **unused** in the game | Street lamps on lit roads | (already there) |
| Pavements | none | Pavements in towns | OSM `sidewalk=*`; BD TOPO `urbain`; PCRS kerbs |
| Paths, cycleways, towpaths | **absent** | Drawn as narrow tracks, no traffic | OSM `highway=path/cycleway/footway`; BD TOPO `Sentier` (currently skipped) |
| Road number, name | BD TOPO `cpx_numero`, names | Signs at junctions | already in data, `number` **dropped** at export |

### 1.2 Geometry and lane transitions

Today (`geometry.py:87-91`, `ChunkMeshes.cs:281-299`):

- Width is constant per section. A width step is blurred over ~12 m (`WIDTH_TAPER`), which is short for a lane drop at speed.
- The **lane count does not taper**: the marking pattern (centre line vs dividers) switches abruptly at a section boundary.
- Edge-line style is chosen from the *surveyed* width, so it also flips at boundaries.
- Cross-sections are flat: no superelevation, even though the chunk format and physics carry a cross slope.

Target:

- **Lane count per sample**, with a *transition zone* wherever it changes. The zone length comes from the design speed (French
  practice: about 1:20 to 1:30 taper, i.e. 60 - 100 m for a 3.5 m lane at 80 - 90 km/h). The width eases with a smooth-step, not a
  moving average.
- **Markings derived from the lanes and the alignment**, not chosen per piece:
  - a divider merges into the edge line along the taper;
  - solid no-overtaking lines where sight distance is short, computed from our own vertical and horizontal profile (we already know the
    crests);
  - edge lines from the drawn width;
  - arrows from `turn:lanes`.
- **Superelevation** in curves from the horizontal curvature and the class design speed (the profile QP can take the cross slope as a variable).

### 1.3 Traffic path tracking

Today (`RoadFollower.cs`, `Traffic.cs`):

- No traffic data is exported; cars ride the **raw centreline** plus `0.5 x mean half width` (one-way roads: centre).
- `lanes` is ignored: no multi-lane driving, no overtaking lane.
- Connectivity is rediscovered at run time: endpoints within 2.5 m, turns up to 115 degrees, O(N) per decision (`RoadFollower.cs:234-257`).
- At junctions cars run to the node point and pivot; only a yaw-rate limit hides the kink. The smoothed `Ahead()` path exists but
  only the autopilot uses it.
- The offset uses the *mean* half width, so it steps at piece boundaries.
- Give-way lines are ignored; priority is always to the right.

Target: **a lane graph built offline** and exported per chunk.

- One polyline per lane, offset from the smoothed centreline by the *local* lane centre. Lane changes follow the transition zones of 1.2.
- **Junction connectors**: for each allowed (incoming lane -> outgoing lane) pair, a smooth curve (clothoid or cubic Bezier) whose radius
  matches the kerb corner of `junction.py`. Turn restrictions come from OSM and BD TOPO `non_communication`.
- Each connector carries its control (stop, give-way, signal, priority) and the connectors it conflicts with, so yielding is a table lookup.
- Speed per lane point from curvature (lateral g) capped by the limit, precomputed.
- Runtime then just follows the graph: no endpoint search, no pivoting, no offset stepping.

---

## 2. Terrain (priority 2)

Heights from LiDAR `mnt` are right and stay as they are. The issues are all where terrain meets a road (`roads/terrain.py`, `ChunkMeshes.cs`).

| Problem | Cause in code | Fix |
|---|---|---|
| **Jagged "crenelure" borders on steep ground** | Terrain is a regular 4 m grid, adjusted only in height. `bench` drops every corner of every *touched cell* to road level, so on a diagonal road the lowered area is a 4 m staircase. The 6 m ease-back is only 1.5 cells, and the alternating triangle split makes the edge zig-zag by half a cell. The road edge never constrains the mesh. | Short term: draw the shoulder + embankment as a **ribbon mesh along the road** (sampled with the road), and bench the terrain under that wider outline, so the grid steps sit hidden under a smooth slope. Proper fix: **conforming terrain**: insert the road-edge and embankment-toe lines as constraints, drop grid vertices inside, and triangulate (constrained Delaunay per chunk). |
| **Tunnels and underground parking ramps carved open as trenches** | `tunnel` is set from BD TOPO (`source.py:144`) but dropped when road pieces are made (`surface.py:21-35`), so tunnel roads enter the outlines and points and the terrain is carved down along their whole length | Carry `tunnel` to the pieces. Exclude tunnels from the terrain inputs (`surface.py:112, 148`). Draw only the portal: a ramp down to the mouth, plus a dark box. Read OSM `tunnel`, `covered`, `layer<0`, `parking=underground` and `service=parking_aisle` for ramps that BD TOPO lacks. |
| No way to cut holes (portals, ramp mouths) | The chunk format has no per-vertex mask | Add a hole bitmask per 4 m cell (BM07), and discard those triangles in `ChunkMeshes`. |
| Terrain can poke through on crests, mostly on the 16 m mesh | Corners are pushed below the road's *tangent plane*; on a crest that plane lies above the road. About 0.4 m error on the 16 m mesh vs a 0.30 m sink. | Sink of at least cell^2 / (2 x crest radius), or test the corners against the road edges themselves. |
| Wrong road wins where roads are close (terraces, slip roads) | `blend` uses only the nearest road point (`terrain.py:62`) | Take the lowest of the nearby road surfaces; emit retaining walls (BD TOPO `construction_lineaire` *Mur de soutènement* gives where real ones stand). |
| Cracks between 4 m and 16 m chunks | 4 m chunks have no skirts | Skirts on the 4 m mesh too. |
| Asphalt colour bleeding onto shoulders | Vertex colour sampled from the ortho at 4 m | Mask road pixels out of the ortho, the same way canopy is masked today. |
| Car floor differs from drawn terrain | Physics uses a 4-corner average, not the drawn triangles (`WorldData.cs:81-87`) | Sample the drawn triangle. |

Target data for terrain: no new sources needed. Optionally use the classified LiDAR point cloud (class 2 ground) where the 2 m `mnt`
blurs a retaining wall or a kerb, and BD TOPO walls for where to put vertical faces.

---

## 3. Buildings (priority 3)

### 3.1 Data we already hold but ignore

From a sample of 27,000 cached buildings:

| BD TOPO field | Fill | What it gives | Status |
|---|---|---|---|
| `altitude_minimale_toit`, `altitude_maximale_toit` | ~86 % | **Eave and ridge altitudes**: the roof rise directly, no guessing from one `mnh` sample | dropped |
| `altitude_minimale_sol`, `altitude_maximale_sol` | most | Base on sloped ground; uphill and downhill wall heights | dropped |
| `materiaux_de_la_toiture` | ~40 % (mostly `10`, which I believe is tiles; the code table needs checking) | Roof colour and material: terracotta tiles, slate, zinc, concrete | dropped |
| `materiaux_des_murs` | ~40 % | Wall style: stone, brick (Toulouse *brique foraine*), render, wood | dropped |
| `construction_legere` | all | Sheds, carports, light structures | dropped |
| `nombre_de_logements`, `usage_2` | partial | House vs apartment block vs farm | dropped |
| `identifiants_rnb` | most | Key to the national building register (RNB), and through it to cadastre / OSM | dropped |
| Python wall colour `w` | all | Sent in the chunk, never read by C# (`WorldData.cs:153`) | dropped |

`nature` is "Indifférenciée" on 92 % of buildings, so use and style have to come from OSM `building=*`, POIs, the RNB and the
materials above.

### 3.2 Roof algorithm: what is wrong

Today (`build_world.py:540-560`, `ChunkMeshes.cs:213-233`):

- A roof is **pitched** if one `mnh` sample at the centroid is 0.8 m above the mean at the edge, the footprint fills at least 86 % of its
  minimum rotated rectangle (MRR), the area is under 900 m², and the short side is over 3 m.
- A pitched roof is always **one gable over the MRR**, with the ridge along the MRR's long side. Anything else gets a flat cap.

Failure modes found:

1. **L, T and U shapes**: up to 14 % (40 % for churches) of the MRR lies outside the footprint, so the roof and gable triangles hang over
   empty space.
2. **Centroid in the yard**: for concave shapes `mnh_c` is about 0, so the building gets a flat roof or a wrong rise.
3. **Rise measured against the attribute wall height**, not the eave the LiDAR sees, and then clipped to 0.8 - 5 m. Narrow buildings
   come out steep and wide ones flat-ish. The roof altitudes in BD TOPO are not used.
4. **Near-square footprints**: the ridge direction flips arbitrarily. **Hip roofs**, the most common shape in the region, are never produced.
5. **Thin buildings**: the 1.2 m inner buffer is empty, so `mnh_edge` mixes ground and roof.
6. **Sloped ground**: the eave is flat at min ground + wall height. Uphill walls come out short and the base is 0.8 m under the lowest point.
7. **Courtyards dropped** (only `exterior` is exported). Fragments left by the road cut are discarded, and the parts of one building
   decide their roofs independently.
8. **Flat-cap triangulation breaks**: ear clipping with `>=` in `InTri` and a `break` when no ear is found (`MeshBuilder.cs:117-146`)
   leave holes in caps of self-touching rings.
9. **Collision box and chimneys** are fitted to the longest edge or the MRR, not the footprint, so they float over voids.

Target:

- **Straight-skeleton roofs on the real footprint** (holes included). They give hips by default and gables by turning chosen ends into
  vertical faces. Valleys on L / T shapes come out naturally.
- **Shape decision per building**:
  - flat vs pitched from the spread of `mnh` over the whole footprint (or roof-class points, class 6, from the point cloud), not one centroid sample;
  - hip vs gable from whether the ridge reaches the short edges;
  - ridge direction from the line of `mnh` maxima;
  - OSM `roof:shape` and `roof:orientation` win where tagged.
- **Heights**: eave = `altitude_minimale_toit`, ridge = `altitude_maximale_toit`, base from `altitude_*_sol`, with `mnh` as fallback and check.
  The rise comes out of the data, and the pitch follows from rise / half-span.
- **Footprint correction against `mnh` roof edges** (from data-sources.md); keep holes; keep multi-part buildings together.
- **Material-driven style**: roof colour from `materiaux_de_la_toiture`, wall from `materiaux_des_murs`, kind from OSM / RNB / POIs.
  This selects among the low-poly kits: tiled hip house, brick town house, stone farm, metal barn, greenhouse, church.
- Use a robust triangulator (e.g. earcut with holes, or the skeleton's own faces).

### 3.3 Facade decoration: windows, doors, details

Today (`Facade.cs`): the style comes from the building kind. Each footprint edge is split into bays, and a window, door or shopfront
is laid in each bay per floor. The ground height `yg` is taken at the front edge (`ChunkMeshes.cs:361`), as an absolute altitude.

Why whole sides or whole upper floors come out blank:

1. **No windows above the ground floor: a bug.** `Facade.cs:202` reads `if (fy + wy - fy + h > wallH + 0.05f && f > 0) continue;`.
   `wy` is an absolute altitude (about 200 m around Bérat) and `wallH` a wall height (3 - 12 m), so the test is always true. Every
   window above the ground floor of every house, town hall, church, school or office is skipped. Line 203 already does the right
   check (`wy + h > top - 0.35`). **Fixed on this branch by removing line 202**; shops (`ShopEdge`) were not affected.
2. **Short edges are blank.** An edge under 1.4 m is skipped, and an edge of 1.4 - 2.6 m gets 0 bays (`Facade.cs:163, 181-183`). After
   `simplify(0.2 - 0.3)` a side with a jog or a slight bend becomes several short edges, so the whole side gets nothing.
3. **Random blanks on single-bay walls.** On side and back walls 22 % of ground-floor bays and 8 % of all bays are skipped
   (`Facade.cs:199-200`), and on shops' side walls 40 % of the ground-floor bays (`:255`). A wall with one bay loses its only window
   about 28 % of the time; with bug 1 that leaves the side entirely blank.
4. **Kinds that are blind by design**: shed and barn (`mode 3`) get one small window per edge with a 40 % chance. Every BD TOPO
   "Industriel, agricole ou commercial" building that is not a POI ends up as a barn or industrial unit.
5. **Ground-floor windows on slopes**: the ground is sampled once, at the front edge. On a slope the back or uphill walls start below
   ground, so their ground floor is half buried, or the windows sit at ground level.
6. **Party walls are not known**: terraced village houses get windows on the walls they share with their neighbour (hidden), and nothing
   moves those openings to the free walls.

Target:

- **Façades instead of raw edges**: merge near-collinear consecutive edges (within ~15 degrees and 0.4 m) into one wall before laying out
  bays. Then every wall of 2 m or more gets at least one opening per floor.
- **Party walls from footprint adjacency**: an edge that lies within ~0.5 m of another building's edge is a shared wall. It stays blind
  and the end walls next to it get no corner bays. Exported per edge as a flag from `build_world.py`.
- **Ground per wall**: sample the terrain at each wall's ends. Ground-floor openings start above the local ground, and a plinth fills the
  gap on the downhill side (cellar doors on tall downhill plinths).
- **Floors from the data**: `nombre_d_etages`, OSM `building:levels`, then the measured eave height. The count is exported rather than
  re-derived from `wallH / floorH`.
- **Rhythm rules instead of random holes**:
  - windows aligned vertically across floors;
  - the front gets the most openings;
  - side walls get a symmetric pair or a centred single;
  - back walls may drop at most one bay per floor;
  - blank walls only where the data says so (party wall, shed, barn, light construction).
- **Era and material styles**:
  - era from BD TOPO `date_d_apparition`: pre-1950 village (tall narrow windows with shutters, brick or stone surrounds) versus
    post-1970 *pavillon* (wider windows, roller shutters, garage door);
  - wall finish from `materiaux_des_murs`: brick (*brique foraine*), stone, render;
  - an apartment block from `nombre_de_logements` > 2: balconies, stairwell door.
- **Ground floor from use**: shopfronts where an OSM POI or `shop=*` sits in the building (today only POI-tagged buildings get one).
  Garage doors on pavillons facing the road. Barn doors on farm buildings, sized from the footprint.
- **Stable seed**: seed the style from `cleabs` (exported as a hash) instead of the first vertex (`ChunkMeshes.cs:191`), so a rebuild with a
  slightly different outline keeps the same look.

---

## 4. Ground types and parcels (priority 4)

Today there is **no land-use data at all**. The ground is an orthophoto vertex colour on a 4 m grid. The shader guesses tarmac,
grass, tiles or brick from the colour and the slope. Trees and shrubs come from `mnh` maxima, and their shape is picked by a hash, not by species or land use.

### 4.1 Sources to add

| Source | What it gives | Grain | Use for | Access |
|---|---|---|---|---|
| **RPG** (Registre Parcellaire Graphique, ASP / IGN) | Every declared farm parcel with its **crop code** (vines, wheat, maize, sunflower, rapeseed, meadow, orchard...), yearly | Parcel polygons | **Crops and vineyards**: field textures, row direction, seasonal colour, vine-row assets | Open, Géoplateforme WFS (layer name to confirm) |
| **BD TOPO `zone_de_vegetation`** | Forest (broadleaf / conifer / mixed), hedges, vines, orchards, heath | Polygons | Vegetation kind for the LiDAR trees (species asset) | Same WFS we already use |
| **BD TOPO `equipement_de_transport`** | **Parkings**, service stations, rest areas, toll booths, stations | Polygons | **Parking lots** (asphalt surface, bay markings, cars) | Same WFS |
| **OSM `amenity=parking`** (+ `parking=surface/underground/multi-storey`, `parking_space`) | Parkings with type, aisles (`service=parking_aisle`) | Polygons, ways | Parking surfaces, underground ramp entrances (terrain section 2) | mace database |
| **OSM `landuse` / `natural` / `leisure`** | Farmland, residential, industrial, quarry, cemetery, allotments, meadow, scrub, pitches, gardens | Polygons | Fills gaps in RPG (non-declared land), urban fabric, gardens | mace database |
| **OCS GE** (IGN large-scale land cover) | Cover (built, impervious, bare, herbaceous, woody) x use | Vector, ~ 200 - 2500 m² | **Impervious surfaces** (yards, car parks), consistent everywhere | Open, per département (check availability for 31 / 09 / 32 / 82) |
| **OSO** (CESBIO / Theia land cover) | ~23 classes incl. vineyards, orchards, summer / winter crops, grassland | 10 m raster, yearly | Wall-to-wall fallback where vectors have nothing | Open |
| **Cadastre** (Etalab PCI, cadastre.data.gouv.fr) | **Parcel boundaries**, cadastral buildings, place names | Parcel polygons | Field edges, hedges and fences along boundaries, garden walls, front yards | Open |
| **BD Haie** (IGN) | Hedgerows as lines | Lines | Hedge assets along field edges | Open |
| **BD Forêt v2** | Forest type and dominant species | Polygons | Tree species per stand | Open |
| BD TOPO `terrain_de_sport`, `cimetiere`, `reservoir`, `construction_lineaire` / `_ponctuelle`, `troncon_de_voie_ferree`, `ligne_electrique`, `pylone` | Sports pitches, cemeteries, water towers, walls, monuments, railways, power lines, pylons | Mixed | Low-poly landmark assets along the roads | Same WFS |
| **BD ORTHO 20 cm + IRC** (infrared) | Sharp colour; infrared gives NDVI (live vegetation) | 20 cm raster | Bare vs green, crop state, roof colour where the material code is missing | Open |
| **LiDAR HD classified point cloud** | Classes 2 ground, 3-5 vegetation, 6 building, 9 water, 17 bridge; **intensity** | 10+ pts/m² | Roof planes, lane markings, vine rows, separating decks from ground | Open (IGN), heavy |

Fiscal land-use per parcel (MAJIC *nature de culture*: vines, orchards, meadows, building land) exists but is restricted to public
bodies. RPG + OCS GE + OSM cover the same ground openly.

### 4.2 Target

- A **ground class per 4 m terrain cell** (or per constrained-mesh polygon once terrain conforms):
  - vineyard, orchard, cereal, maize / sunflower, meadow, forest floor, garden, yard;
  - **parking**, asphalt, gravel, sand, quarry, cemetery, pitch.
- Precedence: OSM / BD TOPO specific polygons > RPG crop > OCS GE > OSO > orthophoto colour.
- The class picks:
  - the shader's ground material (replacing the colour guess);
  - scatter rules: vine rows along the parcel's long axis (check the direction against `mnh` row texture), crop stubble or furrows,
    parked cars and bay lines on parkings, headstones on cemeteries;
  - edge assets along parcel boundaries: hedges from BD Haie, fences and walls from cadastre lines that touch roads.
- Vegetation kind per tree from `zone_de_vegetation` / BD Forêt (conifer vs broadleaf vs poplar line vs orchard), instead of a hash.

---

## 5. Order of work

| # | Work | Data | Effort | Effect |
|---|---|---|---|---|
| 1 | **Lane graph + junction connectors** exported offline; traffic follows it | Existing BD TOPO + OSM `lanes`, `oneway`, `turn:lanes` | M-L | Smooth traffic, multi-lane driving, no pivoting |
| 2 | **Per-sample lane count with transition zones**; markings derived from lanes and sight distance | Same | M | Clean lane drops and merges, realistic markings |
| 3 | Read every OSM road tag already fetched (`oneway`, `bridge`, `tunnel`, `layer`, `cutting`, non-numeric `maxspeed`, `surface` string) | Already cached | S | Fewer wrong levels, right limits, more surface kinds |
| 4 | **Tunnels out of the terrain**, portals, hole mask (BM07) | BD TOPO + OSM `tunnel` / `covered` / parking | M | No more trenches over underground ramps and underpasses |
| 5 | **Road ribbon embankment**, then conforming terrain | none | M, then L | No crenelated edges on slopes |
| 6 | Superelevation in the profile QP | none | M | Real-feeling curves |
| 7 | **Straight-skeleton roofs** using the BD TOPO roof altitudes and the materials | Already cached | M | Roofs fixed: hips, L shapes, right pitch, right colour |
| 7b | **Facade layout**: façades from merged edges, party walls, per-wall ground, floors from data, rhythm rules, era and material styles (the upper-floor bug is already fixed) | Already cached | M | Windows on every free wall and every floor |
| 8 | Widen the vector fetch: OSM landuse / parking / nodes, BD TOPO vegetation / transport equipment / walls, RPG | New WFS layers + mace | S-M | All later ground work is unblocked |
| 9 | Ground class per cell + material + scatter (vines, crops, parkings) | 8 | L | The low-poly dressing the style needs |
| 10 | Centreline and width snapped with LiDAR intensity and flat strip; lane counts from paint | LiDAR point cloud | L | Road fidelity where the survey is off |

Items 3, 7 and 8 are cheap because the data is either already on disk or one WFS layer away. Items 1-2 are the core of the road
priority. Item 5 can start with the ribbon; the conforming mesh only pays off together with item 9.
