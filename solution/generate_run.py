#!/usr/bin/env python3
"""Builds the three rover runs the task ships and the survey report each one must produce.

    generate_run.py --write ROOT           write the environment copy, the verifier sets and
                                           the goldens under the task folder ROOT
    generate_run.py --set NAME --seed S    print the report for a fresh draw (calibrate.py)

A scouting rover drives the alleys of an orchard and tags every tree it passes with its row
camera. Its motor controller reports both wheel counters fifty times a second, its RTK receiver
reports a position five times a second on GPS time, and its mission computer stamps the
controller's frames and the camera's tags on its own clock and hands everything to a radio that
the base station archives. The computer's clock was stepped once during the run, the controller
browned out once, the radio dropped out and filled the gaps in afterwards, and the receiver lost
its fixed solution under the canopy now and then.

The report (the golden) is computed by `rulebook()` from the archive lines alone, by the rules
SPEC.md states, never from the simulation's own bookkeeping. calibrate.py drives `rulebook()`
with one reading changed at a time and counts the graded fields that move.
"""
import argparse
import calendar
import json
import math
import os
import random

GPS_EPOCH_UNIX_MS = 315_964_800_000          # 1980-01-06 00:00:00 UTC
WEEK_MS = 604_800_000
LEAP_MS = 18_000                             # GPS - UTC since 2017-01-01
ERA_WEEKS = 2048                             # the 10-bit week count last rolled over 2019-04-07
DRV_MS = 20
FIX_MS = 200
FIX_LATENCY_MS = 45
RESEND_MS = 2_000
RING = 2_048
TRACK_MM = 560.0


def utc_ms(y, mo, d, h, mi, s):
    return calendar.timegm((y, mo, d, h, mi, s, 0, 0, 0)) * 1000


SETS = {
    "sample": {
        "seed": 41170314, "start": utc_ms(2026, 3, 14, 23, 50, 12) + 381, "alleys": 15, "row_mm": 64_000,
        "spacing_mm": 4_000, "alley_mm": 4_600, "e0": -31_700, "n0": -22_350, "speed": 1_000,
        "id_base": lambda r, k: r * 100 + k,
        "cfg": {"lines": 128, "gear": 4, "circ_l": 1_256_914, "circ_r": 1_254_406, "mirror": "left"},
        "step_ms": -37_412, "step_before_outage": 1, "brownout_at": 0.71, "brownout_s": 2.3,
        "outages": [(0.24, 9.0), (0.52, 44.0)], "float": [(0.12, 11.0), (0.63, 7.5), (0.88, 14.0)],
        "nofix": [(0.40, 5.0)], "loss_p": 0.003, "blur_p": 0.07, "L0": 52_113, "up0": 13_406_991,
        "phase": 7,
    },
    "hidden": {
        "seed": 51260530, "start": utc_ms(2026, 5, 30, 23, 38, 47) + 604, "alleys": 19, "row_mm": 76_000,
        "spacing_mm": 3_500, "alley_mm": 5_200, "e0": -52_900, "n0": -41_120, "speed": 1_100,
        "id_base": lambda r, k: 7_000 + r * 50 + k,
        "cfg": {"lines": 300, "gear": 4, "circ_l": 1_101_532, "circ_r": 1_103_877, "mirror": "right"},
        "step_ms": -58_907, "step_before_outage": 0, "brownout_at": 0.37, "brownout_s": 3.1,
        "outages": [(0.18, 30.0), (0.61, 40.0), (0.83, 6.0)],
        "float": [(0.09, 9.0), (0.47, 16.0), (0.74, 12.0)], "nofix": [(0.30, 7.0), (0.93, 4.0)],
        "loss_p": 0.004, "blur_p": 0.08, "L0": 3_009, "up0": 88_120_404, "phase": 13,
    },
    "edge": {
        "seed": 61260610, "start": utc_ms(2026, 6, 10, 14, 2, 5) + 77, "alleys": 4, "row_mm": 22_000,
        "spacing_mm": 3_000, "alley_mm": 4_000, "e0": -6_300, "n0": -9_700, "speed": 900,
        "id_base": lambda r, k: 300 + r * 10 + k,
        "cfg": {"lines": 256, "gear": 2, "circ_l": 1_308_220, "circ_r": 1_309_915, "mirror": "left"},
        "step_ms": 21_655, "step_before_outage": 0, "brownout_at": 0.58, "brownout_s": 1.7,
        "outages": [(0.33, 12.0)], "float": [(0.72, 5.0)], "nofix": [], "loss_p": 0.004, "blur_p": 0.2,
        "L0": 60_417, "up0": 2_210_338, "phase": 3, "standalone": True,
    },
}


# ── simulation ────────────────────────────────────────────────────────────────────────────

def plan(cfg):
    """Wheel-speed segments (duration_ms, v_left, v_right) in mm/s, forward positive, and the
    alley each straight belongs to (None for manoeuvres)."""
    v = cfg["speed"]
    segs = [(5_000, 0.0, 0.0, None)]
    pivot_w = 300.0
    pivot_ms = (math.pi / 2) * (TRACK_MM / 2) / pivot_w * 1000
    for a in range(cfg["alleys"]):
        segs.append((cfg["row_mm"] / v * 1000, v, v, a))
        if a == cfg["alleys"] - 1:
            break
        segs.append((2_000 / 500 * 1000, -500.0, -500.0, None))          # back out of the row end
        right = (a % 2 == 0)                                             # northbound turns right to face east
        segs.append((pivot_ms, pivot_w if right else -pivot_w, -pivot_w if right else pivot_w, None))
        segs.append(((cfg["alley_mm"]) / 600 * 1000, 600.0, 600.0, None))
        segs.append((pivot_ms, pivot_w if right else -pivot_w, -pivot_w if right else pivot_w, None))
        segs.append((2_000 / 500 * 1000, 500.0, 500.0, None))            # back into the row line
    segs.append((4_000, 0.0, 0.0, None))
    return segs


def simulate(cfg, seed):
    rng = random.Random(seed)
    segs = plan(cfg)
    total_ms = int(sum(s[0] for s in segs)) + 1
    brown_at = int(cfg["brownout_at"] * total_ms)
    brown_ms = int(cfg["brownout_s"] * 1000) + rng.randint(0, 400)
    # the rover stands still while the controller is down: insert a pause into the timeline
    timeline = []
    t = 0.0
    for d, vl, vr, alley in segs:
        if t <= brown_at < t + d:
            first = brown_at - t
            timeline.append((first, vl, vr, alley))
            timeline.append((brown_ms, 0.0, 0.0, "down"))
            timeline.append((d - first, vl, vr, alley))
        else:
            timeline.append((d, vl, vr, alley))
        t += d
    end_ms = int(sum(s[0] for s in timeline))
    # pose at 1 ms resolution; wheel travel per wheel accumulated in mm
    x, y, th = float(cfg["e0"]), float(cfg["n0"]), math.pi / 2           # facing north
    trav_l = trav_r = path = 0.0
    pose = []           # per ms: (x, y, trav_l, trav_r, centre path so far)
    alley_at = []       # per ms: alley index or None / "down"
    seg_i, seg_end = 0, timeline[0][0]
    for ms in range(end_ms + 1):
        while ms >= seg_end and seg_i < len(timeline) - 1:
            seg_i += 1
            seg_end += timeline[seg_i][0]
        _, vl, vr, alley = timeline[seg_i]
        pose.append((x, y, trav_l, trav_r, path))
        alley_at.append(alley)
        dl, dr = vl / 1000.0, vr / 1000.0
        ds = (dl + dr) / 2
        dth = (dr - dl) / TRACK_MM
        x += ds * math.cos(th + dth / 2)
        y += ds * math.sin(th + dth / 2)
        th += dth
        trav_l += dl
        trav_r += dr
        path += abs(ds)
        # snap heading at the end of each pivot so the alleys stay straight
        if alley is None and vl == -vr and vl != 0 and ms + 1 >= seg_end:
            th = round(th / (math.pi / 2)) * (math.pi / 2)
    return rng, pose, alley_at, end_ms, brown_at, brown_ms


def build(name, seed=None):
    """Simulate the run and return (lines, meta): the archive lines in arrival order and what
    the coverage checks need to know about the draw."""
    cfg = SETS[name]
    seed = cfg["seed"] if seed is None else seed
    rng, pose, alley_at, end_ms, brown_at, brown_ms = simulate(cfg, seed)
    start = cfg["start"]
    step = cfg["step_ms"]
    c = cfg["cfg"]
    cpr = 4 * c["lines"] * c["gear"]

    # outages, float and no-fix zones in run ms
    def spans(lst, jitter=True):
        out = []
        for frac, secs in lst:
            a = int(frac * end_ms) + (rng.randint(-3000, 3000) if jitter else 0)
            out.append((a, a + int(secs * 1000) + rng.randint(0, 900)))
        return out
    outages = spans(cfg["outages"])
    floats = spans(cfg["float"])
    nofix = spans(cfg["nofix"])
    # the clock step: just before an outage (inside its re-send window) or at a fixed point
    if cfg["step_before_outage"]:
        o = outages[cfg["step_before_outage"] - 1 + 0]
        step_at = o[0] - rng.randint(700, 1500)
    else:
        step_at = int(0.46 * end_ms) + rng.randint(-2000, 2000)

    frames = []         # (prod_ms_run, order, kind, payload_fn) payload built after clock known

    def raw(tr):        # the mission computer's stamp for true run-time tr
        u = start + tr
        return u - step if tr < step_at else u

    # controller: boots, drive frames every 20 ms, CFG at boot and every 60 s
    boot2 = brown_at + brown_ms
    boot2_phase = rng.randint(0, 19)
    ctl = []            # (tr, up, cl, cr, boot)
    tr = cfg["phase"]
    base_l = base_r = 0.0
    up0 = cfg["up0"]
    boot = 1
    while tr <= end_ms:
        if brown_at <= tr < boot2:
            tr = boot2 + boot2_phase
            boot = 2
            base_l, base_r = pose[min(tr, end_ms)][2], pose[min(tr, end_ms)][3]
            continue
        _, _, tl, trr, _ = pose[tr]
        if boot == 1:
            up = up0 + tr
            # the counters were at some arbitrary value when the run began
            nl = math.floor((tl + 7_311.0) * 1000 * cpr / c["circ_l"])
            nr = math.floor((trr + 2_094.0) * 1000 * cpr / c["circ_r"])
        else:
            up = tr - boot2
            nl = math.floor((tl - base_l) * 1000 * cpr / c["circ_l"])
            nr = math.floor((trr - base_r) * 1000 * cpr / c["circ_r"])
        if c["mirror"] == "left":
            nl = -nl
        else:
            nr = -nr
        ctl.append((tr, up, nl % 65536, nr % 65536, boot, nl, nr))
        tr += DRV_MS
    raw_counts = {}
    for tr, up, cl, cr, b, nl, nr in ctl:
        frames.append((tr, 1, "DRV", (tr, up, cl, cr, b)))
        raw_counts[tr] = (b, nl, nr)
    cfg_times = [0] + list(range(60_000, end_ms, 60_000)) + [boot2]
    for tr in cfg_times:
        if brown_at <= tr < boot2 and tr != boot2:
            continue
        frames.append((tr, 0, "CFG", (tr,)))

    # camera: a tag each time the rover passes a tree it still needs
    rows = cfg["alleys"] - 1
    per_row = int((cfg["row_mm"] - 4_000) // cfg["spacing_mm"])
    tree_n = {}
    for r in range(1, rows + 1):
        for k in range(per_row):
            tree_n[(r, k)] = cfg["n0"] + 2_000 + k * cfg["spacing_mm"] + (r % 3) * 450
    need = {}           # tree -> state: 'first' pending, 'retake', 'done'
    tags = []           # (tr, tree_id, ok)
    blur_first = set()
    for (r, k) in tree_n:
        if rng.random() < cfg["blur_p"]:
            blur_first.add((r, k))
    # plant: at least two retaken trees and one tree blurred both times
    keys = sorted(tree_n)
    for extra in (keys[len(keys) // 3], keys[len(keys) // 2]):
        blur_first.add(extra)
    both_blur = {keys[(2 * len(keys)) // 3]}
    blur_first |= both_blur
    last_alley = None
    prev_y = None
    for ms in range(len(pose)):
        a = alley_at[ms]
        y = pose[ms][1]
        if isinstance(a, int) and a == last_alley and prev_y is not None:
            for side_row in (a, a + 1):          # row a is west of alley a, row a+1 east of it
                if not (1 <= side_row <= rows):
                    continue
                for k in range(per_row):
                    tn = tree_n[(side_row, k)]
                    if (prev_y - tn) * (y - tn) <= 0 and prev_y != y and y != tn:
                        key = (side_row, k)
                        st = need.get(key, "first")
                        first_alley = side_row - 1
                        if st == "first" and a == first_alley:
                            ok = key not in blur_first
                            tags.append((ms, cfg["id_base"](side_row, k), ok, key))
                            need[key] = "done" if ok else "retake"
                        elif st == "retake" and a == first_alley + 1:
                            ok = key not in both_blur and rng.random() > 0.15
                            tags.append((ms, cfg["id_base"](side_row, k), ok, key))
                            need[key] = "done"
        last_alley = a if isinstance(a, int) else None
        prev_y = y if isinstance(a, int) else None
    # plant: one tag exactly on a drive frame's true time while moving
    drv_times = {f[0] for f in ctl}
    moving = [i for i, tg in enumerate(tags) if tg[0] + 40 < end_ms and tg[0] not in drv_times]
    i = moving[len(moving) // 5]
    tr0 = tags[i][0]
    cand = min((d for d in drv_times if abs(d - tr0) <= DRV_MS), key=lambda d: abs(d - tr0))
    tags[i] = (cand,) + tags[i][1:]
    # the canopy: every float zone opens a little before a sharp tag, so each one carries a
    # tag whose two fixes are both float
    sharp_times = [tg[0] for tg in tags if tg[2]]
    anchored = []
    for a0, b0 in floats:
        mid = min(sharp_times, key=lambda t: abs(t - a0))
        lead = rng.randint(1500, 4000)
        anchored.append((mid - lead, mid - lead + (b0 - a0)))
    floats = anchored
    # receiver: epochs every 200 ms of GPS time
    gps_start = start + LEAP_MS
    first_epoch = (gps_start // FIX_MS + 1) * FIX_MS
    # plant: a standalone fix (q 1) at the epoch before a sharp tag whose next epoch is fixed
    solo = set()
    if cfg.get("standalone"):
        tg = sharp_times[len(sharp_times) // 2]
        solo.add((start + tg + LEAP_MS) // FIX_MS * FIX_MS - gps_start)
    fixes = []
    ep = first_epoch
    while ep - gps_start <= end_ms - 100:
        tr_ep = ep - gps_start
        x, y = pose[tr_ep][0], pose[tr_ep][1]
        q, sd = 4, 6.0
        if any(a <= tr_ep < b for a, b in floats):
            q, sd = 5, 140.0
        if any(a <= tr_ep < b for a, b in nofix):
            q = 0
        if tr_ep in solo:
            q, sd = 1, 1_500.0
        if q == 0:
            e = n = 0
        else:
            e = round(x + rng.gauss(0, sd))
            n = round(y + rng.gauss(0, sd))
        g = ep - GPS_EPOCH_UNIX_MS
        week, tow = divmod(g, WEEK_MS)
        fixes.append((tr_ep, week % 1024, tow, e, n, q))
        frames.append((tr_ep + FIX_LATENCY_MS, 2, "FIX", (week % 1024, tow, e, n, q)))
        ep += FIX_MS

    for tr, tid, ok, key in tags:
        frames.append((tr, 3, "TAG", (tr, tid, ok)))
    frames.append((step_at, 4, "SYS", (step_at,)))

    # link order: the order the computer made them
    frames.sort(key=lambda f: (f[0], f[1]))
    L = cfg["L0"]
    link = []           # (L16, prod_tr, text)
    for tr, _, kind, p in frames:
        if kind == "DRV":
            text = f"DRV,{raw(p[0])},{p[1]},{p[2]},{p[3]}"
        elif kind == "CFG":
            text = f"CFG,{raw(p[0])},{c['lines']},{c['gear']},{c['circ_l']},{c['circ_r']},{c['mirror']}"
        elif kind == "FIX":
            text = f"FIX,{p[0]},{p[1]},{p[2]},{p[3]},{p[4]}"
        elif kind == "TAG":
            text = f"TAG,{raw(p[0])},{p[1]},{'ok' if p[2] else 'blur'}"
        else:
            text = f"SYS,{start + p[0]},{step}"
        link.append((L % 65536, tr, kind, text))
        L += 1

    # planted losses: the first drive frames after the brownout, and a fix beside a tag
    planted = set()
    after_boot = [i for i, f in enumerate(link) if f[2] == "DRV" and f[1] >= boot2][:3]
    planted.update(after_boot)
    fix_idx = {f[1] - FIX_LATENCY_MS: i for i, f in enumerate(link) if f[2] == "FIX"}
    good_tags = [tg for tg in tags if tg[2]]
    lost_fix_for = []
    for tg in good_tags[len(good_tags) // 4:]:
        nxt = (start + tg[0] + LEAP_MS) // FIX_MS * FIX_MS + FIX_MS - gps_start
        if (nxt in fix_idx and all(f[5] == 4 for f in fixes if abs(f[0] - nxt) <= 400)
                and not any(a - 3000 <= tg[0] <= b + 3000 for a, b in outages)):
            planted.add(fix_idx[nxt])
            lost_fix_for.append(tg[1])
            break

    # the radio: frames made while the link is up go out as they are made (a few are lost in
    # the air and never sent again); when the link comes back the ring is sent again, newest
    # first, from the start of the re-send window to the end of the outage
    def down(tr):
        return any(a <= tr < b for a, b in outages)
    arrivals = []
    held = set()
    order = 0
    lost = set()
    for i, (l16, tr, kind, text) in enumerate(link):
        if down(tr):
            continue
        if i in planted or rng.random() < cfg["loss_p"]:
            lost.add(i)
            continue
        rx = tr + rng.randint(30, 120)
        arrivals.append((rx, order, f"{l16},{text}"))
        held.add(i)
        order += 1
    for (a, b) in outages:
        # the ring at the moment the link came back: the RING most recent frames made so far
        made = [i for i, f in enumerate(link) if f[1] < b]
        ring_now = made[-RING:]
        resend = [i for i in ring_now if a - RESEND_MS <= link[i][1] < b]
        resend.sort(key=lambda i: -i)            # newest first
        t = float(b)
        for i in resend:
            t += 0.4
            if i in planted:
                continue
            rx = int(t) + rng.randint(30, 120)
            arrivals.append((rx, order, f"{link[i][0]},{link[i][3]}"))
            held.add(i)
            order += 1
    arrivals.sort()
    # the stated premise: between two held drive frames of one boot neither wheel turns half
    # the counter's range
    prev = None
    for i in sorted(held):
        if link[i][2] != "DRV":
            continue
        b, nl, nr = raw_counts[link[i][1]]
        if prev and prev[0] == b:
            assert abs(nl - prev[1]) < 32768 and abs(nr - prev[2]) < 32768, (name, seed, i)
        prev = (b, nl, nr)
    lines = [f"{start + rx} {text}" for rx, _, text in arrivals]
    meta = {"seed": seed, "step_at": step_at, "boot2": boot2, "outages": outages, "floats": floats,
            "nofix": nofix, "lost_fix_for": lost_fix_for, "both_blur": sorted(cfg["id_base"](*k) for k in both_blur),
            "end_ms": end_ms, "start": start,
            "truth": {tid: (start + tr, pose[tr][0], pose[tr][1], pose[tr][4]) for tr, tid, ok, _ in tags if ok},
            "path_mm": pose[-1][4]}
    return lines, meta


# ── the rule book ─────────────────────────────────────────────────────────────────────────

DEFAULT = {
    "dedup": "first",       # first | none
    "order": "link",        # link | arrival | raw_t
    "lwrap": True,          # unwrap the 16-bit link sequence
    "step": "link",         # link | none | stamp
    "leap": LEAP_MS,
    "week": "era",          # era | raw | first
    "quad": 4,
    "mirror": True,
    "circ": "wheel",        # wheel | mean
    "centre": "centre",     # centre | wheels | net
    "wrap16": True,
    "reset": "zero",        # zero | bridge | first_held
    "odo_at": "le",         # le | lt | nearest
    "interp": "floor",      # floor | trunc | nearest | round
    "quality": "fixed",     # fixed | float_ok
    "adjacent": True,       # both fixes on consecutive epochs
    "tag": "sharp",         # sharp | first
    "unsharp": "omit",      # omit | include
    "mm": "floor",          # floor | round
    "per_frame": False,     # round each frame's travel to whole micrometres first
}


def s16(d):
    d %= 65536
    return d - 65536 if d >= 32768 else d


def rulebook(lines, mis=None):
    R = dict(DEFAULT)
    R.update(mis or {})
    # parse: arrival order is the order of the lines
    items = []
    for ln in lines:
        rx, frame = ln.split(" ", 1)
        f = frame.split(",")
        items.append((int(rx), int(f[0]), f[1], f[2:], frame))
    if R["dedup"] == "first":
        seen, kept = set(), []
        for it in items:
            if it[4] in seen:
                continue
            seen.add(it[4])
            kept.append(it)
    else:
        kept = list(items)
    # unwrap the link sequence along arrival order, to the nearest value
    uw = []
    last = None
    for it in kept:
        l16 = it[1]
        if last is None or not R["lwrap"]:
            u = l16
        else:
            u = last + (l16 - last + 32768) % 65536 - 32768
        last = u
        uw.append((u,) + it)
    distinct = {x[5] for x in uw}
    lo, hi = min(x[0] for x in uw), max(x[0] for x in uw)
    lost = (hi - lo + 1) - (len(distinct) if R["dedup"] == "first" else len(uw))

    if R["order"] in ("link", "raw_t"):
        seq = sorted(uw, key=lambda x: x[0])
    else:
        seq = list(uw)
    sys_frames = [x for x in seq if x[3] == "SYS"]
    s = sys_frames[0]
    step = int(s[4][1])
    s_t = int(s[4][0])
    s_link = s[0]

    def true_t(x):
        t = int(x[4][0])
        mode = R["step"]
        if mode == "none":
            return t
        if mode == "stamp":
            return t + step if t < s_t else t
        return t + step if x[0] < s_link else t

    cfgs = [x for x in seq if x[3] == "CFG"]
    cf = cfgs[0][4]
    lines_, gear, circ_l, circ_r, mirror = int(cf[1]), int(cf[2]), int(cf[3]), int(cf[4]), cf[5]
    cpr = R["quad"] * lines_ * gear
    if R["circ"] == "mean":
        circ_l = circ_r = (circ_l + circ_r) // 2
    sl = -1 if (mirror == "left" and R["mirror"]) else 1
    sr = -1 if (mirror == "right" and R["mirror"]) else 1

    drv = [x for x in seq if x[3] == "DRV"]
    if R["order"] == "raw_t":
        drv.sort(key=lambda x: int(x[4][0]))
    acc = 0
    net = 0
    marks = []          # (true_t, acc)
    prev = None
    first_boot = True
    for x in drv:
        f = x[4]
        up, cl, cr = int(f[1]), int(f[2]), int(f[3])
        if prev is None:
            pl, pr = cl, cr
        elif up < prev[0] and R["reset"] != "bridge":
            if R["reset"] == "zero":
                pl, pr = 0, 0
            else:
                pl, pr = cl, cr
            if R["centre"] == "net":
                acc += abs(net)
                net = 0
            first_boot = False
        else:
            pl, pr = prev[1], prev[2]
        if R["wrap16"]:
            dl, dr = s16(cl - pl), s16(cr - pr)
        else:
            dl, dr = cl - pl, cr - pr
        wl, wr = sl * dl * circ_l, sr * dr * circ_r
        if R["per_frame"]:
            acc += abs(wl + wr) // (2 * cpr) * (2 * cpr)
        elif R["centre"] == "centre":
            acc += abs(wl + wr)
        elif R["centre"] == "wheels":
            acc += abs(wl) + abs(wr)
        else:
            net += wl + wr
        marks.append((true_t(x), acc + (abs(net) if R["centre"] == "net" else 0)))
        prev = (up, cl, cr)
    if R["centre"] == "net":
        acc += abs(net)
    del first_boot
    div = 2 * cpr * 1000

    def mm(v):
        if R["mm"] == "floor":
            return v // div
        return (2 * v + div) // (2 * div)

    distance = mm(acc)

    # receiver fixes on the UTC clock
    fixes = {}
    first_week = None
    for x in seq:
        if x[3] != "FIX":
            continue
        f = x[4]
        wk, tow, e, n, q = (int(v) for v in f)
        if R["week"] == "era":
            full = ERA_WEEKS + wk
        elif R["week"] == "raw":
            full = wk
        else:
            if first_week is None:
                first_week = ERA_WEEKS + wk
            full = first_week
        t = GPS_EPOCH_UNIX_MS + full * WEEK_MS + tow - R["leap"]
        fixes[t] = (e, n, q)

    def position(T):
        good = (4,) if R["quality"] == "fixed" else (4, 5)
        if R["adjacent"]:
            t0 = T - (T % FIX_MS)
            t1 = t0 + FIX_MS
            a, b = fixes.get(t0), fixes.get(t1)
            if not a or not b or a[2] not in good or b[2] not in good:
                return None, None
        else:
            ks = [k for k in fixes if fixes[k][2] in good]
            before = [k for k in ks if k <= T]
            after = [k for k in ks if k > T]
            if not before or not after:
                return None, None
            t0, t1 = max(before), min(after)
            if t1 - t0 > 5_000:
                return None, None
            a, b = fixes[t0], fixes[t1]
        out = []
        for i in (0, 1):
            num = a[i] * (t1 - T) + b[i] * (T - t0)
            den = t1 - t0
            if R["interp"] == "floor":
                out.append(num // den)
            elif R["interp"] == "trunc":
                out.append(-((-num) // den) if num < 0 else num // den)
            elif R["interp"] == "round":
                out.append((2 * num + den) // (2 * den))
            else:
                out.append(a[i] if (T - t0) * 2 < den else b[i])
        return out[0], out[1]

    mark_t = [m[0] for m in marks]

    def odometer(T):
        import bisect
        if R["odo_at"] == "le":
            i = bisect.bisect_right(mark_t, T) - 1
        elif R["odo_at"] == "lt":
            i = bisect.bisect_left(mark_t, T) - 1
        else:
            j = bisect.bisect_left(mark_t, T)
            cands = [k for k in (j - 1, j) if 0 <= k < len(mark_t)]
            i = min(cands, key=lambda k: (abs(mark_t[k] - T), k))
        return mm(marks[i][1]) if i >= 0 else 0

    tags = {}
    for x in seq:
        if x[3] != "TAG":
            continue
        tags.setdefault(int(x[4][1]), []).append((true_t(x), x[4][2]))
    trees = []
    for tid in sorted(tags):
        att = tags[tid]
        if R["tag"] == "sharp":
            pick = [a for a in att if a[1] == "ok"]
            if not pick:
                if R["unsharp"] == "omit":
                    continue
                pick = att
            T = pick[0][0]
        else:
            T = att[0][0]
        e, n = position(T)
        trees.append({"id": tid, "utc_ms": T, "odometer_mm": odometer(T), "east_mm": e, "north_mm": n})
    return {"distance_mm": distance, "lost_frames": lost, "trees": trees}


# ── writing ───────────────────────────────────────────────────────────────────────────────

def write_set(root, name, lines, rep):
    d = os.path.join(root, name)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "run.txt"), "w") as fh:
        fh.write("\n".join(lines) + "\n")
    return rep


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write")
    ap.add_argument("--set")
    ap.add_argument("--seed", type=int)
    a = ap.parse_args()
    if a.set:
        lines, _meta = build(a.set, a.seed)
        print(json.dumps(rulebook(lines)))
        return
    root = a.write
    for name in SETS:
        lines, _meta = build(name)
        rep = rulebook(lines)
        dest = os.path.join(root, "tests", "data")
        write_set(dest, name, lines, rep)
        os.makedirs(os.path.join(root, "tests", "golden"), exist_ok=True)
        with open(os.path.join(root, "tests", "golden", name + ".json"), "w") as fh:
            json.dump(rep, fh, sort_keys=True)
            fh.write("\n")
        if name == "sample":
            write_set(os.path.join(root, "environment", "telemetry"), "sample", lines, rep)
        print(name, len(lines), "lines", rep["distance_mm"], rep["lost_frames"], len(rep["trees"]))


if __name__ == "__main__":
    main()
