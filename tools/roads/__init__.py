"""Road pipeline: surveyed centrelines -> a drivable, overlap-free road surface.

Stages (each a module, run in this order by `build.build`):

  source     BD TOPO sections -> edges with normalised attributes (+ OpenStreetMap knowledge, + overrides.toml)
  graph      edges -> nodes, through roads ("strokes"), links between junctions
  alignment  horizontal: roundabouts become circles, every stroke becomes a smooth curve that stays close to the surveyed line
  junction   where arms meet: how far each arm is cut back, the junction outline with rounded corners
  profile    vertical: one height profile for the whole network, limited in grade and curvature; every junction is one plane
  surface    the result: road pieces with their two edges, junction meshes, footprints for the terrain

Run `uv run python -m roads --help` from tools/.
"""
import machine                    # noqa: F401  half the machine, single-threaded maths, before numpy loads (machine.py)
