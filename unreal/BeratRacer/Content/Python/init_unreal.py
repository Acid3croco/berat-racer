"""Runs at every editor start (Unreal executes Content/Python/init_unreal.py).

A file-drop command channel for tools outside the editor: every *.py dropped into Saved/berat_cmd/ is executed on the game
thread (one per tick), its prints and any traceback written to <name>.out, then the .py is renamed .done.
unreal/importer/ue_cmd.py is the client.
"""

import contextlib
import io
import os
import traceback

import unreal

_DIR = os.path.join(unreal.Paths.project_saved_dir(), "berat_cmd")
os.makedirs(_DIR, exist_ok=True)
_clock = [0.0]


def _tick(dt):
    _clock[0] += dt
    if _clock[0] < 0.5:
        return
    _clock[0] = 0.0
    try:
        names = sorted(n for n in os.listdir(_DIR) if n.endswith(".py"))
    except OSError:
        return
    if not names:
        return
    path = os.path.join(_DIR, names[0])
    base = path[:-3]
    out = io.StringIO()
    try:
        with open(path, encoding="utf-8") as f:
            code = f.read()
        os.replace(path, base + ".running")
        with contextlib.redirect_stdout(out):
            exec(compile(code, path, "exec"), {"__name__": "__berat_cmd__", "unreal": unreal})
        status = "ok"
    except Exception:
        out.write(traceback.format_exc())
        status = "error"
    with open(base + ".out", "w", encoding="utf-8") as f:
        f.write(out.getvalue())
        f.write(f"\n[{status}]\n")
    with contextlib.suppress(OSError):
        os.replace(base + ".running", base + ".done")


unreal.register_slate_post_tick_callback(_tick)
unreal.log(f"[berat] command channel watching {_DIR}")
