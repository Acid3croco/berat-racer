"""Every tunable of the road pipeline, in one place.

Lengths are metres, speeds km/h, grades are rise / run. A road class bundles the design rules of one kind of road; an edge gets its class
in `source.classify`. Change a number here, run `uv run python -m roads build`, look at the report and the debug renders.
"""
from dataclasses import dataclass

# ---------------------------------------------------------------- widths

WIDTH_SCALE = 1.27                # drawn carriageway = surveyed width x this (the game had 1.15; +10 % on top: easier cruising, cars are wider than they look)
MIN_WIDTH_PAVED = 3.6             # narrowest drawn paved road (one-way)
MIN_WIDTH_TWO_WAY = 5.0           # narrowest drawn paved two-way road: two cars must be able to pass (the survey has many at 3 - 3.5 m)
MIN_WIDTH_DIRT = 3.2
WIDTH_TAPER = 12.0                # a change of width along a road is spread over this length

# surveyed width missing: full carriageway width by BD TOPO nature, then by importance
WIDTH_BY_NATURE = {"Rond-point": 6.0, "Chemin": 3.0, "Route empierrée": 3.2, "Sentier": 1.4}
WIDTH_BY_IMPORTANCE = {"1": 7.2, "2": 7.0, "3": 6.2, "4": 5.8, "5": 4.8, "6": 4.4}
WIDTH_DEFAULT = 5.2
WIDTH_BY_LANES = {1: 3.0, 2: 5.0}  # no surveyed width but a lane count: a single-track road is ~3 m, a two-lane one ~2.5 m per lane
LANE_WIDTH = 2.8                  # each lane beyond two
LANE_MIN_WIDTH = 2.6               # a road cannot have more lanes than fit at this width each (the survey sometimes says otherwise)

# roads running side by side closer than their widths (dual carriageways, slip roads): each keeps its share of the gap
CLAMP_GAP = 0.6                   # ground left between the two
CLAMP_MIN_HALF_WIDTH = 1.25       # never narrower than this (then they overlap: the data has them on top of each other)
CLAMP_PARALLEL = 0.87             # cosine of the largest angle between two roads still called side by side (30 degrees)
CLAMP_NEAR_NODE = 250.0           # two roads sharing a node are not narrowed this close to it (TRIM_REACH_LONG): the junction handles them
CLAMP_EVERY = 3                   # metres between the points compared

# ---------------------------------------------------------------- graph

NODE_SNAP = 0.5                   # road ends closer than this are one node
SPUR_LENGTH = 8.0                 # a dead-end stub shorter than this is dropped (it would disappear inside its junction)
THROUGH_MAX_DEFLECTION = 40.0     # degrees: two arms of a junction form one through road when they deviate less than this from a straight line
JOINT_MAX_DEFLECTION = 70.0       # degrees: where just two roads meet at a sharper corner than this, the corner is kept and built as a junction
ARM_DIRECTION_REACH = 12.0        # an arm's direction is measured this far from its node

# ---------------------------------------------------------------- horizontal alignment

ALIGN_STEP = 1.0                  # spacing of the working polyline
SAMPLE_STEP = 2.0                 # spacing of the final road samples
SAMPLE_STEP_MIN = 0.75            # ... shortened in tight curves, down to this
SAMPLE_TURN = 0.14                # radians a link may turn between two samples (8 degrees)
ALIGN_ITERATIONS = 70             # re-weighting rounds that pull the smooth curve back inside the deviation bound

# ---------------------------------------------------------------- junctions

TRIM_REACH = 60.0                 # an arm is examined (and may be trimmed) this far from its node
TRIM_REACH_LONG = 250.0           # ... and this far when that is not enough (slip roads, roads leaving side by side)
TRIM_MIN = 1.0                    # every arm of a junction is cut back at least this far
CORNER_TANGENT_MIN, CORNER_TANGENT_MAX = 0.8, 8.0   # length of a corner curve along each kerb
LOOP_MIN_DRAWN = 8.0              # ... and so is a link that leaves and re-enters the same junction with less than this left
LINK_MIN_DRAWN = 1.0              # a link with less than this left between its two junctions is swallowed: both junctions become one

# ---------------------------------------------------------------- vertical alignment

PROFILE_TILE = 3200.0             # side of the squares the height solve is cut into (one sector)
PROFILE_HALO = 600.0              # how far a tile looks into its neighbours when it is solved
PROFILE_ROBUST_SCALE = 0.30       # m: a ground sample this far from the solved profile counts half (ditches, bad LiDAR returns, misplaced centrelines)
PROFILE_ROBUST_ROUNDS = 2
GRADE_FOLLOWS_GROUND = 1.2        # where the ground itself is steeper than the class allows, the road may be this much steeper than the ground
GRADE_GROUND_WINDOW = 40.0        # ... the ground grade being measured over this length
GRADE_ABSOLUTE_MAX = 0.45
TILT_FADE = 14.0                  # a junction's cross slope is unwound over this length of each arm
TILT_STIFFNESS = 20000.0           # how strongly a junction plane resists tilting across its most important arm (less for the lesser arms)
BRIDGE_DECK_MAX_ABOVE = 8.0       # surface-model samples higher than this above the ground are trees, not a deck

# ---------------------------------------------------------------- terrain

ROAD_SINK = 0.05                  # the ground under and beside a road lies this far below the road surface
SHOULDER = 1.5                    # flat ground kept beside the carriageway before the slope starts
EMBANKMENT = 6.0                  # width of the blend from the shoulder back to the natural ground
LOD_SINK = 0.30                   # same as ROAD_SINK for the 16 m terrain


@dataclass(frozen=True)
class RoadClass:
    name: str
    design_kmh: float             # speed the geometry is designed for (players go faster: the limits below already include the margin)
    max_grade: float
    crest_radius: float           # smallest vertical radius over a hump (a car leaves the ground when v^2 / R > g)
    sag_radius: float             # smallest vertical radius through a dip
    profile_wavelength: float     # ground undulations shorter than this are ironed out of the height profile
    max_deviation: float          # the smooth centreline stays within this distance of the surveyed line
    align_wavelength: float       # wiggles of the surveyed line shorter than this are smoothed away
    corner_radius: float          # kerb radius at junctions
    rank: int                     # through-road priority (higher wins)


CLASSES = {c.name: c for c in (
    #          name         kmh  grade  crest   sag  v-wave  dev  h-wave corner rank
    RoadClass("motorway",   130, 0.06, 4000.0, 2500.0, 90.0, 1.5, 140.0, 8.0,  7),
    RoadClass("ramp",        70, 0.08,  900.0, 600.0, 60.0, 1.5, 70.0,  8.0,  4),
    RoadClass("main",        90, 0.10, 1200.0, 800.0, 60.0, 1.5, 70.0,  6.0,  5),
    RoadClass("collector",   80, 0.12,  800.0, 550.0, 50.0, 1.5, 60.0,  5.0,  4),
    RoadClass("local",       60, 0.15,  450.0, 320.0, 40.0, 1.5, 45.0,  4.0,  3),
    RoadClass("street",      50, 0.16,  300.0, 220.0, 34.0, 1.2, 36.0,  3.5,  2),
    RoadClass("roundabout",  30, 0.10,  300.0, 220.0, 40.0, 0.8, 30.0,  4.0,  6),
    RoadClass("track",       30, 0.24,  120.0,  90.0, 24.0, 2.0, 36.0,  2.5,  1),
)}

# French legal limits when neither BD TOPO nor OpenStreetMap says otherwise
LIMIT_URBAN, LIMIT_RURAL, LIMIT_TRACK = 50, 80, 30
