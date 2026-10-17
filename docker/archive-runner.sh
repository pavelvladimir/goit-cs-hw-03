#!/bin/bash
# Supervise one archive process group independently of the Docker CLI connection.
set -euo pipefail
action=${1:-}
token=${2:-}
if [[ ! $token =~ ^[a-f0-9]{32}$ ]]; then
    echo 'Invalid archive operation ID.' >&2
    exit 2
fi
directory="/tmp/hw03-archive-$token"
umask 077
if [[ $action == prepare ]]; then
    mkdir -- "$directory"
    exit 0
fi

owns_group() {
    [[ ${pid:-} =~ ^[1-9][0-9]*$ && -r /proc/$pid/cmdline ]] &&
        tr '\0' '\n' < "/proc/$pid/cmdline" | grep -Fxq "HW03_ARCHIVE_ID=$token"
}

if [[ $action == cancel ]]; then
    # Prepare precedes execution. Removing an unstarted job prevents a delayed run.
    [[ -d $directory ]] || exit 0
    exec 9> "$directory/lock"
    flock -x 9
    if [[ ! -e $directory/pid ]]; then
        rm -rf -- "$directory"
        exit 0
    fi
    flock -u 9
    # Never signal a PID without verifying this operation's token (PID reuse).
    signalled=0
    for ((attempt = 0; attempt < 50; attempt++)); do
        if [[ ! -d $directory ]]; then
            exit 0
        fi
        if [[ -r $directory/pid ]]; then
            read -r pid < "$directory/pid" || true
            if owns_group; then
                if ((attempt >= 20)); then
                    kill -KILL -- "-$pid" 2>/dev/null || true
                elif ((signalled == 0)); then
                    kill -TERM -- "-$pid" 2>/dev/null || true
                    signalled=1
                fi
            fi
        fi
        sleep 0.1
    done
    echo 'Archive cancellation could not be confirmed.' >&2
    exit 1
fi

duration=${3:-}
if [[ $action != run || ! $duration =~ ^[1-9][0-9]*$ || $# -lt 4 ]]; then
    echo 'Usage: archive-runner.sh prepare ID | run ID SECONDS COMMAND... | cancel ID' >&2
    exit 2
fi
shift 3
[[ -d $directory ]] || { echo 'Archive operation was cancelled or not prepared.' >&2; exit 1; }
exec 9> "$directory/lock"
flock -x 9
[[ -d $directory && ! -e $directory/pid ]] || exit 1
export HW03_ARCHIVE_DIRECTORY="$directory"
# shellcheck disable=SC2329 # Invoked indirectly by the EXIT trap below.
cleanup() {
    # A wrapper may exit before its children finish handling TERM.
    # The process group remains ours while any of those children still exist.
    if [[ -n ${pid:-} ]]; then
        kill -KILL -- "-$pid" 2>/dev/null || true
    fi
    rm -rf -- "$directory"
}
trap 'cleanup' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# timeout owns a process group containing the wrapper and all native tool children.
# Preserve stdin explicitly for restores: a background shell command otherwise gets /dev/null.
timeout --signal=TERM --kill-after=2 "$duration" \
    env "HW03_ARCHIVE_ID=$token" "$@" <&0 9>&- &
pid=$!
printf '%s\n' "$pid" > "$directory/pid"
flock -u 9
exec 9>&-
status=0
wait "$pid" || status=$?
if [[ $status == 124 || $status == 137 ]]; then
    echo 'Archive operation timed out; container process group terminated.' >&2
    exit 124
fi
exit "$status"
