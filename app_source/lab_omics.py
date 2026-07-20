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
    "感染组": "infected",
    "未感染": "uninfected",
    "感染": "infection",
    "取食": "feeding",
    "对照": "control",
    "处理": "treatment",
    "病毒": "virus",
    "转录组": "transcriptome",
    "历史芯片": "historical microarray",
    "感性背景（源文件标签：感）": "susceptible background",
    "源表未明确标注": "background not specified in source table",
    "NPB/构建体": "NPB/construct",
    "小时": "h",
    "天": "d",
}

DISPLAY_TIER_LABELS = {
    "differential": "差异统计",
    "abundance_only": "仅定量观察",
    "published_evidence": "论文证据",
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
        raise LabOmicsUnavailable("水稻多组学证据数据库尚未由登录授权解锁。")
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
        "published_evidence": [],
        "consensus_scores": [],
        "qc_metrics": [],
        "dataset_context": [],
        "dataset_registry": [],
        "dataset_summaries": [],
        "database_schema": "",
        "data_package_version": "",
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
            WHERE r.msu_locus IN ({placeholders}) AND d.search_section='primary'
                  AND d.biological_replicates_verified=1
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
            WHERE p.msu_locus IN ({placeholders}) AND d.search_section='primary'
                  AND d.biological_replicates_verified=1
            ORDER BY d.category,d.assay,d.dataset_id,p.msu_locus,p.site_position,p.msu_model
            """,
            tuple(loci),
        )
        primary_dataset_ids = sorted({str(row["dataset_id"]) for row in [*differential, *profiles]})
        published_evidence = _rows(
            connection,
            f"""
            SELECT e.*, d.display_name AS dataset_name, d.host_background, d.treatment,
                   d.control_group, d.replicate_note, d.evidence_level,
                   d.biological_replicates_verified, d.raw_data_availability,
                   d.analysis_origin, d.tissue, d.reference_version,
                   d.statistical_threshold, d.qc_summary, d.accession, d.citation,
                   d.risk_note AS dataset_risk_note
            FROM published_evidence e
            JOIN datasets d ON d.dataset_id=e.dataset_id
            WHERE e.msu_locus IN ({placeholders}) AND d.search_section='published_evidence'
            ORDER BY d.dataset_id,e.source_file,e.source_sheet,e.source_row,e.evidence_id
            """,
            tuple(loci),
        )
        published_dataset_ids = sorted({str(row["dataset_id"]) for row in published_evidence})
        dataset_ids = sorted(set(primary_dataset_ids + published_dataset_ids))
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
               FROM datasets WHERE inclusion_status IN ('absent','candidate')
               AND search_section='primary' ORDER BY inclusion_status,dataset_id""",
        )
        schema = connection.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()
        package_version = connection.execute("SELECT value FROM metadata WHERE key='data_package_version'").fetchone()
        dataset_registry = _rows(
            connection,
            "SELECT * FROM datasets ORDER BY search_section,category,assay,dataset_id",
        )
        consensus_scores = _rows(
            connection,
            f"SELECT * FROM consensus_scores WHERE msu_locus IN ({placeholders}) ORDER BY treatment_class,rank",
            tuple(loci),
        )
        if dataset_ids:
            dataset_marks = ",".join("?" for _ in dataset_ids)
            qc_metrics = _rows(
                connection,
                f"SELECT * FROM qc_metrics WHERE dataset_id IN ({dataset_marks}) ORDER BY dataset_id,metric",
                tuple(dataset_ids),
            )
        else:
            qc_metrics = []
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
        dataset_summaries = build_dataset_summaries(
            loci=loci,
            datasets=datasets,
            comparisons=comparisons,
            samples=samples,
            differential=differential,
            profiles=profiles,
            published_evidence=published_evidence,
        )
        return {
            "msu_loci": loci,
            "datasets": datasets,
            "comparisons": comparisons,
            "samples": samples,
            "differential": differential,
            "profiles": profiles,
            "status": status,
            "published_evidence": published_evidence,
            "consensus_scores": consensus_scores,
            "qc_metrics": qc_metrics,
            "dataset_context": datasets,
            "dataset_registry": dataset_registry,
            "dataset_summaries": dataset_summaries,
            "database_schema": str(schema[0]) if schema else "",
            "data_package_version": str(package_version[0]) if package_version else "",
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


def _clean_label_part(value: object) -> str:
    text = _figure_term(value).strip()
    text = text.replace("_", " ").replace("；", " / ")
    text = re.sub(r"(?<=[A-Za-z0-9])(infection|feeding)", r" \1", text)
    text = re.sub(r"\s+", " ", text)
    text = text.replace("infection infection", "infection")
    return text.strip(" ;|-/")


def _short_label(
    treatment: object,
    background: object,
    time_label: object = "",
    *,
    fallback: object = "dataset",
) -> str:
    """Return a compact two-line scientific label without internal identifiers."""
    treatment_text = _clean_label_part(treatment) or _clean_label_part(fallback) or "Treatment"
    treatment_text = treatment_text.replace("white-backed planthopper", "WBPH")
    treatment_text = treatment_text.replace("electric leafhopper", "leafhopper")
    treatment_text = treatment_text.replace("brown planthopper", "BPH")
    treatment_text = re.sub(r"\b([A-Z]{2,8}V) infection\b", r"\1", treatment_text)
    treatment_text = re.sub(r"\bBPH feeding\s*", "BPH ", treatment_text)
    time_text = _clean_label_part(time_label)
    if time_text and time_text.lower() not in treatment_text.lower():
        treatment_text = f"{treatment_text} {time_text}"
    background_text = _clean_label_part(background) or "background n.s."
    background_text = background_text.replace("Oryza sativa", "O. sativa")
    background_text = background_text.replace("susceptible background", "susceptible")
    return f"{treatment_text}\n{background_text}"


def _unique_short_labels(rows: list[dict[str, object]]) -> None:
    """Make display labels unique while keeping accessions and dataset IDs out of axes."""
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("short_label") or "")].append(row)
    for group in grouped.values():
        dataset_ids = list(dict.fromkeys(str(row.get("dataset_id") or "") for row in group))
        if len(dataset_ids) < 2:
            continue
        representative = {str(row.get("dataset_id") or ""): row for row in group}
        ordered = sorted(representative.values(), key=lambda item: (str(item.get("assay") or ""), str(item.get("dataset_name") or "")))
        used: set[str] = set()
        replacements: dict[str, str] = {}
        for index, row in enumerate(ordered, start=1):
            first, _, second = str(row["short_label"]).partition("\n")
            assay = _clean_label_part(row.get("assay"))
            suffix = assay if assay and assay not in used else f"study {index}"
            used.add(suffix)
            replacements[str(row.get("dataset_id") or "")] = f"{first}\n{second} · {suffix}"
        for row in group:
            row["short_label"] = replacements[str(row.get("dataset_id") or "")]


def _as_float(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if np.isfinite(number) else None


def _representative_differential(rows: list[dict[str, object]]) -> dict[str, object]:
    if not rows:
        return {}
    feature_types = {str(row.get("feature_type") or "") for row in rows}
    numeric = [(row, _as_float(row.get("log2fc"))) for row in rows]
    numeric = [(row, value) for row, value in numeric if value is not None]
    if not numeric:
        selected = rows[0]
        effect = None
    elif feature_types & {"phosphosite", "ubiquitination_site"}:
        selected, effect = max(numeric, key=lambda item: abs(float(item[1])))
    else:
        values = np.asarray([float(value) for _, value in numeric], dtype=float)
        effect = float(np.median(values))
        selected = min(numeric, key=lambda item: abs(float(item[1]) - effect))[0]
    padj = _as_float(selected.get("padj"))
    pvalue = _as_float(selected.get("pvalue"))
    significance = "*" if padj is not None and padj <= 0.05 else ("ns" if padj is not None else "")
    return {
        "effect_value": effect,
        "effect_metric": str(selected.get("effect_metric") or "source log2FC"),
        "pvalue": pvalue,
        "padj": padj,
        "significance": significance,
        "comparison_id": str(selected.get("comparison_id") or ""),
        "comparison_name": str(selected.get("comparison_name") or ""),
        "regulated": str(selected.get("regulated") or ""),
        "differential_source_rows": len(rows),
    }


def _profile_groups(
    profile_rows: list[dict[str, object]],
    sample_rows: list[dict[str, object]],
) -> tuple[list[dict[str, object]], str, int]:
    """Summarise biological replicates without converting abundance into fold change."""
    if not profile_rows or not sample_rows:
        return [], "", len(profile_rows)
    ordered_samples = sorted(sample_rows, key=lambda row: int(row.get("sample_order") or 0))
    sample_codes = [str(row.get("original_sample_code") or row.get("sample_id") or "") for row in ordered_samples]
    sample_matrix: list[list[float]] = []
    for profile in profile_rows:
        sample_values = profile.get("sample_values", {})
        sample_matrix.append([
            _as_float(sample_values.get(code)) if isinstance(sample_values, dict) else None
            for code in sample_codes
        ])
    values_by_sample: list[float | None] = []
    for index in range(len(sample_codes)):
        available = [row[index] for row in sample_matrix if row[index] is not None]
        values_by_sample.append(float(np.median(available)) if available else None)
    groups: dict[tuple[str, str, str], dict[str, object]] = {}
    for sample, value in zip(ordered_samples, values_by_sample):
        group_id = str(sample.get("group_id") or sample.get("treatment") or sample.get("condition_role") or "group")
        role = str(sample.get("condition_role") or "observed")
        treatment = str(sample.get("treatment") or group_id)
        time_label = str(sample.get("time_label") or "")
        key = (group_id, treatment, time_label)
        group = groups.setdefault(
            key,
            {
                "group_id": group_id,
                "label": _short_label(treatment, "", time_label, fallback=group_id).split("\n", 1)[0],
                "role": role,
                "treatment": treatment,
                "time_label": time_label,
                "replicate_values": [],
                "sample_codes": [],
            },
        )
        if value is not None:
            group["replicate_values"].append(value)
            group["sample_codes"].append(str(sample.get("original_sample_code") or ""))
    summaries: list[dict[str, object]] = []
    for group in groups.values():
        values = np.asarray(group["replicate_values"], dtype=float)
        group["n"] = int(values.size)
        group["mean"] = float(np.mean(values)) if values.size else None
        group["sd"] = float(np.std(values, ddof=1)) if values.size > 1 else None
        summaries.append(group)
    unit = str(profile_rows[0].get("unit") or (sample_rows[0].get("unit") if sample_rows else "") or "source abundance")
    return summaries, unit, len(profile_rows)


def build_dataset_summaries(
    *,
    loci: list[str],
    datasets: list[dict[str, object]],
    comparisons: list[dict[str, object]],
    samples: list[dict[str, object]],
    differential: list[dict[str, object]],
    profiles: list[dict[str, object]],
    published_evidence: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Create the report-facing, evidence-tiered index of every matched dataset."""
    dataset_index = {str(row.get("dataset_id")): row for row in datasets}
    samples_by_dataset: dict[str, list[dict[str, object]]] = defaultdict(list)
    profiles_by_key: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
    differential_by_key: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
    published_by_key: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
    comparison_by_dataset: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in samples:
        samples_by_dataset[str(row.get("dataset_id"))].append(row)
    for row in profiles:
        profiles_by_key[(str(row.get("msu_locus")), str(row.get("dataset_id")))].append(row)
    for row in differential:
        differential_by_key[(str(row.get("msu_locus")), str(row.get("dataset_id")))].append(row)
    for row in published_evidence:
        published_by_key[(str(row.get("msu_locus")), str(row.get("dataset_id")))].append(row)
    for row in comparisons:
        comparison_by_dataset[str(row.get("dataset_id"))].append(row)

    summaries: list[dict[str, object]] = []
    primary_keys = sorted(set(profiles_by_key) | set(differential_by_key), key=lambda item: (item[0], item[1]))
    for locus, dataset_id in primary_keys:
        dataset = dataset_index.get(dataset_id, {})
        profile_rows = profiles_by_key.get((locus, dataset_id), [])
        diff_rows = differential_by_key.get((locus, dataset_id), [])
        diff_summary = _representative_differential(diff_rows)
        groups, unit, feature_count = _profile_groups(profile_rows, samples_by_dataset.get(dataset_id, []))
        dataset_comparisons = comparison_by_dataset.get(dataset_id) or [{}]
        comparison = dataset_comparisons[0]
        distinct_times = list(dict.fromkeys(str(item.get("time_label") or "") for item in dataset_comparisons if item.get("time_label")))
        time_label = distinct_times[0] if len(distinct_times) == 1 else ""
        treatment = dataset.get("treatment") or comparison.get("treatment") or dataset.get("display_name")
        tier = "differential" if diff_rows else "abundance_only"
        summaries.append(
            {
                "msu_locus": locus,
                "dataset_id": dataset_id,
                "dataset_name": str(dataset.get("display_name") or dataset_id),
                "accession": str(dataset.get("accession") or ""),
                "category": str(dataset.get("category") or ""),
                "assay": str(dataset.get("assay") or ""),
                "background": str(dataset.get("host_background") or ""),
                "treatment": str(treatment or ""),
                "time_label": str(time_label or ""),
                "short_label": _short_label(treatment, dataset.get("host_background"), time_label, fallback=dataset.get("display_name")),
                "display_tier": tier,
                "display_tier_label": DISPLAY_TIER_LABELS[tier],
                "replicate_groups": groups,
                "n_control": sum(int(group.get("n") or 0) for group in groups if group.get("role") == "control"),
                "n_treatment": sum(int(group.get("n") or 0) for group in groups if group.get("role") != "control"),
                "quantitation_unit": unit or str(dataset.get("abundance_unit") or ""),
                "profile_feature_count": feature_count,
                "availability_note": "有原始差异统计与重复定量" if diff_rows else "有重复定量，但原数据包未提供可核验的差异统计量",
                **diff_summary,
            }
        )

    for (locus, dataset_id), evidence_rows in sorted(published_by_key.items(), key=lambda item: item[0]):
        dataset = dataset_index.get(dataset_id, {})
        summaries.append(
            {
                "msu_locus": locus,
                "dataset_id": dataset_id,
                "dataset_name": str(dataset.get("display_name") or dataset_id),
                "accession": str(dataset.get("accession") or ""),
                "category": str(dataset.get("category") or "论文证据"),
                "assay": str(dataset.get("assay") or "published"),
                "background": str(dataset.get("host_background") or ""),
                "treatment": str(dataset.get("treatment") or ""),
                "time_label": "",
                "short_label": _short_label(dataset.get("treatment"), dataset.get("host_background"), fallback=dataset.get("display_name")),
                "display_tier": "published_evidence",
                "display_tier_label": DISPLAY_TIER_LABELS["published_evidence"],
                "replicate_groups": [],
                "n_control": 0,
                "n_treatment": 0,
                "quantitation_unit": "",
                "profile_feature_count": 0,
                "effect_value": None,
                "pvalue": None,
                "padj": None,
                "significance": "",
                "published_evidence_count": len(evidence_rows),
                "availability_note": "论文中的定向证据；不与本地重复定量或差异统计混合",
            }
        )
    _unique_short_labels(summaries)
    return summaries


def cross_project_matrix(result: dict[str, object]) -> tuple[pd.DataFrame, pd.DataFrame]:
    loci = list(result.get("msu_loci", []))
    summaries = [row for row in result.get("dataset_summaries", []) if row.get("display_tier") != "published_evidence"]
    if not summaries:
        return pd.DataFrame(index=loci), pd.DataFrame()
    records: list[dict[str, object]] = []
    for row in summaries:
        padj = _as_float(row.get("padj"))
        records.append(
            {
                "msu_locus": row.get("msu_locus"),
                "dataset_id": row.get("dataset_id"),
                "dataset_name": row.get("dataset_name"),
                "accession": row.get("accession"),
                "comparison_label": row.get("short_label"),
                "log2fc": _as_float(row.get("effect_value")),
                "padj": padj,
                "annotation": f"{float(row['effect_value']):.2f}{'*' if padj is not None and padj <= 0.05 else ''}" if _as_float(row.get("effect_value")) is not None else "NA",
                "display_tier": row.get("display_tier"),
                "missing_reason": "" if row.get("display_tier") == "differential" else row.get("availability_note"),
            }
        )
    plotting = pd.DataFrame(records)
    if plotting.empty:
        return pd.DataFrame(index=loci), plotting
    matrix = plotting.pivot_table(index="msu_locus", columns="comparison_label", values="log2fc", aggfunc="first", dropna=False)
    matrix = matrix.reindex(index=loci)
    ordered_rows = sorted(
        summaries,
        key=lambda row: (str(row.get("category") or ""), str(row.get("treatment") or ""), str(row.get("background") or ""), str(row.get("dataset_name") or "")),
    )
    ordered_columns = [str(row.get("short_label")) for row in ordered_rows if str(row.get("short_label")) in matrix.columns]
    matrix = matrix.reindex(columns=list(dict.fromkeys(ordered_columns)))
    return matrix, plotting


def _row_zscore(matrix: pd.DataFrame) -> pd.DataFrame:
    values = matrix.to_numpy(dtype=float)
    means = np.nanmean(values, axis=1, keepdims=True)
    standard_deviations = np.nanstd(values, axis=1, keepdims=True)
    standard_deviations[~np.isfinite(standard_deviations) | (standard_deviations == 0)] = 1.0
    return pd.DataFrame((values - means) / standard_deviations, index=matrix.index, columns=matrix.columns)


def _heatmap_bytes(
    matrix: pd.DataFrame,
    title: str,
    colorbar_label: str,
    *,
    annotations: pd.DataFrame | None = None,
) -> dict[str, bytes]:
    if matrix.empty or matrix.shape[1] == 0:
        return {}
    values = matrix.to_numpy(dtype=float)
    finite = np.abs(values[np.isfinite(values)])
    bound = float(np.quantile(finite, 0.99)) if finite.size else 1.0
    bound = max(bound, 0.25)
    cmap = LinearSegmentedColormap.from_list("wulab_diverging", ["#2166AC", "#F7F7F7", "#B2182B"])
    cmap.set_bad("#D1D5DB")
    masked = np.ma.masked_invalid(values)
    width = min(22.0, max(7.2, 3.4 + matrix.shape[1] * 0.72))
    height = min(18.0, max(4.2, 2.8 + matrix.shape[0] * 0.38))
    figure, axis = plt.subplots(figsize=(width, height))
    image = axis.imshow(masked, aspect="auto", interpolation="nearest", cmap=cmap, vmin=-bound, vmax=bound)
    axis.set_xticks(np.arange(matrix.shape[1]), labels=[str(value) for value in matrix.columns], rotation=0, ha="center", fontsize=7)
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
    if annotations is not None:
        annotations = annotations.reindex(index=matrix.index, columns=matrix.columns)
        for row_index in range(matrix.shape[0]):
            for column_index in range(matrix.shape[1]):
                annotation_value = annotations.iat[row_index, column_index]
                label = "NA" if pd.isna(annotation_value) else str(annotation_value)
                number = values[row_index, column_index]
                color = "white" if np.isfinite(number) and abs(number) >= bound * 0.55 else "#111827"
                axis.text(column_index, row_index, label, ha="center", va="center", fontsize=6.5, color=color, weight="bold")
    left_margin = min(0.30, max(0.10, 0.07 + max(len(str(value)) for value in matrix.index) * 0.003))
    figure.subplots_adjust(left=left_margin, right=0.96, bottom=0.22, top=0.88)
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
    dataset_backgrounds = {
        str(row.get("dataset_id") or ""): str(row.get("host_background") or "")
        for row in result.get("datasets", [])
    }
    matched_control_treatments = {
        (str(row.get("dataset_id") or ""), str(row.get("control_group") or "")): str(row.get("treatment") or "")
        for row in result.get("comparisons", [])
        if row.get("control_group") and row.get("treatment")
    }
    output: list[tuple[str, str, pd.DataFrame, pd.DataFrame]] = []
    by_dataset: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in profile_rows:
        by_dataset[str(row["dataset_id"])].append(row)
    for dataset_id, rows in sorted(by_dataset.items()):
        sample_rows = samples[samples["dataset_id"] == dataset_id].sort_values("sample_order", kind="stable") if not samples.empty else pd.DataFrame()
        source_columns = list(sample_rows["original_sample_code"]) if not sample_rows.empty else list(rows[0].get("sample_values", {}))
        if not sample_rows.empty:
            columns = []
            for _, sample in sample_rows.iterrows():
                group_id = str(sample.get("group_id") or "")
                treatment = str(sample.get("treatment") or group_id or sample.get("condition_role") or "")
                time_label = str(sample.get("time_label") or "")
                if str(sample.get("condition_role") or "") == "control":
                    matched_treatment = matched_control_treatments.get((dataset_id, group_id), "")
                    if matched_treatment:
                        treatment = f"{matched_treatment} control"
                        time_label = ""
                first, _, second = _short_label(
                    treatment,
                    dataset_backgrounds.get(dataset_id, ""),
                    time_label,
                    fallback=group_id,
                ).partition("\n")
                columns.append(f"{first}\n{second} · R{int(sample.get('replicate') or 0)}")
        else:
            columns = [f"sample {index + 1}" for index in range(len(source_columns))]
        matrix_rows: list[list[float | None]] = []
        labels: list[str] = []
        seen: set[tuple[str, str]] = set()
        for row in rows:
            site = ""
            if row.get("site_position") is not None:
                site = f" {row.get('site_residue') or ''}{row['site_position']}"
            model = str(row.get("msu_model") or row.get("original_id") or "")
            label = f"{row['msu_locus']} | {model}{site}".strip()
            values = [row.get("sample_values", {}).get(column) for column in source_columns]
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
        dataset_summary = next(
            (row for row in result.get("dataset_summaries", []) if str(row.get("dataset_id")) == dataset_id),
            {},
        )
        title = str(dataset_summary.get("short_label") or dataset_summary.get("dataset_name") or "Within-project abundance")
        output.append((dataset_id, title, raw, scaled))
    return output


def _draw_response_axis(axis: plt.Axes, summary: dict[str, object], *, compact: bool = False) -> None:
    groups = [group for group in summary.get("replicate_groups", []) if group.get("replicate_values")]
    if not groups:
        axis.text(0.5, 0.5, "No quantitative replicates", ha="center", va="center", transform=axis.transAxes, color="#6B7280")
        axis.set_axis_off()
        return
    control_color = "#6B7280"
    treatment_color = "#1F6F8B" if str(summary.get("category")) == "病毒" else "#B45309"
    positions = np.arange(len(groups), dtype=float)
    for position, group in zip(positions, groups):
        values = np.asarray(group.get("replicate_values", []), dtype=float)
        color = control_color if group.get("role") == "control" else treatment_color
        if values.size:
            jitter = np.linspace(-0.10, 0.10, values.size) if values.size > 1 else np.asarray([0.0])
            axis.scatter(position + jitter, values, s=16 if compact else 24, color=color, alpha=0.82, edgecolors="white", linewidths=0.35, zorder=3)
            mean = float(np.mean(values))
            sd = float(np.std(values, ddof=1)) if values.size > 1 else 0.0
            axis.errorbar(position, mean, yerr=sd, fmt="o", markersize=4.5 if compact else 5.5, color="#111827", ecolor="#111827", capsize=3, linewidth=1.0, zorder=4)
    labels = [_clean_label_part(group.get("label")) or str(group.get("group_id") or "group") for group in groups]
    axis.set_xticks(positions, labels=labels, rotation=25 if len(groups) > 3 else 0, ha="right" if len(groups) > 3 else "center", fontsize=6.5 if compact else 8)
    axis.set_ylabel(str(summary.get("quantitation_unit") or "Source abundance"), fontsize=7 if compact else 9)
    axis.tick_params(axis="y", labelsize=7 if compact else 8)
    axis.grid(axis="y", color="#E5E7EB", linewidth=0.6)
    axis.spines[["top", "right"]].set_visible(False)
    title = str(summary.get("short_label") or summary.get("dataset_name") or "Response").replace("\n", " · ")
    axis.set_title(title, fontsize=8 if compact else 10, weight="bold", pad=7)


def _figure_outputs(figure: plt.Figure) -> dict[str, bytes]:
    outputs: dict[str, bytes] = {}
    for extension, dpi in (("svg", None), ("pdf", None), ("png", 600)):
        buffer = io.BytesIO()
        figure.savefig(buffer, format=extension, dpi=dpi, facecolor="white", bbox_inches="tight")
        outputs[extension] = buffer.getvalue()
    plt.close(figure)
    return outputs


def _individual_response_bytes(summary: dict[str, object]) -> dict[str, bytes]:
    figure, axis = plt.subplots(figsize=(7.2, 4.5))
    _draw_response_axis(axis, summary)
    figure.text(
        0.01,
        0.01,
        "Dots: biological replicates; black symbol: mean ± SD. Values retain the source unit and are not converted to log2FC.",
        fontsize=7.5,
        color="#4B5563",
    )
    figure.subplots_adjust(left=0.12, right=0.98, bottom=0.25, top=0.84)
    return _figure_outputs(figure)


def _multipanel_response_bytes(
    summaries: list[dict[str, object]],
    *,
    locus: str,
    category: str,
) -> dict[str, bytes]:
    if not summaries:
        return {}
    columns = 2
    rows = int(np.ceil(len(summaries) / columns))
    figure, axes = plt.subplots(rows, columns, figsize=(11.2, max(4.2, rows * 3.0)), squeeze=False)
    flat_axes = list(axes.flat)
    for index, (axis, summary) in enumerate(zip(flat_axes, summaries)):
        _draw_response_axis(axis, summary, compact=True)
        axis.text(-0.12, 1.08, chr(65 + index), transform=axis.transAxes, fontsize=10, weight="bold", va="top")
    for axis in flat_axes[len(summaries):]:
        axis.set_axis_off()
    category_title = "Virus response" if category == "病毒" else "Insect feeding response"
    figure.suptitle(f"{category_title} · {locus}", fontsize=13, weight="bold", y=0.995)
    figure.text(
        0.5,
        0.008,
        "Each panel keeps its own source scale and unit. Dots show biological replicates; black symbols show mean ± SD.",
        ha="center",
        fontsize=8,
        color="#4B5563",
    )
    figure.subplots_adjust(left=0.09, right=0.98, bottom=0.08, top=0.94, hspace=0.58, wspace=0.28)
    return _figure_outputs(figure)


def _summary_table_frame(result: dict[str, object]) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for summary in result.get("dataset_summaries", []):
        groups = summary.get("replicate_groups", [])
        rows.append(
            {
                "msu_locus": summary.get("msu_locus"),
                "short_label": str(summary.get("short_label") or "").replace("\n", " | "),
                "display_tier": summary.get("display_tier"),
                "dataset_name": summary.get("dataset_name"),
                "accession": summary.get("accession"),
                "category": summary.get("category"),
                "assay": summary.get("assay"),
                "background": summary.get("background"),
                "treatment": summary.get("treatment"),
                "n_control": summary.get("n_control"),
                "n_treatment": summary.get("n_treatment"),
                "effect_value": summary.get("effect_value"),
                "pvalue": summary.get("pvalue"),
                "padj": summary.get("padj"),
                "quantitation_unit": summary.get("quantitation_unit"),
                "group_count": len(groups),
                "availability_note": summary.get("availability_note"),
            }
        )
    return pd.DataFrame(rows)


def _replicate_table_frame(summary: dict[str, object]) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for group in summary.get("replicate_groups", []):
        for sample_code, value in zip(group.get("sample_codes", []), group.get("replicate_values", [])):
            rows.append(
                {
                    "msu_locus": summary.get("msu_locus"),
                    "dataset_name": summary.get("dataset_name"),
                    "accession": summary.get("accession"),
                    "group": group.get("label"),
                    "condition_role": group.get("role"),
                    "sample_code": sample_code,
                    "value": value,
                    "unit": summary.get("quantitation_unit"),
                }
            )
    return pd.DataFrame(rows)


def build_lab_omics_artifacts(result: dict[str, object]) -> tuple[dict[str, bytes], dict[str, bytes]]:
    charts: dict[str, bytes] = {}
    raw_artifacts: dict[str, bytes] = {}
    matrix, plotting = cross_project_matrix(result)
    if not matrix.empty:
        annotations = plotting.pivot_table(
            index="msu_locus", columns="comparison_label", values="annotation", aggfunc="first", dropna=False
        )
        for extension, payload in _heatmap_bytes(
            matrix,
            "Multi-omics differential response",
            "Source log2FC (treatment / control)",
            annotations=annotations,
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
    summaries = [row for row in result.get("dataset_summaries", []) if row.get("replicate_groups")]
    for summary in summaries:
        safe_locus = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(summary.get("msu_locus") or "gene"))
        safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(summary.get("dataset_id") or "dataset"))
        for extension, payload in _individual_response_bytes(summary).items():
            charts[f"lab_omics/response_{safe_locus}_{safe_id}.{extension}"] = payload
        replicate_frame = _replicate_table_frame(summary)
        raw_artifacts[f"lab_omics/response_{safe_locus}_{safe_id}.csv"] = replicate_frame.to_csv(index=False).encode("utf-8-sig")
    for locus in result.get("msu_loci", []):
        for category, category_slug in (("病毒", "virus"), ("昆虫", "insect")):
            subset = [
                row for row in summaries
                if str(row.get("msu_locus")) == str(locus) and str(row.get("category")) == category
            ]
            if not subset:
                continue
            subset = sorted(subset, key=lambda row: (str(row.get("treatment") or ""), str(row.get("background") or "")))
            safe_locus = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(locus))
            for extension, payload in _multipanel_response_bytes(subset, locus=str(locus), category=category).items():
                charts[f"lab_omics/overview_{safe_locus}_{category_slug}_response.{extension}"] = payload
    summary_frame = _summary_table_frame(result)
    raw_artifacts["lab_omics/dataset_coverage_summary.csv"] = summary_frame.to_csv(index=False).encode("utf-8-sig")
    raw_artifacts["lab_omics/query_metadata.json"] = json.dumps(
        {
            "msu_loci": result.get("msu_loci", []),
            "database_schema": result.get("database_schema", ""),
            "cross_project_metric": "existing source log2FC; PTM overview uses max absolute site response",
            "within_project_metric": "existing FPKM/TPM/count/normalized protein or PTM quantitation; row z-score only within dataset",
            "column_clustering": False,
            "missing_color": "#D1D5DB",
            "color_bounds": "symmetric 99th percentile of absolute matrix values",
            "display_tiers": DISPLAY_TIER_LABELS,
            "response_figures": "source abundance with all available biological replicates and mean ± SD; each panel has an independent source scale",
        },
        ensure_ascii=False,
        indent=2,
    ).encode("utf-8")
    return charts, raw_artifacts


__all__ = [
    "LabOmicsUnavailable",
    "build_lab_omics_artifacts",
    "canonical_msu_loci",
    "build_dataset_summaries",
    "cross_project_matrix",
    "project_profile_matrices",
    "query_lab_omics",
    "resolve_database_path",
]
