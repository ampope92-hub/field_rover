#!/bin/bash

# Verifier entrypoint for the rover survey. It runs in the verifier container and sees only the
# collected /app/rover/src. Everything it needs is in tests/Dockerfile; nothing is fetched here.
# The archive each graded run talks to is started by this script from the copy in this image,
# serving runs the agent never had.

set -uo pipefail
# The image's working directory is inside the tree every build deletes and recreates.
cd /
mkdir -p /logs/verifier
echo 0 > /logs/verifier/reward.txt
exec 2>&1

# Root runs this script and pytest after the build user's code has run, so nothing on root's
# PATH may be writable by anyone but root. PATH is pinned and every entry checked first.
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
hash -r
for d in ${PATH//:/ }; do
  real="$(/usr/bin/readlink -f "$d" 2>/dev/null)" || continue
  [ -d "$real" ] || continue
  owner="$(/usr/bin/stat -c %u "$real")"
  mode="$(/usr/bin/stat -c %a "$real")"
  if [ "$owner" != "0" ] || [ $(( 8#$mode & 8#022 )) -ne 0 ]; then
    echo "FATAL: $d (-> $real) is on root's PATH as owner $owner mode $mode"
    echo 0 > /logs/verifier/reward.txt
    exit 0
  fi
done
echo "root's PATH holds only root-owned directories nobody else can write"

OCAMLOPT=/usr/bin/ocamlopt
if [ ! -x "$OCAMLOPT" ]; then
  echo "FATAL: no ocamlopt at $OCAMLOPT"
  echo 0 > /logs/verifier/reward.txt
  exit 0
fi

/usr/bin/install -d -m 700 -o root -g root /opt/run
chown -R runner:runner /app/rover 2>/dev/null || true

as_runner() {
  setpriv --clear-groups --no-new-privs --reuid runner --regid runner "$@"
}

# The short run is pointed at its archive by name. SPEC.md says TELEMETRY_HOST holds a host
# name or an address, so the name goes into this container's hosts file before anything
# untrusted runs.
ARCHIVE_NAME=basestation.orchard.internal
printf '%s\t%s\n' 127.0.0.4 "$ARCHIVE_NAME" >> /etc/hosts
if ! /usr/bin/getent hosts "$ARCHIVE_NAME" >/dev/null 2>&1; then
  echo "FATAL: $ARCHIVE_NAME does not resolve here, so the named run cannot be graded"
  echo 0 > /logs/verifier/reward.txt
  exit 0
fi
echo "the short run reaches its archive by name: $ARCHIVE_NAME -> 127.0.0.4"

# The tests, the runs, the service and the reward directory are closed to the build user before
# anything it wrote runs. `ls` needs read on the directory itself, which is the right probe.
chmod 700 /tests /logs/verifier /opt/truth /opt/archive /opt/run 2>/dev/null || true
if as_runner bash -c 'test -r /tests/test_outputs.py || ls /opt/truth || ls /opt/archive || ls /logs/verifier' >/dev/null 2>&1; then
  echo "FATAL: /tests, the runs, the service or the log directory are reachable by the unprivileged build user"
  echo 0 > /logs/verifier/reward.txt
  exit 0
fi
echo "verifier files closed to the unprivileged build user"

# A root-owned pristine copy of what was collected. Every build starts from it, so nothing one
# run leaves behind reaches the next compile.
rm -rf /opt/src_pristine
cp -a /app/rover /opt/src_pristine
chown -R root:root /opt/src_pristine
chmod -R go-w /opt/src_pristine
chmod 700 /opt/src_pristine

# Every mount that is a real filesystem; proc, sysfs and the like hold nothing to execute.
scratch_roots() {
  local dev mnt fstype rest
  while read -r dev mnt fstype rest; do
    case "$fstype" in
      proc|sysfs|cgroup|cgroup2|devpts|mqueue|securityfs|debugfs|tracefs|binfmt_misc|configfs|fusectl|pstore|bpf|hugetlbfs|autofs|nsfs|rpc_pipefs) continue ;;
    esac
    case "$mnt" in /proc|/proc/*|/sys|/sys/*) continue ;; esac
    printf '%s\n' "$mnt"
  done < /proc/mounts
}

# While the tool runs, nothing else can be started: every regular file with a group or other
# execute bit, on every real mount, loses those bits, and the tool runs as a user id drawn for that
# run alone with a limit of one process, so a fork or a spawn fails. The tool is a static binary
# that user owns, so it keeps its own bit and needs no loader. The modes are saved and restored
# after the run, and by the EXIT trap if the script dies in between.
EXEC_SAVED=""
close_exec() {
  local roots left
  roots="$(scratch_roots)"
  EXEC_SAVED="$(echo "$roots" | /usr/bin/xargs -d '\n' -I{} /usr/bin/find {} -xdev -type f -perm /011 \
                   -printf '%m %p\n' 2>/dev/null)"
  echo "$EXEC_SAVED" | /usr/bin/cut -d' ' -f2- | /usr/bin/xargs -d '\n' -r /usr/bin/chmod go-x 2>/dev/null || true
  left="$(echo "$roots" | /usr/bin/xargs -d '\n' -I{} /usr/bin/find {} -xdev -type f -perm /011 2>/dev/null)"
  echo "closed while the tool runs: $(echo "$EXEC_SAVED" | /usr/bin/grep -c .) programs lost their group and other execute bits;" \
       "still executable by the build user: $(echo "$left" | /usr/bin/grep -c .)" $(echo "$left" | /usr/bin/tr '\n' ' ')
}
open_exec() {
  local m
  [ -n "${EXEC_SAVED:-}" ] || return 0
  for m in $(echo "$EXEC_SAVED" | /usr/bin/cut -d' ' -f1 | /usr/bin/sort -u); do
    echo "$EXEC_SAVED" | /usr/bin/awk -v m="$m" '$1 == m { print substr($0, length(m) + 2) }' \
      | /usr/bin/xargs -d '\n' -r /usr/bin/chmod "$m" 2>/dev/null || true
  done
  EXEC_SAVED=""
}

# The interpreters on the box, by real path, closed while the OCaml compiles: the compile runs
# ocamlopt, the C compiler and the linker and has no use for python or perl.
INTERP="$(for t in python3 python3.13 pip perl; do
            p="$(command -v "$t" 2>/dev/null)" && readlink -f "$p"; done | sort -u)"
close_interp() { for f in $INTERP; do /usr/bin/chmod 750 "$f" 2>/dev/null || true; done; }
open_interp()  { for f in $INTERP; do /usr/bin/chmod 755 "$f" 2>/dev/null || true; done; }
trap 'open_exec; open_interp' EXIT

echo ok > /opt/run/build_check.txt

# Compile the collected source. The build starts from the root-owned copy with main.ml stamped
# fresh and no binary or compiler output left over, and main.ml is the only thing under src that
# survives: every other path there is deleted first, so an object, a C stub or a second module
# never reaches the compiler. The compile line is the one SPEC.md states, bounded by its 300
# seconds. Anything else in the tree loses its execute bits afterwards, so a prebuilt helper is
# inert.
build_tool() {
  local status
  /usr/bin/find /tmp -mindepth 1 -maxdepth 1 ! -name 'pytest_cache' -exec /usr/bin/rm -rf {} + 2>/dev/null || true
  /usr/bin/rm -rf /home/runner
  /usr/bin/install -d -o runner -g runner -m 755 /home/runner
  /usr/bin/rm -rf /app/rover
  /usr/bin/cp -a /opt/src_pristine /app/rover
  /usr/bin/rm -rf /app/rover/bin
  /usr/bin/find /app/rover/src -mindepth 1 -maxdepth 1 ! -name main.ml -exec /usr/bin/rm -rf {} + 2>/dev/null || true
  if [ -L /app/rover/src/main.ml ] || [ ! -f /app/rover/src/main.ml ]; then
    echo "src/main.ml is missing or is not a regular file"
    /usr/bin/rm -rf /app/rover/src/main.ml
  fi
  echo "left under src for the compile: $(/usr/bin/find /app/rover/src -mindepth 1 | /usr/bin/tr '\n' ' ')"
  /usr/bin/install -d -o runner -g runner -m 755 /app/rover/bin
  /usr/bin/chown -R runner:runner /app/rover
  /usr/bin/chmod 755 /app/rover   # the pristine copy is 0700; the run's own uid must be able to reach bin/survey
  /usr/bin/touch /app/rover/src/main.ml 2>/dev/null || true

  close_interp
  setsid /usr/bin/timeout --signal=KILL 300 \
    setpriv --clear-groups --no-new-privs --reuid runner --regid runner \
    env -C /app/rover HOME=/home/runner TMPDIR=/tmp \
      "$OCAMLOPT" -ccopt -static unix.cmxa /app/rover/src/main.ml -o /app/rover/bin/survey \
      > /opt/run/compile.txt 2>&1
  status=$?
  /usr/bin/grep -v -m 40 -e 'statically linked applications requires at runtime' -e '^/usr/bin/ld: .*libunix.a' -e 'libasmrun.a(unix' /opt/run/compile.txt
  echo "ocamlopt exit status: $status"
  open_interp
  as_runner bash -c 'kill -9 -1' 2>/dev/null || true
  [ "$status" -eq 0 ] || echo failed > /opt/run/build_check.txt
  if [ -L /app/rover/bin/survey ] || [ ! -f /app/rover/bin/survey ]; then
    echo "no regular binary was produced at /app/rover/bin/survey"
    echo failed > /opt/run/build_check.txt
    /usr/bin/rm -f /app/rover/bin/survey
  fi
  /usr/bin/find /app/rover -type f -perm /111 ! -samefile /app/rover/bin/survey -exec /usr/bin/chmod a-x {} + 2>/dev/null || true
}

SERVICE_PID=""
start_archive() {   # $1 run, $2 host, $3 port, $4 batch size
  /usr/bin/rm -f /opt/run/ready
  /usr/local/bin/python3 -P /opt/archive/archive.py --data "/opt/truth/$1" \
      --host "$2" --port "$3" --batch "$4" \
      --ready-file /opt/run/ready --log "/opt/run/archive_$1.log" >> "/opt/run/archive_$1.err" 2>&1 &
  SERVICE_PID=$!
  local i
  for i in $(/usr/bin/seq 1 300); do
    [ -f /opt/run/ready ] && return 0
    /usr/bin/sleep 0.1
  done
  echo "FATAL: the archive never came up on $2:$3"
  return 1
}

# `kill` is the shell builtin, which nothing planted on PATH can shadow.
stop_archive() {
  [ -n "$SERVICE_PID" ] || return 0
  kill -TERM "$SERVICE_PID" 2>/dev/null || true
  wait "$SERVICE_PID" 2>/dev/null
  SERVICE_PID=""
}

# $1 host, $2 port, $3 where stdout goes. Starts the built binary from the directory SPEC.md
# names, as a user of its own, with every other program closed to it, for at most the 120 seconds
# SPEC.md states.
exec_tool() {
  local host="$1" port="$2" out="$3" pid rc t0=$SECONDS uid=$(( 20000 + RANDOM ))
  /usr/bin/chown "$uid:$uid" /app/rover/bin/survey
  close_exec
  setsid /usr/bin/timeout --signal=KILL 120 bash -c '
      cd /app/rover
      export HOME=/home/runner
      export TELEMETRY_HOST="$1" TELEMETRY_PORT="$2"
      ulimit -u 1
      exec setpriv --clear-groups --no-new-privs --reuid "$3" --regid "$3" /app/rover/bin/survey' \
      _ "$host" "$port" "$uid" > "$out" &
  pid=$!
  wait "$pid"; rc=$?
  echo "run exit status: $rc (archive $host:$port)"
  # killed only when the run hit the 120 s limit; a 124/137 before it is the tool's own exit
  if { [ "$rc" -eq 124 ] || [ "$rc" -eq 137 ]; } && [ $(( SECONDS - t0 )) -ge 120 ]; then echo killed; else echo "exit $rc"; fi > "${out%.json}.status"
  open_exec
  kill -TERM -- -"$pid" 2>/dev/null || true
  kill -KILL -- -"$pid" 2>/dev/null || true
  as_runner bash -c 'kill -9 -1' 2>/dev/null || true
  /usr/bin/chown root:root "$out" 2>/dev/null || true
  /usr/bin/chmod 600 "$out" 2>/dev/null || true
}

echo "=== the run the agent's archive serves, on another address, port and batch size ==="
build_tool
start_archive sample 127.0.0.2 7711 317 || { echo 0 > /logs/verifier/reward.txt; exit 0; }
exec_tool 127.0.0.2 7711 /opt/run/report_sample.json
stop_archive

echo "=== a longer run the agent has never seen ==="
build_tool
start_archive hidden 127.0.0.1 7712 733 || { echo 0 > /logs/verifier/reward.txt; exit 0; }
exec_tool 127.0.0.1 7712 /opt/run/report_hidden.json
stop_archive

echo "=== a short run, reached by host name ==="
build_tool
start_archive edge 127.0.0.4 7713 64 || { echo 0 > /logs/verifier/reward.txt; exit 0; }
exec_tool "$ARCHIVE_NAME" 7713 /opt/run/report_edge.json
stop_archive
printf '%s\n' "$ARCHIVE_NAME" > /opt/run/named_host.txt

as_runner bash -c 'kill -9 -1' 2>/dev/null || true
echo "=== what each archive recorded ==="
for s in sample hidden edge; do
  echo "$s: $(/usr/bin/grep -c ' BATCH ' /opt/run/archive_$s.log) batches, $(/usr/bin/grep -c ' refused' /opt/run/archive_$s.log) refused"
done
/usr/bin/head -4 /opt/run/archive_sample.log

# By absolute path; -P keeps the working directory off the module path.
/usr/local/bin/python3 -P -m pytest -o cache_dir=/tmp/pytest_cache \
  --ctrf /logs/verifier/ctrf.json /tests/test_outputs.py -rA
if [ "$?" -eq 0 ]; then echo 1 > /logs/verifier/reward.txt; else echo 0 > /logs/verifier/reward.txt; fi
exit 0
