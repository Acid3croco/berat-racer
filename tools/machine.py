"""What a build may use of the machine: half of it by default (the user's rule), so the computer stays usable while a map is
generated. BERAT_MACHINE_SHARE=0.9 raises the share when the user allows it (e.g. while away).

  JOBS        worker processes: that share of the cores (5 of 10 on the M1 Max by default)
  MEMORY_GB   resident memory of the whole process tree: that share of the RAM (16 GB of 32 by default)

Importing this module also keeps the maths libraries single-threaded in every process (a worker is one core).
"""
import os

for _name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_name, "1")

SHARE = min(float(os.environ.get("BERAT_MACHINE_SHARE", "0.5")), 0.9)
CORES = os.cpu_count() or 2
JOBS = max(1, int(CORES * SHARE))
MEMORY_GB = (os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")) / 2**30 * SHARE


def jobs(asked=None):
    """The worker count to use: what was asked, never more than JOBS."""
    return JOBS if not asked else max(1, min(int(asked), JOBS))
