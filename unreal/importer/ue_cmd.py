"""Run Python in the open Unreal Editor through the file-drop channel of Content/Python/init_unreal.py and print its output.

    python ue_cmd.py "print(unreal.SystemLibrary.get_engine_version())"
    python ue_cmd.py --file script.py [--timeout 600]
"""

import argparse
import os
import sys
import time
import uuid

DIR = r"C:\Users\jack\projects\berat-racer\unreal\BeratRacer\Saved\berat_cmd"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("code", nargs="?")
    ap.add_argument("--file")
    ap.add_argument("--timeout", type=float, default=600)
    a = ap.parse_args()
    code = open(a.file, encoding="utf-8").read() if a.file else a.code
    os.makedirs(DIR, exist_ok=True)
    name = time.strftime("%H%M%S_") + uuid.uuid4().hex[:6]
    tmp = os.path.join(DIR, name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(code)
    os.replace(tmp, os.path.join(DIR, name + ".py"))
    out = os.path.join(DIR, name + ".out")
    t0 = time.time()
    while not os.path.exists(out):
        if time.time() - t0 > a.timeout:
            sys.exit(f"no answer after {a.timeout:.0f} s (is the editor open and idle?)")
        time.sleep(0.25)
    time.sleep(0.1)
    text = open(out, encoding="utf-8").read()
    print(text.rstrip())
    sys.exit(0 if text.rstrip().endswith("[ok]") else 1)


if __name__ == "__main__":
    main()
