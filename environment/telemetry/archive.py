#!/usr/bin/env python3
"""The base station's telemetry archive for one rover run. Serves the frames the base station
received, in the order they arrived, each with its arrival time, over a line protocol on TCP.

    archive.py --data DIR --host H --port P [--batch N] [--ready-file F] [--log F]

DIR holds run.txt: one received frame per line, `<rx_ms> <frame>`, in arrival order.
--batch   the most frames one batch carries (at most 1000)
Every request that is not `FROM <cursor> <max>` with a known cursor and max 1 to 1000 is refused.
"""
import argparse
import contextlib
import secrets
import socketserver
import sys
import threading
import time

MAX_ASK = 1000


class Archive:
    def __init__(self, lines, batch, log):
        self.lines = lines
        self.batch = batch
        self.log = log
        self.lock = threading.Lock()
        self.cursors = {"start": 0}

    def note(self, msg):
        with self.lock:
            self.log.write(f"{time.strftime('%H:%M:%S')} {msg}\n")
            self.log.flush()

    def serve(self, cursor, want):
        """Returns (lines, next)."""
        with self.lock:
            pos = self.cursors[cursor]
            chunk = self.lines[pos:pos + min(want, self.batch)]
            end = pos + len(chunk)
            if end >= len(self.lines):
                nxt = "done"
            else:
                nxt = secrets.token_hex(5)
                self.cursors[nxt] = end
            return chunk, nxt


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        a = self.server.archive
        peer = f"{self.client_address[0]}:{self.client_address[1]}"
        a.note(f"open {peer}")
        try:
            while True:
                raw = self.rfile.readline(4096)
                if not raw:
                    break
                if not raw.endswith(b"\n"):
                    a.note(f"{peer} refused a request with no newline")
                    self.wfile.write(b"ERR a request ends in a newline\n")
                    break
                parts = raw.decode("ascii", "replace").split()
                if len(parts) != 3 or parts[0] != "FROM":
                    a.note(f"{peer} refused {' '.join(parts)[:20]!r}")
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
                chunk, nxt = a.serve(cur, int(want))
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
    ap.add_argument("--ready-file")
    ap.add_argument("--log")
    args = ap.parse_args()
    with open(f"{args.data}/run.txt") as fh:
        lines = [ln.rstrip("\n") for ln in fh if ln.strip()]
    with open(args.log, "a") if args.log else contextlib.nullcontext(sys.stderr) as log:
        serve(args, lines, log)


def serve(args, lines, log):
    archive = Archive(lines, min(args.batch, MAX_ASK), log)
    srv = Server((args.host, args.port), Handler)
    srv.archive = archive
    archive.note(f"serving {len(lines)} frames from {args.data} on {args.host}:{args.port}, "
                 f"batches of up to {archive.batch}")
    if args.ready_file:
        with open(args.ready_file, "w") as fh:
            fh.write("ready\n")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
