"""Measure a build command: wall time, CPU time, peak memory (per process and in total) and disk written, with the stage lines it prints.

  uv run python profile_build.py --name small_world --watch ../world_small_prof -- uv run python build_world.py --list data/big/small_sectors.json ...

The command runs as a child; every `--every` seconds the whole process tree is sampled (resident memory of each process, the sum, CPU
times). Every line the command prints is echoed with its time since the start. The record goes to data/big/profile/<name>.json:

  wall_s, cpu_s (user + system of the whole tree), peak_rss_mb (largest single process), peak_tree_rss_mb (largest sum at one sample),
  processes (pid -> peak MB, CPU s, command), disk (MB under each --watch path at the end), lines (time, text) of the output,
  sectors (the `{...}` stats lines of build_world.py, parsed: per sector secs, cpu, rss and the time of each part)

`report.py`-style summaries: `uv run python profile_build.py --show data/big/profile/<name>.json`.
"""
import argparse
import ast
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil

OUT = Path("data/big/profile")


def tree_size(path):
    """Bytes under a path (a file or a directory), 0 when it does not exist."""
    path = Path(path)
    if path.is_file():
        return path.stat().st_size
    total = 0
    for root, _, files in os.walk(path):
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except FileNotFoundError:
                pass
    return total


class Sampler(threading.Thread):
    """Peak resident memory and CPU time of every process under `pid`, sampled every `every` seconds."""

    def __init__(self, pid, every):
        super().__init__(daemon=True)
        self.root, self.every, self.stop = psutil.Process(pid), every, threading.Event()
        self.procs = {}                         # pid -> dict(peak_mb, cpu_s, cmd)
        self.peak_tree_mb, self.samples = 0.0, 0

    def run(self):
        while not self.stop.is_set():
            try:
                members = [self.root] + self.root.children(recursive=True)
            except psutil.NoSuchProcess:
                return
            total = 0.0
            for p in members:
                try:
                    with p.oneshot():
                        rss = p.memory_info().rss / 2**20
                        cpu = p.cpu_times()
                        rec = self.procs.setdefault(p.pid, dict(peak_mb=0.0, cpu_s=0.0, cmd=" ".join(p.cmdline()[:4])[:120]))
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    continue
                rec["peak_mb"] = max(rec["peak_mb"], rss)
                rec["cpu_s"] = max(rec["cpu_s"], cpu.user + cpu.system)
                total += rss
            self.peak_tree_mb = max(self.peak_tree_mb, total)
            self.samples += 1
            self.stop.wait(self.every)


STATS = re.compile(r"^\[\d+/\d+\] (\{.*\})")


def run(args):
    OUT.mkdir(parents=True, exist_ok=True)
    before = {w: tree_size(w) for w in args.watch}
    t0 = time.time()
    usage0 = os.times()
    child = subprocess.Popen(args.command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    sampler = Sampler(child.pid, args.every)
    sampler.start()
    lines, sectors = [], []
    for line in child.stdout:
        at = time.time() - t0
        line = line.rstrip("\n")
        print(f"{at:8.1f}  {line}", flush=True)
        lines.append((round(at, 2), line))
        m = STATS.match(line)
        if m:
            try:
                sectors.append(ast.literal_eval(m.group(1)))
            except (ValueError, SyntaxError):
                pass
    code = child.wait()
    sampler.stop.set()
    sampler.join()
    usage1 = os.times()
    wall = time.time() - t0
    cpu = (usage1.children_user - usage0.children_user) + (usage1.children_system - usage0.children_system)
    record = dict(name=args.name, command=args.command, exit=code, wall_s=round(wall, 1), cpu_s=round(cpu, 1),
                  peak_rss_mb=round(max((p["peak_mb"] for p in sampler.procs.values()), default=0.0), 1),
                  peak_tree_rss_mb=round(sampler.peak_tree_mb, 1), samples=sampler.samples,
                  processes={str(pid): dict(p, peak_mb=round(p["peak_mb"], 1), cpu_s=round(p["cpu_s"], 1)) for pid, p in sampler.procs.items()},
                  disk={w: dict(before_mb=round(before[w] / 2**20, 1), after_mb=round(tree_size(w) / 2**20, 1)) for w in args.watch},
                  lines=lines, sectors=sectors)
    path = OUT / f"{args.name}.json"
    path.write_text(json.dumps(record, indent=1, default=str))
    show(record)
    print(f"-> {path}")
    return code


def show(record):
    print(f"{record['name']}: exit {record['exit']}, wall {record['wall_s']} s, cpu {record['cpu_s']} s "
          f"(x{record['cpu_s'] / max(record['wall_s'], 1e-9):.1f}), peak process {record['peak_rss_mb']:.0f} MB, peak tree {record['peak_tree_rss_mb']:.0f} MB")
    for w, d in record["disk"].items():
        print(f"  disk {w}: {d['before_mb']} -> {d['after_mb']} MB")
    built = [s for s in record["sectors"] if "parts" in s]
    if built:
        parts = {}
        for s in built:
            for k, v in s["parts"].items():
                parts[k] = parts.get(k, 0.0) + v
        total = sum(parts.values())
        print(f"  {len(built)} sectors built: secs mean {sum(s['secs'] for s in built) / len(built):.1f}, cpu mean {sum(s.get('cpu', 0) for s in built) / len(built):.1f}, "
              f"worker peak rss max {max(s.get('rss_mb', 0) for s in built):.0f} MB")
        for k, v in sorted(parts.items(), key=lambda kv: -kv[1]):
            print(f"    {k:<14} {v / len(built):7.2f} s/sector  {100 * v / total:5.1f} %")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", help="record name (data/big/profile/<name>.json)")
    ap.add_argument("--watch", action="append", default=[], help="a path whose size is recorded before and after (repeatable)")
    ap.add_argument("--every", type=float, default=0.25, help="sampling period in seconds")
    ap.add_argument("--show", help="print the summary of a record instead of running a command")
    ap.add_argument("command", nargs=argparse.REMAINDER)
    args = ap.parse_args()
    if args.show:
        show(json.loads(Path(args.show).read_text()))
        return 0
    if args.command and args.command[0] == "--":
        args.command = args.command[1:]
    if not args.name or not args.command:
        ap.error("--name and a command are required")
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
