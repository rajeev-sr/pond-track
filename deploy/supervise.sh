#!/usr/bin/env bash
# Keep one process running: restart it whenever it exits, as systemd would.
#
#   deploy/supervise.sh <name> <command...>
#
# uvicorn was OOM-killed twice on the lab host and tmux does not bring anything
# back, so every long-running process there runs under this loop. Output goes to
# $LOG_DIR/<name>.log and each start and exit to $LOG_DIR/<name>.supervise.log,
# so a restart leaves a record of when and with what exit code.
#
# A process that dies within 30 s of starting (a bad config, a port in use) is
# restarted with a doubling delay up to a minute, so it cannot spin; one that ran
# for longer is restarted after 2 s. Ctrl-C or SIGTERM stops the child and the
# loop together.
set -u

if [ "$#" -lt 2 ]; then
  echo "usage: $0 <name> <command...>" >&2
  exit 2
fi

name=$1
shift
log_dir=${LOG_DIR:-$HOME/contour-logs}
mkdir -p "$log_dir"
events="$log_dir/$name.supervise.log"
child=0
delay=2

stop() {
  echo "$(date -Is) [$name] stopping" >>"$events"
  [ "$child" -ne 0 ] && kill "$child" 2>/dev/null && wait "$child" 2>/dev/null
  exit 0
}
trap stop INT TERM

while true; do
  started=$(date +%s)
  echo "$(date -Is) [$name] starting: $*" >>"$events"
  "$@" >>"$log_dir/$name.log" 2>&1 &
  child=$!
  wait "$child"
  code=$?
  child=0
  ran=$(($(date +%s) - started))
  echo "$(date -Is) [$name] exited with code $code after ${ran}s" >>"$events"
  if [ "$ran" -lt 30 ]; then
    delay=$((delay * 2 > 60 ? 60 : delay * 2))
  else
    delay=2
  fi
  # In the background and waited on, so a SIGTERM during a 60 s backoff is
  # acted on at once: bash runs traps only between foreground commands.
  sleep "$delay" &
  wait $!
done
