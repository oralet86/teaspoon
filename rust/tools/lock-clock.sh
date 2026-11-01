#!/usr/bin/env bash
# Locks the CPU for timed runs: boost off, the `performance` governor 
# and energy preference on every core, and every
# core's frequency floor pinned to its ceiling 
# (4.0 GHz on a Ryzen 9 7940HS with boost off)
# so a core never slows down while briefly idle.
#
# The per-core settings it changes are saved to $STATE first, so that
# unlock-clock.sh restores them exactly.  Run without arguments; it asks
# for sudo itself. Check the result with `rust/target/release/aos clock`.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    exec sudo -- "$0" "$@"
fi

CPUS=/sys/devices/system/cpu
STATE=/run/teaspoon-clock.state

if [[ -e $STATE ]]; then
    echo "error: already locked ($STATE exists); run unlock-clock.sh first" >&2
    exit 1
fi

# Save every core's governor, energy preference and frequency floor.
tmp=$(mktemp "$STATE.XXXXXX")
for dir in "$CPUS"/cpu[0-9]*/cpufreq; do
    cpu=$(basename "$(dirname "$dir")")
    epp=$(cat "$dir/energy_performance_preference" 2>/dev/null || echo -)
    echo "$cpu $(cat "$dir/scaling_governor") $epp $(cat "$dir/scaling_min_freq")" >>"$tmp"
done
mv "$tmp" "$STATE"

echo 0 >"$CPUS/cpufreq/boost"
for dir in "$CPUS"/cpu[0-9]*/cpufreq; do
    echo performance >"$dir/scaling_governor"
    # With the performance governor the driver already forces this
    # preference and refuses writes of another one, so write only if needed.
    if [[ -w $dir/energy_performance_preference &&
        $(cat "$dir/energy_performance_preference") != performance ]]; then
        echo performance >"$dir/energy_performance_preference"
    fi
    # The ceiling drops to the base frequency once boost is off.
    cat "$dir/scaling_max_freq" >"$dir/scaling_min_freq"
done

# Verify, loudly.
failed=0
if [[ $(cat "$CPUS/cpufreq/boost") != 0 ]]; then
    echo "error: boost is still on" >&2
    failed=1
fi
for dir in "$CPUS"/cpu[0-9]*/cpufreq; do
    cpu=$(basename "$(dirname "$dir")")
    floor=$(cat "$dir/scaling_min_freq")
    ceiling=$(cat "$dir/scaling_max_freq")
    governor=$(cat "$dir/scaling_governor")
    if [[ $floor != "$ceiling" || $governor != performance ]]; then
        echo "error: $cpu floor $floor kHz, ceiling $ceiling kHz, governor $governor" >&2
        failed=1
    fi
done
if [[ $failed != 0 ]]; then
    echo "the clock is NOT locked; run unlock-clock.sh to restore the saved settings" >&2
    exit 1
fi
echo "clock locked: boost off, performance governor, every core at $(cat "$CPUS/cpu0/cpufreq/scaling_max_freq") kHz"
echo "previous settings saved in $STATE (restored by unlock-clock.sh)"
