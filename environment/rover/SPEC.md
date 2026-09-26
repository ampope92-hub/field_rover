# SR-4 run archive and the tree survey

Field notes for the survey tool, kept by the rover team. The first part is how to get a run off
the base station, the second is what each kind of frame carries, the third is what we know about
the rover's parts on this run, and the last is the report the orchard team wants and how the tool
gets built.

## Getting the run

The base station keeps every frame it heard from the rover over the radio, together with the
moment it heard it, and serves them over TCP. `TELEMETRY_HOST` holds the host name or the address
of the archive and `TELEMETRY_PORT` its port. The values here are this box's; the
grading box uses its own.

A request is one line ending in a newline, and so is the first line of every answer. A connection
can carry as many requests as you like, and more than one connection can be open at once.

    FROM <cursor> <max>
    QUIT

The cursor `start` is the beginning of the run. The archive answers either

    BATCH <k> <next>

followed by exactly k frame lines, or

    BUSY <ms>

which means the base station was busy writing and that batch is not ready yet, so ask for it
again; `ms` is how much longer the base station expects to be busy. `next` is the cursor for the
rest of the run, or the word `done` on the final batch, which is not a cursor. A cursor is an
opaque string and the only thing to do with one is to send it back. `max` is a whole number from
1 to 1000. A batch carries at most `max` frames and at most the archive's own batch size, which
is set per deployment. Anything the archive does not accept is answered with a line starting
`ERR`, and `QUIT` closes the connection.

A frame line is

    <rx_ms> <L>,<type>,<field>,<field>,...

`rx_ms` is when the base station heard the frame, in milliseconds since the Unix epoch on the
base station's own clock. The lines come in the order the base station heard them. `L` is the
radio's frame number, and `type` is one of these five:

    DRV,<t>,<up>,<count_l>,<count_r>
    CFG,<t>,<lines>,<gear>,<circ_l_um>,<circ_r_um>,<mirrored>
    FIX,<week>,<tow_ms>,<east_mm>,<north_mm>,<q>
    TAG,<t>,<tree>,<result>
    SYS,<t>,<step_ms>

Every field is a decimal integer except `mirrored`, which is `left` or `right`, and `result`,
which is `ok` or `blur`. Wherever it appears, `t` is the mission computer's clock in milliseconds
since the Unix epoch.

## The rover

### Mission computer and radio

The computer stamps every frame it makes or passes on with its own clock, except FIX frames, and
hands them to the radio in the order it made them. The radio numbers frames in that order with a
16-bit counter that goes from 65535 back to 0, and a run is long enough for that to happen.

While the link is up the radio sends each frame as it is handed over. A frame lost in the air at
that point is never sent again. The radio holds on to its 2,048 most recent frames, and when the
link has dropped and comes back it sends again every frame still held that was handed over
during the drop or in the two seconds before it, newest first, while new frames keep going out
as they are made. Frames that got through just before a drop therefore reach the base station
twice. A frame is the same frame when its text after the arrival time is the same.

### The computer's clock

The computer started the run on its battery clock, which was wrong. Its time service stepped the
clock once during the run and logged it in the one SYS frame of the run, stamped on the corrected
clock, with `step_ms` the amount the clock was moved by (it can be negative). From the step on,
the computer's clock is right to the millisecond. A stamp the computer made before the step is
`step_ms` behind the true time of the moment it stamped.

### Drive controller

The controller sends a DRV frame every 20 ms of its own time. `up` is milliseconds since it
booted. `count_l` and `count_r` are the left and right drive encoders' counters; each is 16 bits
and wraps in both directions. An encoder has `lines` lines per motor turn and the controller
counts every edge of both quadrature channels, which makes four counts per line. The motor turns
`gear` times for each turn of its wheel, and `circ_l_um` and `circ_r_um` are the rolling
circumferences of the two wheels, measured on this rover, in micrometres. The motor on the side
`mirrored` names is mounted the other way round, so that side's counter falls while its wheel
rolls forward. A CFG frame, the same all run, goes out when the controller boots and every
minute after that.

Between two drive frames of one boot that the archive holds, neither wheel turns as much as half
the counter's range.

The controller had been up for hours when the run started. It browned out once during the run.
The drive stops while it is down, and when it comes back its `up` and both counters start again
from zero.

### Receiver

The RTK receiver solves a fix every 200 ms of GPS time, on the whole 200 ms. `week` is its 10-bit
GPS week number, which last went round to 0 on 7 April 2019 (GPS week 2048), and `tow_ms` is
milliseconds into that week; `tow_ms` starts again from 0 when a new GPS week begins, at Sunday
00:00:00 GPS time. GPS time counts from 6 January 1980 00:00:00 UTC and has no leap seconds, and
since 1 January 2017 it has been 18 seconds ahead of UTC.

`east_mm` and `north_mm` are the antenna's position in the orchard's site grid, whose origin is
the base station, so either can be negative. `q` is 4 for a fixed RTK solution, 5 for float, 1
for a standalone fix and 0 for none, when the position reads 0,0. Under the canopy the receiver
drops to float or loses the fix for a while. The computer passes FIX frames on exactly as the
receiver wrote them, with no stamp of its own.

### Camera

The row camera watches the trees on both sides of the alley. The first time the rover passes a
tree it tags it: the TAG frame is stamped with the moment the camera took the picture and handed
to the radio at that moment. A blurred picture is tagged `blur`, and the tree is tagged again the
next time the rover passes it, from the next alley. Now and then that picture is blurred too, and
the tree is not tagged a third time.

## The report

The tool writes one line to standard output: a JSON object with the members `distance_mm`,
`lost_frames` and `trees`. Whitespace and the order of members do not matter. Every figure in it
is a JSON integer. Times in the report are true times: UTC, milliseconds since the Unix epoch.

`distance_mm` is how far the rover's centre, the point midway between the two drive wheels,
travelled during the run. From one drive frame to the next one the archive holds from the same
boot, the centre moves by half the sum of the two wheels' forward travel, and that step adds the
size of the move to the distance, whichever way the centre went. The run starts at the archive's
first drive frame, and the first step of the boot after the brownout starts from the counters at
zero. The distance is summed exactly and rounded down to a whole millimetre once, at the end.

`lost_frames` is how many of the frames the radio numbered from the first frame the archive holds
to the last one, both included, the archive does not hold.

`trees` is an array with one object per tree that has a sharp (`ok`) tag, in ascending order of
`id`, with the members:

- `id`, the tree number from the tag
- `utc_ms`, the true time of the sharp tag
- `odometer_mm`, the distance as above counted up to and including the last drive frame at or
  before that time, rounded down to a whole millimetre
- `east_mm` and `north_mm`, the antenna's position at that time, interpolated in time along the
  straight line from the fix of the epoch at or before that time to the fix of the next epoch and
  rounded down (toward minus infinity) to a whole millimetre, when the archive holds both of
  those fixes and both are fixed (q 4); otherwise both are `null`

A tree with no sharp tag is left out.

## Building and running

The tool is OCaml, compiled with the OCaml 4.13.1 on the box (`ocamlopt`) against the standard
library and the `unix` library that comes with it, and nothing else. The whole tool is
`/app/rover/src/main.ml`. The grader deletes every other file under `/app/rover/src` before it
compiles, and compiles with

    ocamlopt -ccopt -static unix.cmxa /app/rover/src/main.ml -o <a path of its own>

The linker warns that name lookups in a static program need the C library's shared files at run
time; they are on the grading box. The result is started from `/app/rover` with the two
environment variables set, and while it runs nothing else on the box can be started. The
compiler is allowed 300 seconds, and a run of the tool 120. `/app/rover/build.sh` does the same
compile and `/app/rover/run.sh` builds and runs the tool against the archive here. The grader
uses neither of them.