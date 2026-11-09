#!/usr/bin/env bash
# Undoes lock-clock.sh: turns boost back on, lets every core use its full
# frequency range again (ceiling back to the boosted maximum), and restores
# each core's governor, energy preference and frequency floor from the
# state lock-clock.sh saved.  Without a saved state (for example after a
# reboot, which clears /run) it still turns boost on and sets each floor
# to the driver's lowest efficient frequency.  Also restores the amdgpu
# power level and SMT saved by lock-clock.sh (default: `auto` and SMT on);
# SMT first, so the cores it brings back get their settings below.  Asks
# for sudo itself.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    exec sudo -- "$0" "$@"
fi

CPUS=/sys/devices/system/cpu
STATE=/run/teaspoon-clock.state
SOC_STATE=/run/teaspoon-soc.state

if [[ -e $SOC_STATE ]]; then
    # SMT first (its line is restored before the power levels either way).
    grep " " "$SOC_STATE" | sort -r | while read -r file value; do
        echo "$value" >"$file"
    done
    rm -- "$SOC_STATE"
else
    if [[ -w $CPUS/smt/control ]]; then
        echo on >"$CPUS/smt/control"
    fi
    for level in /sys/class/drm/card*/device/power_dpm_force_performance_level; do
        if [[ -w $level ]]; then
            echo auto >"$level"
        fi
    done
fi

echo 1 >"$CPUS/cpufreq/boost"

for dir in "$CPUS"/cpu[0-9]*/cpufreq; do
    # Ceiling first: the floor may not exceed it.
    cat "$dir/cpuinfo_max_freq" >"$dir/scaling_max_freq"
done

if [[ -e $STATE ]]; then
    while read -r cpu governor epp floor; do
        dir=$CPUS/$cpu/cpufreq
        echo "$floor" >"$dir/scaling_min_freq"
        echo "$governor" >"$dir/scaling_governor"
        if [[ $epp != - && -w $dir/energy_performance_preference &&
            $(cat "$dir/energy_performance_preference") != "$epp" ]]; then
            echo "$epp" >"$dir/energy_performance_preference"
        fi
    done <"$STATE"
    rm -- "$STATE"
    echo "restored the settings saved by lock-clock.sh"
else
    echo "no saved state ($STATE); setting each floor to the driver's lowest efficient frequency"
    for dir in "$CPUS"/cpu[0-9]*/cpufreq; do
        floor_file=$dir/amd_pstate_lowest_nonlinear_freq
        [[ -r $floor_file ]] || floor_file=$dir/cpuinfo_min_freq
        cat "$floor_file" >"$dir/scaling_min_freq"
    done
fi

if [[ $(cat "$CPUS/cpufreq/boost") != 1 ]]; then
    echo "error: boost is still off" >&2
    exit 1
fi
echo "clock unlocked: boost on, cpu0 range $(cat "$CPUS/cpu0/cpufreq/scaling_min_freq")-$(cat "$CPUS/cpu0/cpufreq/scaling_max_freq") kHz, governor $(cat "$CPUS/cpu0/cpufreq/scaling_governor")"
