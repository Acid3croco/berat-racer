"""What a build may use of the machine: half of it (the user's rule), so the computer stays usable while a map is generated.

  JOBS        worker processes: half the cores (5 on the 10-core M1 Max)
  MEMORY_GB   resident memory of the whole process tree: half the RAM (16 GB of 32); a worker must stay under MEMORY_GB / JOBS

Importing this module also keeps the maths libraries single-threaded in every process (a worker is one core).
"""
import os

for _name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_name, "1")

CORES = os.cpu_count() or 2
JOBS = max(1, CORES // 2)
MEMORY_GB = (os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")) / 2**30 / 2


def jobs(asked=None):
    """The worker count to use: what was asked, never more than JOBS."""
    return JOBS if not asked else max(1, min(int(asked), JOBS))
