# Field Rover Survey Reconstruction

An orchard survey rover (SR-4) has finished a night run, and its base station archived every telemetry frame it heard over the radio. The agent gets a stub at `/app/rover/src/main.ml` and a spec (`/app/rover/SPEC.md`) covering the archive's TCP protocol, the frame layouts and facts about the rover's hardware on this run. It must write an OCaml tool that pages the whole run out of the archive (located by `TELEMETRY_HOST` / `TELEMETRY_PORT`) and prints one line of JSON: total distance driven, how many radio frames never reached the base station, and for each tree with a sharp camera tag, the UTC time it was tagged, the odometer reading at that moment and the rover's grid position. The tool must build from `main.ml` alone with the exact `ocamlopt` line given in the spec.

## What makes it hard

The spec lists each hardware quirk as a plain fact and never says how to correct for it:

- The mission computer's clock was stepped once mid-run (backwards on the shipped run).
- The drive controller counts quadrature edges on 16-bit counters, has one motor mounted mirrored, and browned out once.
- The RTK receiver keeps GPS time with a 10-bit week number and drops to float under the canopy.
- The camera re-tags a blurred tree from the next alley.
- The radio numbers frames on a 16-bit counter and, after a dropout, re-sends what it buffered newest first.

None of these is hard alone. The difficulty is that they interact. The clock step decides where each drive frame sits on the true timeline, and so the odometer at each tag. The newest-first refill decides frame order and where a boot appears to restart. Frames lost after the brownout decide where that boot's travel starts. Leap seconds, the week count and the step together decide which two GNSS fixes bracket a tag.

Each plausible misreading was measured against the reference rule book on the shipped run (830 graded fields). Some examples: GPS time taken as UTC gets 406 fields wrong, the week number read as a full week 396, OCaml's truncating division on interpolated positions 164, walking drive frames in stamp order 161, ignoring the mirrored motor 208, and measuring the post-brownout boot from its first archived frame 50. `solution/calibrate.py` checks that each of thirty misreadings changes at least one graded field on eight fresh seeds of every run that contains its case. A report can track the receiver closely, pass every check the agent can run locally, and still be wrong on the run it cannot see.

## How it's graded

Grading runs in a separate verifier container that collects only `/app/rover/src/`. For each of three runs, the verifier deletes everything under `src` except `main.ml`, compiles it with the spec's exact command as an unprivileged user, and runs the binary against an archive service it starts itself, using a batch size the agent never saw:

- **Shipped run**, served on a different address and port.
- **Held-back run** (92,183 frames) with a different calibration, the other motor mirrored, a longer backward step, another date and different tree numbering.
- **Short run** with a forward step, reached by a host name rather than an IP.

`distance_mm`, `lost_frames` and every tree row must exactly match the expected report. The verifier also checks the archive logs to confirm that every batch was fetched in one cursor chain from start to `done`, with no refused requests. Coverage checks confirm that each run actually contains the cases it is meant to exercise.

The expected reports come from a Python rule book written before the OCaml solution. They are cross-checked against the OCaml reference's output, which was stored at generation time. Anti-cheat measures: the goldens and the archive data are readable only by root; the agent's binary is statically linked and runs under a fresh per-run UID; every other program on the box has its execute bits removed during the run; interpreters are closed during the compile; and the verifier refuses to grade if root's PATH contains anything non-root-writable. Twelve single-misreading variants of the reference, plus a frozen copy of the shipped report, all scored 0 through this verifier.

## Repository layout

- `instruction.md`: the task prompt given to the agent.
- `task.toml`: task metadata, difficulty, solution and verification notes, and resource limits.
- `environment/`: the agent's container (`Dockerfile`), the telemetry archive service (`Dockerfile.telemetry`, `telemetry/archive.py`, sample run), wired together by `docker-compose.yaml`; `rover/` holds `SPEC.md`, the stub `src/main.ml` and build/run scripts.
- `solution/`: `solve.sh`, the reference OCaml tool (`src/main.ml`), the run simulator and rule book (`generate_run.py`), and the misreading calibration script (`calibrate.py`).
- `tests/`: verifier image and entrypoint (`Dockerfile`, `test.sh`), pytest checks (`test_outputs.py`), the archive server, the three graded runs (`data/`) and their expected reports (`golden/`, `golden_ref/`).

## Running it

Requires Docker and Harbor (`uv tool install harbor` or `pip install harbor`). From the repo root:

```
harbor run -p . -a oracle
```

This runs the reference solution through the verifier and should score reward 1. To try an agent instead:

```
harbor run -p . -a <agent> -m <model>
```

For example, `-a claude-code`.

---

Task design, data generators, reference solution and verifier by Audrey Pope.
