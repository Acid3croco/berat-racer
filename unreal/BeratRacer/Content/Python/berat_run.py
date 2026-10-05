"""Run the importer from the command line (-ExecutePythonScript), logging any failure to Saved/berat_import_error.txt."""
import importlib
import os
import traceback

import unreal

import berat_import

importlib.reload(berat_import)
err = os.path.join(unreal.Paths.project_saved_dir(), "berat_import_error.txt")
try:
    if os.path.exists(err):
        os.remove(err)
    berat_import.run()
except Exception:
    with open(err, "w") as f:
        f.write(traceback.format_exc())
    unreal.log_error(traceback.format_exc())
