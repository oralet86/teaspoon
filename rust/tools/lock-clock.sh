#!/usr/bin/env bash
# Locks the CPU for timed runs: boost off, the `performance` governor 
# and energy preference on every core, and every
# core's frequency floor pinned to its ceiling 
# (4.0 GHz on a Ryzen 9 7940HS with boost off)
# so a core never slows down while briefly idle.
#
# Also (added 2026-11-02, after E0 failed on an idle machine without them):
# - the amdgpu power level forced to `high`: on this APU the GPU driver's
#   power management also sets the data-fabric (FCLK) and memory (MCLK)
#   clocks, which otherwise follow load (FCLK 500, 1600 or 1960 MHz), so a
#   memory-bound operator ran up to 31% faster when other cores were busy
#   and two quiet single-core phases differed by up to 12%;
# - SMT off: any task on the timed core's sibling thread slowed it by
#   24-48%, and an idle desktop keeps hundreds of short tasks a second on
#   every logical CPU.  This halves the logical CPUs (16 -> 8), so untimed
#   stages that use every CPU run on 8.
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
SOC_STATE=/run/teaspoon-soc.state

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

# Fabric and memory clocks, then SMT; the previous values go to $SOC_STATE.
: >"$SOC_STATE"
for level in /sys/class/drm/card*/device/power_dpm_force_performance_level; do
    [[ -w $level ]] || continue
    echo "$level $(cat "$level")" >>"$SOC_STATE"
    echo high >"$level"
done
if [[ -w $CPUS/smt/control ]]; then
    echo "$CPUS/smt/control $(cat "$CPUS/smt/control")" >>"$SOC_STATE"
    echo off >"$CPUS/smt/control"
fi

# Verify, loudly.
failed=0
for level in /sys/class/drm/card*/device/power_dpm_force_performance_level; do
    if [[ -r $level && $(cat "$level") != high ]]; then
        echo "error: $level is $(cat "$level"), not high" >&2
        failed=1
    fi
done
if [[ -r $CPUS/smt/control && $(cat "$CPUS/smt/control") != off ]]; then
    echo "error: SMT is $(cat "$CPUS/smt/control"), not off" >&2
    failed=1
fi
if [[ $(cat "$CPUS/cpufreq/boost") != 0 ]]; then
    echo "error: boost is still on" >&2
    failed=1
fi
for dir in "$CPUS"/cpu[0-9]*/cpufreq; do
    cpu=$(basename "$(dirname "$dir")")
    # SMT off takes the sibling threads offline; their cpufreq files then
    # refuse reads (EBUSY).  They were set above, before going offline.
    if [[ $(cat "$CPUS/$cpu/online" 2>/dev/null || echo 1) == 0 ]]; then
        continue
    fi
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
echo "clock locked: boost off, performance governor, every core at $(cat "$CPUS/cpu0/cpufreq/scaling_max_freq") kHz, SoC clocks high, SMT off"
echo "previous settings saved in $STATE and $SOC_STATE (restored by unlock-clock.sh)"
