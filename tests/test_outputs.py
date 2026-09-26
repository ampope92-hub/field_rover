"""Grades the survey reports. test.sh rebuilt the agent's OCaml three times and ran it against the
shipped run, a held-back run and a short run reached by host name, keeping stdout under /opt/run.
Nothing here recomputes a report. The oracle is cross-checked by two independent implementations
of SPEC.md: the rule book in solution/generate_run.py wrote golden/, the OCaml reference wrote
golden_ref/, and they must agree. The coverage checks read the runs to show each case is there."""
import json
import os
import re

import pytest

RUN = "/opt/run"
TRUTH = "/opt/truth"
SETS = ["sample", "hidden", "edge"]
TREE_KEYS = {"id", "utc_ms", "odometer_mm", "east_mm", "north_mm"}


def read_bytes(path):
    """Opens without following a symlink, so a link planted at the path is refused."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as fh:
        return fh.read()


def report(name):
    assert read_bytes(os.path.join(RUN, f"report_{name}.status")) != b"killed\n", f"the {name} run was killed at 120 s"
    text = read_bytes(os.path.join(RUN, f"report_{name}.json")).decode("utf-8")
    line = text.removesuffix("\n")
    assert line and "\n" not in line, f"the {name} report is not exactly one line"
    return json.loads(line)


def golden(name):
    return json.loads(read_bytes(os.path.join(TRUTH, "golden", name + ".json")))


def is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


# ── the reports ───────────────────────────────────────────────────────────────────────────

def test_every_build_succeeded():
    """All three compiles of the collected src/main.ml produced a regular binary."""
    status = read_bytes(os.path.join(RUN, "build_check.txt")).decode().strip()
    assert status == "ok", "a compile of /app/rover/src/main.ml failed or produced no binary"


@pytest.mark.parametrize("name", SETS)
def test_report_shape(name):
    """One JSON line: distance_mm, lost_frames, trees in ascending id order, integers or nulls."""
    rep = report(name)
    assert isinstance(rep, dict) and set(rep) == {"distance_mm", "lost_frames", "trees"}, sorted(rep)
    assert is_int(rep["distance_mm"]) and is_int(rep["lost_frames"])
    assert isinstance(rep["trees"], list)
    ids = []
    for row in rep["trees"]:
        assert isinstance(row, dict) and set(row) == TREE_KEYS, row
        assert all(is_int(row[k]) for k in ("id", "utc_ms", "odometer_mm")), row
        both_null = row["east_mm"] is None and row["north_mm"] is None
        assert both_null or (is_int(row["east_mm"]) and is_int(row["north_mm"])), row
        ids.append(row["id"])
    assert ids == sorted(set(ids)), "trees is not in ascending id order with one object per tree"


def test_the_two_references_agree():
    """The rule book's golden and the OCaml reference's report are equal on every graded run."""
    for name in SETS:
        assert golden(name) == json.loads(read_bytes(os.path.join(TRUTH, "golden_ref", name + ".json"))), name


def _totals(name):
    rep, want = report(name), golden(name)
    assert rep["distance_mm"] == want["distance_mm"], f"{name}: distance_mm {rep['distance_mm']}, the rule book gives {want['distance_mm']}"
    assert rep["lost_frames"] == want["lost_frames"], f"{name}: lost_frames {rep['lost_frames']}, the rule book gives {want['lost_frames']}"


def _trees(name):
    got = {r["id"]: r for r in report(name)["trees"]}
    want = {r["id"]: r for r in golden(name)["trees"]}
    missing = sorted(set(want) - set(got))
    extra = sorted(set(got) - set(want))
    assert not missing and not extra, f"{name}: trees missing {missing[:8]}, not expected {extra[:8]}"
    wrong = [k for k in sorted(want) if got[k] != want[k]]
    assert not wrong, (f"{name}: {len(wrong)} of {len(want)} trees differ from the rule book, first: "
                       + "; ".join(f"{got[k]} (expected {want[k]})" for k in wrong[:3]))


def test_sample_distance_and_lost():
    """On the shipped run, served on an address, port, batch size and BUSY schedule the agent's
    environment never used, distance_mm and lost_frames equal the rule book's."""
    _totals("sample")


def test_sample_trees():
    """On the shipped run every tree row (tag time, odometer and position) equals the rule book's."""
    _trees("sample")


def test_hidden_distance_and_lost():
    """On a longer run the agent never saw, with another calibration, mirrored side, clock step,
    dates and tree numbering, distance_mm and lost_frames equal the rule book's."""
    _totals("hidden")


def test_hidden_trees():
    """On the held-back run every tree row equals the rule book's."""
    _trees("hidden")


def test_edge_distance_and_lost():
    """On the short run, reached by host name, with the clock stepped forward, distance_mm and
    lost_frames equal the rule book's."""
    _totals("edge")


def test_edge_trees():
    """On the short run every tree row equals the rule book's."""
    _trees("edge")


# ── the protocol ──────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", SETS)
def test_each_run_read_the_whole_archive(name):
    """The run's archive logged batches covering every frame it holds, each asked for with `start`
    or a cursor it had handed out (they are random), met a BUSY, and refused no request."""
    log = read_bytes(os.path.join(RUN, f"archive_{name}.log")).decode("utf-8", "replace")
    total = int(re.search(r"serving (\d+) frames", log).group(1))
    handed, served, busy, finals = {"start"}, 0, 0, 0
    for line in log.splitlines():
        m = re.search(r" BATCH cursor=(\S+) frames=(\d+) next=(\S+)", line)
        if m:
            assert m.group(1) in handed, f"cursor {m.group(1)!r} was never handed out by this archive"
            handed.add(m.group(3))
            served += int(m.group(2))
            finals += m.group(3) == "done"
            continue
        busy += " BUSY " in line
    assert served >= total and finals >= 1, f"{served} of {total} frames were fetched"
    assert busy, "the run never met a BUSY answer"
    assert " refused " not in log, f"the archive refused {log.count(' refused ')} requests"


def test_a_graded_run_was_pointed_at_its_archive_by_name():
    """The short run was given a host name, and its archive, on the address it resolves to, served."""
    host = read_bytes(os.path.join(RUN, "named_host.txt")).decode().strip()
    assert host and not re.match(r"^\d+\.\d+\.\d+\.\d+$", host), f"the named run was given {host!r}"
    log = read_bytes(os.path.join(RUN, "archive_edge.log")).decode("utf-8", "replace")
    assert "127.0.0.4:" in log.splitlines()[0]
    assert " BATCH " in log, "the named run never reached its archive"


# ── coverage: counted from the runs rather than assumed ───────────────────────────────────

def frames(name):
    """[(rx, L16, type, fields, text)] in arrival order."""
    out = []
    for ln in read_bytes(os.path.join(TRUTH, name, "run.txt")).decode().splitlines():
        rx, text = ln.split(" ", 1)
        f = text.split(",")
        out.append((int(rx), int(f[0]), f[1], f[2:], text))
    return out


def cases(name):
    items = frames(name)
    texts = [it[4] for it in items]
    copies = len(texts) - len(set(texts))
    firsts, seen = [], set()
    for it in items:
        if it[4] not in seen:
            seen.add(it[4])
            firsts.append(it)
    # the frame number put back in order, each to the value nearest the one before
    last, order = None, []
    for it in firsts:
        u = it[1] if last is None else last + (it[1] - last + 32768) % 65536 - 32768
        last = u
        order.append((u, it))
    wraps_link = sum(1 for i in range(1, len(order)) if order[i][1][1] < order[i - 1][1][1] - 30000)
    newest_first = sum(1 for i in range(1, len(order)) if order[i][0] == order[i - 1][0] - 1)
    order.sort(key=lambda x: x[0])
    sys_ = [x for x in order if x[1][2] == "SYS"]
    s_u, s_t, step = sys_[0][0], int(sys_[0][1][3][0]), int(sys_[0][1][3][1])
    stamped_before = [x for x in order if x[1][2] in ("DRV", "TAG") and x[0] < s_u]
    late_stamps = sum(1 for x in stamped_before if int(x[1][3][0]) >= s_t)
    drv = [x[1][3] for x in order if x[1][2] == "DRV"]
    resets = [i for i in range(1, len(drv)) if int(drv[i][1]) < int(drv[i - 1][1])]
    counter_wraps = sum(1 for i in range(1, len(drv)) for k in (2, 3) if abs(int(drv[i][k]) - int(drv[i - 1][k])) > 32768)
    fix = [x[1][3] for x in order if x[1][2] == "FIX"]
    tow_restarts = sum(1 for i in range(1, len(fix)) if int(fix[i][1]) < int(fix[i - 1][1]))
    q = {int(f[4]) for f in fix}
    fix_q = {315_964_800_000 + (2048 + int(f[0])) * 604_800_000 + int(f[1]) - 18_000: int(f[4]) for f in fix}
    sharp = [int(x[1][3][0]) + (step if x[0] < s_u else 0) for x in order if x[1][2] == "TAG" and x[1][3][2] == "ok"]
    tags = {}
    for x in order:
        if x[1][2] == "TAG":
            tags.setdefault(int(x[1][3][1]), []).append(x[1][3][2])
    drv_stamps = {(x[0] < s_u, int(x[1][3][0])) for x in order if x[1][2] == "DRV"}
    tag_on_drive = sum(1 for x in order if x[1][2] == "TAG" and (x[0] < s_u, int(x[1][3][0])) in drv_stamps)
    return {
        "copies": copies, "wraps_link": wraps_link, "newest_first": newest_first, "step": step,
        "late_stamps": late_stamps, "sys": len(sys_), "resets": resets, "first_after_reset": drv[resets[0]] if resets else None,
        "counter_wraps": counter_wraps, "tow_restarts": tow_restarts, "q": q,
        "retaken": sum(1 for v in tags.values() if v[0] == "blur" and "ok" in v),
        "never_sharp": sum(1 for v in tags.values() if v.count("blur") == 2 and "ok" not in v), "tag_on_drive": tag_on_drive,
        "mirrored": {x[1][3][5] for x in order if x[1][2] == "CFG"}, "cfgs": len({tuple(x[1][3][1:]) for x in order if x[1][2] == "CFG"}),
        "quiet_reset": all(abs((int(drv[i][k]) - int(drv[i - 1][k]) + 32768) % 65536 - 32768) < 100 for i in resets[:1] for k in (2, 3)),
        "most_tags": max(len(v) for v in tags.values()),
        "bounds": {(fix_q.get(T - T % 200), fix_q.get(T - T % 200 + 200), T % 200 == 0 and fix_q.get(T - 200)) for T in sharp},
    }


def golden_cases(name):
    g = golden(name)
    pos = [t for t in g["trees"] if t["east_mm"] is not None]
    return {
        "negative_both": sum(1 for t in pos if t["east_mm"] < 0 and t["north_mm"] < 0),
        "floor_matters": sum(1 for t in pos if t["east_mm"] < 0 or t["north_mm"] < 0),
        "null": sum(1 for t in g["trees"] if t["east_mm"] is None),
        "lost": g["lost_frames"],
    }


def _common(c, g):
    assert c["sys"] == 1 and c["copies"] >= 20 and c["newest_first"] >= 50 and c["wraps_link"] >= 1
    assert len(c["resets"]) == 1 and (int(c["first_after_reset"][2]) != 0 or int(c["first_after_reset"][3]) != 0)
    assert c["counter_wraps"] >= 3 and {4, 5} <= c["q"] and c["cfgs"] == 1
    assert c["retaken"] >= 2 and c["never_sharp"] >= 1 and c["tag_on_drive"] >= 1 and c["most_tags"] == 2
    assert (4, None, False) in c["bounds"] and any(5 in b[:2] for b in c["bounds"])
    assert g["negative_both"] >= 1 and g["null"] >= 2 and g["lost"] > 0


def test_sample_carries_every_case():
    """The shipped run carries each case SPEC.md's facts turn on (see _common), a backward clock
    step with stamps before it that read later than it, and the GPS week turning over."""
    c, g = cases("sample"), golden_cases("sample")
    _common(c, g)
    assert c["step"] < 0 and c["late_stamps"] >= 100 and c["tow_restarts"] == 1 and 0 in c["q"]
    assert c["mirrored"] == {"left"}


def test_hidden_carries_every_case():
    """The held-back run carries the same cases, the right motor mirrored and a larger step."""
    c, g = cases("hidden"), golden_cases("hidden")
    _common(c, g)
    assert c["step"] < 0 and c["late_stamps"] >= 100 and c["tow_restarts"] == 1 and 0 in c["q"]
    assert c["mirrored"] == {"right"} and (None, 4, False) in c["bounds"]


def test_edge_carries_its_cases():
    """The short run steps the clock forward, stays inside one GPS week and still carries the
    copies, the wraps and the tag cases, a brownout only the fall in up marks, and sharp tags with
    fixes q 1/q 4, q 4/q 1 and q 4/q 0 around them and one on a q 4 epoch after a q 1 fix."""
    c, g = cases("edge"), golden_cases("edge")
    _common(c, g)
    assert c["step"] > 0 and c["late_stamps"] == 0 and c["tow_restarts"] == 0
    assert c["quiet_reset"] and {(1, 4, False), (4, 1, False), (4, 0, False), (4, 4, 1)} <= c["bounds"]
