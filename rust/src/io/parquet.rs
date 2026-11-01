//! Parquet tables (`ideas/rust-port.md` §10.1).
//!
//! A [`Table`] is built column by column and written atomically.  Long runs
//! write one part file per finished unit (for example per state) under
//! `<table>.parts/` and merge the parts at the end: a Parquet file is only
//! readable once its footer is written, so appending row groups to one
//! file would lose everything on an interruption, while finished parts
//! survive it.

use std::fs::File;
use std::path::{Path, PathBuf};
use std::sync::Arc;

use anyhow::{Context, bail};
use arrow::array::{
    Array as _, ArrayRef, BooleanArray, Float64Array, Int64Array, StringArray, UInt32Array,
};
use arrow::compute::concat_batches;
use arrow::record_batch::RecordBatch;
use parquet::arrow::ArrowWriter;
use parquet::arrow::arrow_reader::ParquetRecordBatchReaderBuilder;
use parquet::basic::{Compression, ZstdLevel};
use parquet::file::properties::WriterProperties;

use super::rundir::temporary_path;

/// One column of values.
#[derive(Debug, Clone)]
pub enum Column {
    /// 64-bit integers (lengths, gains, counters, nanosecond ticks).
    I64(Vec<i64>),
    /// City indices and small counts.
    U32(Vec<u32>),
    /// Floating point (rates, times in ns).
    F64(Vec<f64>),
    /// Floating point with missing values.
    OptF64(Vec<Option<f64>>),
    /// Flags.
    Bool(Vec<bool>),
    /// Labels.
    Str(Vec<String>),
}

impl Column {
    fn len(&self) -> usize {
        match self {
            Self::I64(values) => values.len(),
            Self::U32(values) => values.len(),
            Self::F64(values) => values.len(),
            Self::OptF64(values) => values.len(),
            Self::Bool(values) => values.len(),
            Self::Str(values) => values.len(),
        }
    }

    fn into_array(self) -> ArrayRef {
        match self {
            Self::I64(values) => Arc::new(Int64Array::from(values)),
            Self::U32(values) => Arc::new(UInt32Array::from(values)),
            Self::F64(values) => Arc::new(Float64Array::from(values)),
            Self::OptF64(values) => Arc::new(Float64Array::from(values)),
            Self::Bool(values) => Arc::new(BooleanArray::from(values)),
            Self::Str(values) => Arc::new(StringArray::from(values)),
        }
    }
}

/// Named columns of equal length.
#[derive(Debug, Clone, Default)]
pub struct Table {
    columns: Vec<(String, Column)>,
}

impl Table {
    /// An empty table.
    pub fn new() -> Self {
        Self::default()
    }

    /// Adds a column (builder style).
    #[must_use]
    pub fn with(mut self, name: &str, column: Column) -> Self {
        self.columns.push((name.to_string(), column));
        self
    }

    fn into_batch(self) -> anyhow::Result<RecordBatch> {
        if let Some((_, first)) = self.columns.first() {
            let rows = first.len();
            if let Some((name, column)) =
                self.columns.iter().find(|(_, column)| column.len() != rows)
            {
                bail!("column {name} has {} rows, expected {rows}", column.len());
            }
        }
        let arrays: Vec<(String, ArrayRef)> = self
            .columns
            .into_iter()
            .map(|(name, column)| (name, column.into_array()))
            .collect();
        Ok(RecordBatch::try_from_iter(arrays)?)
    }
}

fn properties() -> WriterProperties {
    WriterProperties::builder()
        .set_compression(Compression::ZSTD(ZstdLevel::default()))
        .build()
}

fn write_batch(path: &Path, batch: &RecordBatch) -> anyhow::Result<()> {
    let temporary = temporary_path(path);
    let file =
        File::create(&temporary).with_context(|| format!("creating {}", temporary.display()))?;
    let mut writer = ArrowWriter::try_new(file, batch.schema(), Some(properties()))?;
    writer.write(batch)?;
    writer.close()?;
    std::fs::rename(&temporary, path).with_context(|| format!("renaming to {}", path.display()))?;
    Ok(())
}

/// Writes `table` to `path` atomically.
///
/// # Errors
///
/// If the columns differ in length or the file cannot be written.
pub fn write(path: &Path, table: Table) -> anyhow::Result<()> {
    write_batch(path, &table.into_batch()?)
}

/// The part directory of table `name` in `directory`.
pub fn parts_directory(directory: &Path, name: &str) -> PathBuf {
    directory.join(format!("{name}.parts"))
}

/// Writes one finished part of table `name` (atomically).  The presence of
/// a part marks its unit as done, which makes runs resumable.
///
/// # Errors
///
/// If the part cannot be written.
pub fn write_part(directory: &Path, name: &str, key: &str, table: Table) -> anyhow::Result<()> {
    let parts = parts_directory(directory, name);
    std::fs::create_dir_all(&parts)?;
    write(&parts.join(format!("{key}.parquet")), table)
}

/// Whether part `key` of table `name` exists.
pub fn has_part(directory: &Path, name: &str, key: &str) -> bool {
    parts_directory(directory, name)
        .join(format!("{key}.parquet"))
        .is_file()
}

/// Reads every row of a Parquet file as one batch.
///
/// # Errors
///
/// If the file cannot be read.
pub fn read(path: &Path) -> anyhow::Result<RecordBatch> {
    let file = File::open(path).with_context(|| format!("opening {}", path.display()))?;
    let builder = ParquetRecordBatchReaderBuilder::try_new(file)?;
    let schema = builder.schema().clone();
    let batches = builder.build()?.collect::<Result<Vec<_>, _>>()?;
    Ok(concat_batches(&schema, &batches)?)
}

/// Merges the parts of table `name` (in key order) into `<name>.parquet`.
///
/// # Errors
///
/// If there are no parts, their schemas differ, or a file fails.
pub fn merge_parts(directory: &Path, name: &str) -> anyhow::Result<PathBuf> {
    let parts = parts_directory(directory, name);
    let mut paths: Vec<PathBuf> = std::fs::read_dir(&parts)
        .with_context(|| format!("reading {}", parts.display()))?
        .flatten()
        .map(|entry| entry.path())
        .filter(|path| {
            path.extension()
                .is_some_and(|extension| extension == "parquet")
        })
        .collect();
    paths.sort();
    if paths.is_empty() {
        bail!("{} holds no parts", parts.display());
    }
    let batches = paths
        .iter()
        .map(|path| read(path))
        .collect::<anyhow::Result<Vec<_>>>()?;
    let schema = batches[0].schema();
    let merged = concat_batches(&schema, &batches)
        .with_context(|| format!("parts of {name} have different schemas"))?;
    let path = directory.join(format!("{name}.parquet"));
    write_batch(&path, &merged)?;
    Ok(path)
}

/// A named column of a batch as `i64` values (Int64 or UInt32 columns).
///
/// # Errors
///
/// If the column is missing, has another type, or holds nulls.
pub fn column_i64(batch: &RecordBatch, name: &str) -> anyhow::Result<Vec<i64>> {
    let column = batch
        .column_by_name(name)
        .with_context(|| format!("table has no column {name}"))?;
    if column.null_count() > 0 {
        bail!("column {name} holds nulls");
    }
    if let Some(values) = column.as_any().downcast_ref::<Int64Array>() {
        return Ok(values.values().to_vec());
    }
    if let Some(values) = column.as_any().downcast_ref::<UInt32Array>() {
        return Ok(values
            .values()
            .iter()
            .map(|&value| i64::from(value))
            .collect());
    }
    bail!("column {name} is {:?}, not an integer", column.data_type())
}

/// A named Float64 column of a batch (nulls become NaN).
///
/// # Errors
///
/// If the column is missing or has another type.
pub fn column_f64(batch: &RecordBatch, name: &str) -> anyhow::Result<Vec<f64>> {
    let column = batch
        .column_by_name(name)
        .with_context(|| format!("table has no column {name}"))?;
    let values = column
        .as_any()
        .downcast_ref::<Float64Array>()
        .with_context(|| format!("column {name} is {:?}, not f64", column.data_type()))?;
    Ok((0..values.len())
        .map(|index| {
            if values.is_null(index) {
                f64::NAN
            } else {
                values.value(index)
            }
        })
        .collect())
}

/// A named Utf8 column of a batch.
///
/// # Errors
///
/// If the column is missing, has another type, or holds nulls.
pub fn column_str(batch: &RecordBatch, name: &str) -> anyhow::Result<Vec<String>> {
    let column = batch
        .column_by_name(name)
        .with_context(|| format!("table has no column {name}"))?;
    if column.null_count() > 0 {
        bail!("column {name} holds nulls");
    }
    let values = column
        .as_any()
        .downcast_ref::<StringArray>()
        .with_context(|| format!("column {name} is {:?}, not text", column.data_type()))?;
    Ok((0..values.len())
        .map(|index| values.value(index).to_string())
        .collect())
}

/// A named Boolean column of a batch.
///
/// # Errors
///
/// If the column is missing, has another type, or holds nulls.
pub fn column_bool(batch: &RecordBatch, name: &str) -> anyhow::Result<Vec<bool>> {
    let column = batch
        .column_by_name(name)
        .with_context(|| format!("table has no column {name}"))?;
    if column.null_count() > 0 {
        bail!("column {name} holds nulls");
    }
    let values = column
        .as_any()
        .downcast_ref::<BooleanArray>()
        .with_context(|| format!("column {name} is {:?}, not boolean", column.data_type()))?;
    Ok((0..values.len()).map(|index| values.value(index)).collect())
}

/// Writes `batch` as CSV (header, then one line per row) to `out`,
/// keeping only `columns` when given.  Values containing commas or quotes
/// are quoted.
///
/// # Errors
///
/// If a named column does not exist or writing fails.
pub fn write_csv(
    batch: &RecordBatch,
    columns: &[String],
    out: &mut impl std::io::Write,
) -> anyhow::Result<()> {
    use arrow::util::display::{ArrayFormatter, FormatOptions};
    let schema = batch.schema();
    let indices: Vec<usize> = if columns.is_empty() {
        (0..batch.num_columns()).collect()
    } else {
        columns
            .iter()
            .map(|name| {
                schema
                    .index_of(name)
                    .with_context(|| format!("no column {name}"))
            })
            .collect::<anyhow::Result<_>>()?
    };
    let options = FormatOptions::default().with_null("");
    let formatters = indices
        .iter()
        .map(|&index| ArrayFormatter::try_new(batch.column(index).as_ref(), &options))
        .collect::<Result<Vec<_>, _>>()?;
    let quote = |text: String| {
        if text.contains([',', '"', '\n']) {
            format!("\"{}\"", text.replace('"', "\"\""))
        } else {
            text
        }
    };
    let header: Vec<String> = indices
        .iter()
        .map(|&index| quote(schema.field(index).name().clone()))
        .collect();
    writeln!(out, "{}", header.join(","))?;
    for row in 0..batch.num_rows() {
        let line: Vec<String> = formatters
            .iter()
            .map(|formatter| quote(formatter.value(row).to_string()))
            .collect();
        writeln!(out, "{}", line.join(","))?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parts_merge_in_key_order() {
        let directory =
            std::env::temp_dir().join(format!("aos-parquet-test-{}", std::process::id()));
        std::fs::create_dir_all(&directory).unwrap();
        for key in ["b", "a"] {
            let table = Table::new()
                .with("key", Column::Str(vec![key.to_string(); 2]))
                .with("value", Column::I64(vec![1, 2]));
            write_part(&directory, "cells", key, table).unwrap();
        }
        assert!(has_part(&directory, "cells", "a"));
        let merged = read(&merge_parts(&directory, "cells").unwrap()).unwrap();
        assert_eq!(merged.num_rows(), 4);
        let keys = merged
            .column(0)
            .as_any()
            .downcast_ref::<StringArray>()
            .unwrap();
        assert_eq!(keys.value(0), "a");
        std::fs::remove_dir_all(&directory).unwrap();
    }

    #[test]
    fn unequal_columns_are_refused() {
        let table = Table::new()
            .with("a", Column::I64(vec![1]))
            .with("b", Column::I64(vec![1, 2]));
        assert!(table.into_batch().is_err());
    }
}
