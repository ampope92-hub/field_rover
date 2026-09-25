#!/usr/bin/env python3
"""Measures how far each plausible misreading of SPEC.md lands from the rule book.

    calibrate.py [--seeds 8] [--jobs 4]

For every set (sample, hidden, edge) the shipped draw and N fresh seeds are generated with
generate_run.build(); rulebook() is run once as written and once per misreading with exactly one
reading switched, and the graded fields that differ are counted: distance_mm, lost_frames, and
per tree its presence plus utc_ms, odometer_mm, east_mm and north_mm. A misreading separates when
it moves at least one field on every draw that carries its case.
"""
import argparse
import os
import sys
from multiprocessing import Pool

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import generate_run as g

MIS = {
    "step_ignored": {"step": "none"},
    "step_by_stamp": {"step": "stamp"},
    "drive_by_stamp": {"order": "raw_t"},
    "arrival_order": {"order": "arrival"},
    "copies_kept": {"dedup": "none"},
    "copies_kept_arrival": {"dedup": "none", "order": "arrival"},
    "link_unwrapped": {"lwrap": False},
    "no_leap": {"leap": 0},
    "leap_wrong_way": {"leap": -g.LEAP_MS},
    "week_as_full": {"week": "raw"},
    "week_fixed": {"week": "first"},
    "no_quadrature": {"quad": 1},
    "mirror_ignored": {"mirror": False},
    "mean_circumference": {"circ": "mean"},
    "wheel_travel": {"centre": "wheels"},
    "net_travel": {"centre": "net"},
    "counter_unwrapped": {"wrap16": False},
    "reset_bridged": {"reset": "bridge"},
    "reset_from_first_held": {"reset": "first_held"},
    "odometer_before": {"odo_at": "lt"},
    "odometer_nearest": {"odo_at": "nearest"},
    "interp_truncated": {"interp": "trunc"},
    "interp_rounded": {"interp": "round"},
    "nearest_fix": {"interp": "nearest"},
    "float_accepted": {"quality": "float_ok"},
    "gap_bridged": {"adjacent": False},
    "first_attempt": {"tag": "first"},
    "unsharp_kept": {"unsharp": "include"},
    "mm_rounded": {"mm": "round"},
    "frame_micrometres": {"per_frame": True},
}


def diff(a, b):
    n = int(a["distance_mm"] != b["distance_mm"]) + int(a["lost_frames"] != b["lost_frames"])
    ta = {t["id"]: t for t in a["trees"]}
    tb = {t["id"]: t for t in b["trees"]}
    for k in set(ta) | set(tb):
        if k not in ta or k not in tb:
            n += 1
            continue
        n += sum(ta[k][f] != tb[k][f] for f in ("utc_ms", "odometer_mm", "east_mm", "north_mm"))
    return n


def fields(rep):
    return 2 + 4 * len(rep["trees"])


def one(job):
    name, seed = job
    lines, _ = g.build(name, seed)
    base = g.rulebook(lines)
    out = {"_fields": fields(base)}
    for k, m in MIS.items():
        try:
            out[k] = diff(base, g.rulebook(lines, m))
        except (KeyError, IndexError, ValueError, ZeroDivisionError) as e:  # no report at all moves everything
            out[k] = fields(base)
            out[k + "_err"] = repr(e)[:80]
    return name, seed, out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=8)
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--sets", default="sample,hidden,edge")
    a = ap.parse_args()
    sets = a.sets.split(",")
    jobs = []
    for name in sets:
        jobs.append((name, None))
        base = g.SETS[name]["seed"]
        jobs += [(name, base + 1000 + i) for i in range(a.seeds)]
    with Pool(a.jobs) as p:
        res = p.map(one, jobs)
    table = {}
    for name, seed, out in res:
        table.setdefault(name, []).append((seed, out))
    print(f"{'misreading':24}" + "".join(f"{n:>10}{'min':>6}" for n in sets))
    print(f"{'(graded fields)':24}" + "".join(f"{table[n][0][1]['_fields']:>10}{min(o['_fields'] for _, o in table[n]):>6}" for n in sets))
    for k in MIS:
        row = f"{k:24}"
        for n in sets:
            shipped = table[n][0][1][k]
            lo = min(o[k] for _, o in table[n][1:]) if len(table[n]) > 1 else shipped
            row += f"{shipped:>10}{lo:>6}"
        print(row)
    errs = {(n, s, k): o[k] for n in table for s, o in table[n] for k in o if k.endswith("_err")}
    for key, v in list(errs.items())[:10]:
        print("error", key, v)


if __name__ == "__main__":
    main()
