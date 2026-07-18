"""Read-only Wu Lab analysed-omics queries and publication-style heatmaps."""

from __future__ import annotations

from collections import defaultdict
import io
import json
import os
from pathlib import Path
import re
import sqlite3
from typing import Iterable

import matplotlib

matplotlib.use("Agg", force=True)
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap
import numpy as np
import pandas as pd


MSU_PATTERN = re.compile(r"LOC_Os\d{2}g\d{5}", re.IGNORECASE)
DEFAULT_DATABASE = Path(__file__).resolve().parent / "data/lab_omics/wulab_omics_v1.sqlite"
MAX_PROFILE_ROWS_PER_DATASET = 60
FIGURE_TERM_TRANSLATIONS = {
    "电光叶蝉": "electric leafhopper",
    "白背飞虱": "white-backed planthopper",
    "褐飞虱": "brown planthopper",
    "历史芯片": "historical microarray",
    "感性背景（源文件标签：感）": "susceptible background",
    "源表未明确标注": "background not specified in source table",
    "NPB/构建体": "NPB/construct",
}


class LabOmicsUnavailable(RuntimeError):
    """Raised when the login-unlocked read-only database is unavailable."""


def canonical_msu_loci(values: Iterable[object]) -> list[str]:
    loci: list[str] = []
    for value in values:
        match = MSU_PATTERN.search(str(value or ""))
        if match:
            locus = match.group(0)
            locus = "LOC_Os" + locus[6:8] + "g" + locus[-5:]
            if locus not in loci:
                loci.append(locus)
    return loci


def resolve_database_path(explicit: str | Path | None = None) -> Path:
    candidate = Path(explicit) if explicit else Path(os.environ.get("MY_BIO_TOOLS_OMICS_DB", "") or DEFAULT_DATABASE)
    if not candidate.is_file():
        raise LabOmicsUnavailable("实验室多组学数据库尚未由登录授权解锁。")
    return candidate


def _rows(connection: sqlite3.Connection, query: str, parameters: tuple[object, ...] = ()) -> list[dict[str, object]]:
    cursor = connection.execute(query, parameters)
    columns = [item[0] for item in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def query_lab_omics(msu_loci: Iterable[object], database_path: str | Path | None = None) -> dict[str, object]:
    loci = canonical_msu_loci(msu_loci)
    empty = {
        "msu_loci": loci,
        "datasets": [],
        "comparisons": [],
        "samples": [],
        "differential": [],
        "profiles": [],
        "status": [],
        "database_schema": "",
    }
    if not loci:
        return empty
    path = resolve_database_path(database_path)
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = None
    placeholders = ",".join("?" for _ in loci)
    try:
        differential = _rows(
            connection,
            f"""
            SELECT r.*, d.display_name AS dataset_name, d.category, d.assay, d.host_background,
                   d.replicate_note, d.descriptive, d.historical, c.display_name AS comparison_name,
                   c.treatment, c.time_label, c.time_order, c.n_treatment, c.n_control,
                   c.direction, c.effect_metric, c.p_metric, c.notes AS comparison_notes
            FROM differential_results r
            JOIN datasets d ON d.dataset_id=r.dataset_id
            JOIN comparisons c ON c.comparison_id=r.comparison_id
            WHERE r.msu_locus IN ({placeholders}) AND d.inclusion_status='included'
            ORDER BY d.category,d.assay,d.dataset_id,c.time_order,c.comparison_id,r.msu_locus,
                     r.site_position,r.msu_model
            """,
            tuple(loci),
        )
        profiles = _rows(
            connection,
            f"""
            SELECT p.*, d.display_name AS dataset_name, d.category, d.assay,
                   d.host_background, d.replicate_note, d.descriptive, d.historical
            FROM abundance_profiles p
            JOIN datasets d ON d.dataset_id=p.dataset_id
            WHERE p.msu_locus IN ({placeholders}) AND d.inclusion_status='included'
            ORDER BY d.category,d.assay,d.dataset_id,p.msu_locus,p.site_position,p.msu_model
            """,
            tuple(loci),
        )
        dataset_ids = sorted({str(row["dataset_id"]) for row in [*differential, *profiles]})
        if dataset_ids:
            dataset_marks = ",".join("?" for _ in dataset_ids)
            datasets = _rows(
                connection,
                f"SELECT * FROM datasets WHERE dataset_id IN ({dataset_marks}) ORDER BY category,assay,dataset_id",
                tuple(dataset_ids),
            )
            comparisons = _rows(
                connection,
                f"SELECT * FROM comparisons WHERE dataset_id IN ({dataset_marks}) ORDER BY dataset_id,time_order,comparison_id",
                tuple(dataset_ids),
            )
            samples = _rows(
                connection,
                f"SELECT * FROM samples WHERE dataset_id IN ({dataset_marks}) ORDER BY dataset_id,sample_order",
                tuple(dataset_ids),
            )
        else:
            datasets, comparisons, samples = [], [], []
        status = _rows(
            connection,
            """SELECT dataset_id,display_name,category,assay,inclusion_status,inclusion_reason,notes
               FROM datasets WHERE inclusion_status IN ('absent','candidate') ORDER BY inclusion_status,dataset_id""",
        )
        schema = connection.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()
        sample_index: dict[str, list[dict[str, object]]] = defaultdict(list)
        for sample in samples:
            sample_index[str(sample["dataset_id"])].append(sample)
        for profile in profiles:
            values = json.loads(str(profile.pop("values_json")))
            ordered_samples = sample_index.get(str(profile["dataset_id"]), [])
            profile["sample_values"] = {
                str(sample["original_sample_code"]): values[index] if index < len(values) else None
                for index, sample in enumerate(ordered_samples)
            }
        return {
            "msu_loci": loci,
            "datasets": datasets,
            "comparisons": comparisons,
            "samples": samples,
            "differential": differential,
            "profiles": profiles,
            "status": status,
            "database_schema": str(schema[0]) if schema else "",
        }
    finally:
        connection.close()


def _max_abs(values: pd.Series) -> float | None:
    numeric = pd.to_numeric(values, errors="coerce").dropna()
    if numeric.empty:
        return None
    return float(numeric.iloc[int(np.argmax(np.abs(numeric.to_numpy(dtype=float))))])


def _figure_term(value: object) -> str:
    text = str(value or "")
    for source, target in FIGURE_TERM_TRANSLATIONS.items():
        text = text.replace(source, target)
    return text


def cross_project_matrix(result: dict[str, object]) -> tuple[pd.DataFrame, pd.DataFrame]:
    differential = pd.DataFrame(result.get("differential", []))
    loci = list(result.get("msu_loci", []))
    if differential.empty:
        return pd.DataFrame(index=loci), pd.DataFrame()
    comparison_meta = (
        differential[
            [
                "comparison_id", "dataset_name", "assay", "host_background", "comparison_name",
                "treatment", "time_label", "time_order", "historical",
            ]
        ]
        .drop_duplicates("comparison_id")
        .copy()
    )
    comparison_meta["column_label"] = comparison_meta.apply(
        lambda row: " | ".join(
            part
            for part in [
                _figure_term(row.get("treatment") or row.get("comparison_name") or ""),
                _figure_term(row.get("time_label") or ""),
                _figure_term(row.get("assay") or ""),
                _figure_term(row.get("host_background") or ""),
                str(row.get("comparison_id") or "").split(":", 1)[0],
            ]
            if part
        ),
        axis=1,
    )
    comparison_meta = comparison_meta.sort_values(
        ["historical", "treatment", "time_order", "assay", "host_background", "dataset_name"],
        kind="stable",
        na_position="last",
    )
    labels = dict(zip(comparison_meta["comparison_id"], comparison_meta["column_label"]))
    records: list[dict[str, object]] = []
    for (locus, comparison_id), group in differential.groupby(["msu_locus", "comparison_id"], sort=False):
        feature_types = set(str(value) for value in group["feature_type"].dropna())
        aggregation = "max_abs_site" if feature_types & {"phosphosite", "ubiquitination_site"} else "median_model"
        if aggregation == "max_abs_site":
            value = _max_abs(group["log2fc"])
        else:
            numeric = pd.to_numeric(group["log2fc"], errors="coerce").dropna()
            value = float(numeric.median()) if not numeric.empty else None
        records.append(
            {
                "msu_locus": locus,
                "comparison_id": comparison_id,
                "comparison_label": labels.get(comparison_id, comparison_id),
                "log2fc": value,
                "aggregation": aggregation,
                "source_rows": len(group),
            }
        )
    plotting = pd.DataFrame(records)
    if plotting.empty:
        return pd.DataFrame(index=loci), plotting
    matrix = plotting.pivot(index="msu_locus", columns="comparison_label", values="log2fc")
    matrix = matrix.reindex(index=loci)
    ordered_columns = [labels[value] for value in comparison_meta["comparison_id"] if labels[value] in matrix.columns]
    matrix = matrix.reindex(columns=list(dict.fromkeys(ordered_columns)))
    return matrix, plotting


def _row_zscore(matrix: pd.DataFrame) -> pd.DataFrame:
    values = matrix.to_numpy(dtype=float)
    means = np.nanmean(values, axis=1, keepdims=True)
    standard_deviations = np.nanstd(values, axis=1, keepdims=True)
    standard_deviations[~np.isfinite(standard_deviations) | (standard_deviations == 0)] = 1.0
    return pd.DataFrame((values - means) / standard_deviations, index=matrix.index, columns=matrix.columns)


def _heatmap_bytes(matrix: pd.DataFrame, title: str, colorbar_label: str) -> dict[str, bytes]:
    if matrix.empty or matrix.shape[1] == 0:
        return {}
    values = matrix.to_numpy(dtype=float)
    finite = np.abs(values[np.isfinite(values)])
    bound = float(np.quantile(finite, 0.99)) if finite.size else 1.0
    bound = max(bound, 0.25)
    cmap = LinearSegmentedColormap.from_list("wulab_diverging", ["#2166AC", "#F7F7F7", "#B2182B"])
    cmap.set_bad("#D1D5DB")
    masked = np.ma.masked_invalid(values)
    width = min(22.0, max(7.2, 3.4 + matrix.shape[1] * 0.34))
    height = min(18.0, max(4.2, 2.8 + matrix.shape[0] * 0.38))
    figure, axis = plt.subplots(figsize=(width, height))
    image = axis.imshow(masked, aspect="auto", interpolation="nearest", cmap=cmap, vmin=-bound, vmax=bound)
    axis.set_xticks(np.arange(matrix.shape[1]), labels=[str(value) for value in matrix.columns], rotation=60, ha="right", fontsize=7)
    axis.set_yticks(np.arange(matrix.shape[0]), labels=[str(value) for value in matrix.index], fontsize=8)
    axis.set_title(title, fontsize=12, weight="bold", pad=12)
    axis.set_xlabel("Ordered conditions (columns are not clustered)", fontsize=9)
    axis.set_ylabel("MSU locus / feature", fontsize=9)
    axis.set_xticks(np.arange(-0.5, matrix.shape[1], 1), minor=True)
    axis.set_yticks(np.arange(-0.5, matrix.shape[0], 1), minor=True)
    axis.grid(which="minor", color="white", linewidth=0.35)
    axis.tick_params(which="minor", bottom=False, left=False)
    colorbar = figure.colorbar(image, ax=axis, fraction=0.025, pad=0.02)
    colorbar.set_label(colorbar_label, fontsize=9)
    left_margin = min(0.30, max(0.10, 0.07 + max(len(str(value)) for value in matrix.index) * 0.003))
    figure.subplots_adjust(left=left_margin, right=0.96, bottom=0.34, top=0.90)
    outputs: dict[str, bytes] = {}
    for extension, dpi in (("svg", None), ("pdf", None), ("png", 600)):
        buffer = io.BytesIO()
        figure.savefig(buffer, format=extension, dpi=dpi, facecolor="white")
        outputs[extension] = buffer.getvalue()
    plt.close(figure)
    return outputs


def _dataframe_csv(frame: pd.DataFrame) -> bytes:
    return frame.to_csv(index=True).encode("utf-8-sig")


def project_profile_matrices(result: dict[str, object]) -> list[tuple[str, str, pd.DataFrame, pd.DataFrame]]:
    profile_rows = list(result.get("profiles", []))
    samples = pd.DataFrame(result.get("samples", []))
    output: list[tuple[str, str, pd.DataFrame, pd.DataFrame]] = []
    by_dataset: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in profile_rows:
        by_dataset[str(row["dataset_id"])].append(row)
    for dataset_id, rows in sorted(by_dataset.items()):
        sample_rows = samples[samples["dataset_id"] == dataset_id].sort_values("sample_order", kind="stable") if not samples.empty else pd.DataFrame()
        columns = list(sample_rows["original_sample_code"]) if not sample_rows.empty else list(rows[0].get("sample_values", {}))
        matrix_rows: list[list[float | None]] = []
        labels: list[str] = []
        seen: set[tuple[str, str]] = set()
        for row in rows:
            site = ""
            if row.get("site_position") is not None:
                site = f" {row.get('site_residue') or ''}{row['site_position']}"
            model = str(row.get("msu_model") or row.get("original_id") or "")
            label = f"{row['msu_locus']} | {model}{site}".strip()
            values = [row.get("sample_values", {}).get(column) for column in columns]
            dedupe_key = (label, json.dumps(values, separators=(",", ":")))
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            labels.append(label)
            matrix_rows.append(values)
            if len(labels) >= MAX_PROFILE_ROWS_PER_DATASET:
                break
        if not matrix_rows:
            continue
        raw = pd.DataFrame(matrix_rows, index=labels, columns=columns, dtype=float)
        raw = raw.loc[raw.notna().any(axis=1)]
        if raw.empty:
            continue
        scaled = _row_zscore(raw) if raw.shape[1] > 1 else raw.copy()
        title = dataset_id
        output.append((dataset_id, title, raw, scaled))
    return output


def build_lab_omics_artifacts(result: dict[str, object]) -> tuple[dict[str, bytes], dict[str, bytes]]:
    charts: dict[str, bytes] = {}
    raw_artifacts: dict[str, bytes] = {}
    matrix, plotting = cross_project_matrix(result)
    if not matrix.empty:
        for extension, payload in _heatmap_bytes(
            matrix,
            "Wu Lab analysed multi-omics: treatment response",
            "Source log2FC (treatment / control)",
        ).items():
            charts[f"lab_omics/heatmap_cross_project_log2fc.{extension}"] = payload
        raw_artifacts["lab_omics/heatmap_cross_project_log2fc_matrix.csv"] = _dataframe_csv(matrix)
        raw_artifacts["lab_omics/heatmap_cross_project_log2fc_long.csv"] = plotting.to_csv(index=False).encode("utf-8-sig")
    for dataset_id, title, raw, scaled in project_profile_matrices(result):
        safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", dataset_id)
        for extension, payload in _heatmap_bytes(
            scaled,
            f"{title}: within-project abundance pattern",
            "Row z-score within this dataset",
        ).items():
            charts[f"lab_omics/project_{safe_id}_abundance_heatmap.{extension}"] = payload
        raw_artifacts[f"lab_omics/project_{safe_id}_abundance_raw.csv"] = _dataframe_csv(raw)
        raw_artifacts[f"lab_omics/project_{safe_id}_abundance_row_zscore.csv"] = _dataframe_csv(scaled)
    raw_artifacts["lab_omics/query_metadata.json"] = json.dumps(
        {
            "msu_loci": result.get("msu_loci", []),
            "database_schema": result.get("database_schema", ""),
            "cross_project_metric": "existing source log2FC; PTM overview uses max absolute site response",
            "within_project_metric": "existing FPKM/TPM/count/normalized protein or PTM quantitation; row z-score only within dataset",
            "column_clustering": False,
            "missing_color": "#D1D5DB",
            "color_bounds": "symmetric 99th percentile of absolute matrix values",
        },
        ensure_ascii=False,
        indent=2,
    ).encode("utf-8")
    return charts, raw_artifacts


__all__ = [
    "LabOmicsUnavailable",
    "build_lab_omics_artifacts",
    "canonical_msu_loci",
    "cross_project_matrix",
    "project_profile_matrices",
    "query_lab_omics",
    "resolve_database_path",
]
