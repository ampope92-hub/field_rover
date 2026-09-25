#!/usr/bin/env python3
"""The base station's telemetry archive for one rover run. Serves the frames the base station
received, in the order they arrived, each with its arrival time, over a line protocol on TCP.

    archive.py --data DIR --host H --port P [--batch N] [--busy LIST] [--ready-file F] [--log F]

DIR holds run.txt: one received frame per line, `<rx_ms> <frame>`, in arrival order.
--batch   the most frames one batch carries (at most 1000)
--busy    FROM requests answered BUSY first, comma separated: an ordinal counted over every
          connection (1 is the first FROM this archive sees), eN for every Nth one, and the
          word `last` for every fresh request of the final batch
"""
import argparse
import contextlib
import secrets
import socketserver
import sys
import threading
import time

MAX_ASK = 1000
IDLE_S = 30
BUSY_MS = 250


class Archive:
    def __init__(self, lines, batch, busy, log):
        self.lines = lines
        self.batch = batch
        self.busy_n = {int(b) for b in busy if b.isdigit()}
        self.busy_every = [int(b[1:]) for b in busy if b.startswith("e") and b[1:].isdigit()]
        self.busy_last = "last" in busy
        self.log = log
        self.lock = threading.Lock()
        self.cursors = {"start": 0}
        self.asked = 0
        self.hold = {}            # cursor -> monotonic time before which it is answered BUSY

    def note(self, msg):
        with self.lock:
            self.log.write(f"{time.strftime('%H:%M:%S')} {msg}\n")
            self.log.flush()

    def serve(self, cursor, want):
        """Returns ('BUSY', ms) or ('BATCH', lines, next)."""
        with self.lock:
            pos = self.cursors[cursor]
            now = time.monotonic()
            until = self.hold.get(cursor)
            if until is not None:
                if now < until:
                    return ("BUSY", max(1, int((until - now) * 1000) + 1))
                del self.hold[cursor]
            else:
                self.asked += 1
                final = pos + min(want, self.batch) >= len(self.lines)
                if (self.asked in self.busy_n or any(self.asked % e == 0 for e in self.busy_every)
                        or (final and self.busy_last)):
                    self.hold[cursor] = now + BUSY_MS / 1000
                    return ("BUSY", BUSY_MS)
            chunk = self.lines[pos:pos + min(want, self.batch)]
            end = pos + len(chunk)
            if end >= len(self.lines):
                nxt = "done"
            else:
                nxt = secrets.token_hex(5)
                self.cursors[nxt] = end
            return ("BATCH", chunk, nxt)


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        a = self.server.archive
        peer = f"{self.client_address[0]}:{self.client_address[1]}"
        self.request.settimeout(IDLE_S)
        a.note(f"open {peer}")
        try:
            while True:
                try:
                    raw = self.rfile.readline(4096)
                except TimeoutError:
                    a.note(f"{peer} idle for {IDLE_S} s, closed")
                    break
                if not raw:
                    break
                parts = raw.decode("ascii", "replace").split()
                if not parts:
                    continue
                if parts[0] == "QUIT" and len(parts) == 1:
                    a.note(f"{peer} QUIT")
                    break
                if parts[0] != "FROM" or len(parts) != 3:
                    a.note(f"{peer} refused {parts[0][:20]}")
                    self.wfile.write(b"ERR unknown command\n")
                    continue
                cur, want = parts[1], parts[2]
                if not want.isdigit() or not 1 <= int(want) <= MAX_ASK:
                    a.note(f"{peer} refused FROM {cur} {want[:12]}")
                    self.wfile.write(b"ERR max must be 1 to 1000\n")
                    continue
                if cur not in a.cursors:
                    a.note(f"{peer} refused cursor={cur[:40]}")
                    self.wfile.write(b"ERR no such cursor\n")
                    continue
                res = a.serve(cur, int(want))
                if res[0] == "BUSY":
                    a.note(f"{peer} BUSY cursor={cur} {res[1]} ms")
                    self.wfile.write(f"BUSY {res[1]}\n".encode())
                    continue
                _, chunk, nxt = res
                a.note(f"{peer} BATCH cursor={cur} frames={len(chunk)} next={nxt}")
                out = [f"BATCH {len(chunk)} {nxt}\n"] + [ln + "\n" for ln in chunk]
                self.wfile.write("".join(out).encode())
        except (BrokenPipeError, ConnectionResetError):
            pass
        a.note(f"close {peer}")


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=7700)
    ap.add_argument("--batch", type=int, default=500)
    ap.add_argument("--busy", default="")
    ap.add_argument("--ready-file")
    ap.add_argument("--log")
    args = ap.parse_args()
    with open(f"{args.data}/run.txt") as fh:
        lines = [ln.rstrip("\n") for ln in fh if ln.strip()]
    with open(args.log, "a") if args.log else contextlib.nullcontext(sys.stderr) as log:
        serve(args, lines, log)


def serve(args, lines, log):
    archive = Archive(lines, min(args.batch, MAX_ASK), [b for b in args.busy.split(",") if b], log)
    srv = Server((args.host, args.port), Handler)
    srv.archive = archive
    archive.note(f"serving {len(lines)} frames from {args.data} on {args.host}:{args.port}, "
                 f"batches of up to {archive.batch}, busy on {args.busy or 'nothing'}")
    if args.ready_file:
        with open(args.ready_file, "w") as fh:
            fh.write("ready\n")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
