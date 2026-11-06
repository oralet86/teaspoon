//! Comparisons of two measurement-A results, per state: the robustness
//! factors of `ideas/rust-port.md` §9 (main design against one changed
//! implementation choice), and one run against another (e.g. across
//! nights). The comparison with the Python `cells.csv` (§11.3) was dropped
//! on 2026-11-05 with the other Python cross-checks.
//!
//! For every state in both: the leader on each side, whether it changed,
//! whether the class agrees, and the ratio (alternative / main) of the
//! rates of the main side's three best operators.  A leader that changes
//! is reported as implementation-dependent ([claims.md] rule).

use std::collections::{BTreeMap, HashMap};
use std::path::Path;

use anyhow::bail;

use crate::io::parquet::{Column, Table, column_f64, column_i64, column_str, read, write};
use crate::io::rundir::write_atomic;

/// Per (instance, seed, state): class and per-operator rates.
type Cells = BTreeMap<(String, u32, String), (String, HashMap<String, f64>)>;

fn read_rust(directory: &Path) -> anyhow::Result<Cells> {
    let batch = read(&directory.join("cells.parquet"))?;
    let (instance, seed, state, class, operator, rate) = (
        column_str(&batch, "instance")?,
        column_i64(&batch, "seed")?,
        column_str(&batch, "state")?,
        column_str(&batch, "class")?,
        column_str(&batch, "operator")?,
        column_f64(&batch, "rate")?,
    );
    let mut cells = Cells::new();
    for row in 0..instance.len() {
        cells
            .entry((instance[row].clone(), seed[row] as u32, state[row].clone()))
            .or_insert_with(|| (class[row].clone(), HashMap::new()))
            .1
            .insert(operator[row].clone(), rate[row]);
    }
    Ok(cells)
}

fn leader(rates: &HashMap<String, f64>) -> String {
    let mut ranked: Vec<(&String, &f64)> = rates.iter().collect();
    ranked.sort_by(|a, b| b.1.total_cmp(a.1).then(a.0.cmp(b.0)));
    ranked[0].0.clone()
}

/// Compares `alternative` against `main` (each a Rust measurement
/// directory) and writes `<out>.parquet` and
/// `<out>.md`.
///
/// # Errors
///
/// If an input cannot be read or the two share no state.
pub fn compare(main: &Path, alternative: &Path, out: &Path) -> anyhow::Result<()> {
    let main_cells = read_rust(main)?;
    let other_cells = read_rust(alternative)?;
    let (mut instance, mut seed, mut state, mut main_leader, mut other_leader) =
        (vec![], vec![], vec![], vec![], vec![]);
    let (mut changed, mut class_main, mut class_other, mut ratios) =
        (vec![], vec![], vec![], vec![]);
    for (key, (main_class, main_rates)) in &main_cells {
        let Some((other_class, other_rates)) = other_cells.get(key) else {
            continue;
        };
        let shared: HashMap<String, f64> = main_rates
            .iter()
            .filter(|(name, _)| other_rates.contains_key(*name))
            .map(|(name, rate)| (name.clone(), *rate))
            .collect();
        let other_shared: HashMap<String, f64> = other_rates
            .iter()
            .filter(|(name, _)| main_rates.contains_key(*name))
            .map(|(name, rate)| (name.clone(), *rate))
            .collect();
        if shared.is_empty() {
            continue;
        }
        let (lead_main, lead_other) = (leader(&shared), leader(&other_shared));
        let mut top: Vec<(&String, &f64)> = shared.iter().collect();
        top.sort_by(|a, b| b.1.total_cmp(a.1).then(a.0.cmp(b.0)));
        let text: Vec<String> = top
            .iter()
            .take(3)
            .map(|(name, rate)| {
                let ratio = other_shared[*name] / **rate;
                format!("{name}={ratio:.3}")
            })
            .collect();
        instance.push(key.0.clone());
        seed.push(key.1);
        state.push(key.2.clone());
        changed.push(lead_main != lead_other && shared[&lead_main] > 0.0);
        main_leader.push(lead_main);
        other_leader.push(lead_other);
        class_main.push(main_class.clone());
        class_other.push(other_class.clone());
        ratios.push(text.join(" "));
    }
    if instance.is_empty() {
        bail!("the two results share no state");
    }
    let total = instance.len();
    let leaders_changed = changed.iter().filter(|&&flag| flag).count();
    let classes_differ = class_main
        .iter()
        .zip(&class_other)
        .filter(|(a, b)| a != b)
        .count();
    let mut by_class: BTreeMap<String, (usize, usize)> = BTreeMap::new();
    for (class, flag) in class_main.iter().zip(&changed) {
        let entry = by_class.entry(class.clone()).or_default();
        entry.0 += 1;
        entry.1 += usize::from(*flag);
    }
    let mut md = format!(
        "# Comparison\n\nMain: `{}`\n\nAlternative: `{}`\n\n{total} shared states; leader changed in {leaders_changed} ({:.1}%); class differs in {classes_differ} ({:.1}%).\n\n| Class (main) | States | Leader changed |\n|---|---|---|\n",
        main.display(),
        alternative.display(),
        100.0 * leaders_changed as f64 / total as f64,
        100.0 * classes_differ as f64 / total as f64,
    );
    for (class, (count, flagged)) in &by_class {
        md += &format!(
            "| {class} | {count} | {flagged} ({:.1}%) |\n",
            100.0 * *flagged as f64 / *count as f64
        );
    }
    write(
        &out.with_extension("parquet"),
        Table::new()
            .with("instance", Column::Str(instance))
            .with("seed", Column::U32(seed))
            .with("state", Column::Str(state))
            .with("main_leader", Column::Str(main_leader))
            .with("alternative_leader", Column::Str(other_leader))
            .with("leader_changed", Column::Bool(changed))
            .with("main_class", Column::Str(class_main))
            .with("alternative_class", Column::Str(class_other))
            .with("top3_rate_ratios", Column::Str(ratios)),
    )?;
    write_atomic(&out.with_extension("md"), md.as_bytes())?;
    print!("{md}");
    Ok(())
}
