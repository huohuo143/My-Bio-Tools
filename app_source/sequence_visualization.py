"""Publication-style relationship diagrams for rice gene sequence assets."""

from __future__ import annotations

import csv
import io
from collections import defaultdict

from rice_gene_core import AnalysisBundle, CDS, PROTEIN, safe_file_stem, translate_cds


TYPE_ORDER = {"Promoter": 0, "Gene genomic": 1, "5′UTR": 2, "CDS": 3, "3′UTR": 4, "Protein": 5}
TYPE_COLORS = {
    "Promoter": "#8E6C8A",
    "Gene genomic": "#3A7D8C",
    "5′UTR": "#84A98C",
    "CDS": "#E07A5F",
    "3′UTR": "#84A98C",
    "Protein": "#D4A72C",
}


def build_sequence_plot_rows(bundle: AnalysisBundle) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    proteins_by_transcript: dict[str, list[object]] = defaultdict(list)
    for record in bundle.sequences:
        if record.sequence_type == PROTEIN:
            proteins_by_transcript[record.transcript_id or record.input_id].append(record)
    for record in bundle.sequences:
        translation_status = "not_applicable"
        if record.sequence_type == CDS and record.sequence:
            translated, errors = translate_cds(record.sequence)
            proteins = proteins_by_transcript.get(record.transcript_id or record.input_id, [])
            if errors:
                translation_status = "invalid_cds"
            elif proteins:
                translation_status = "consistent" if any(item.sequence.rstrip("*") == translated for item in proteins) else "protein_differs"
            else:
                translation_status = "translated_protein_not_selected"
        rows.append({
            "input_id": record.input_id,
            "rap_gene": record.resolved_rap_gene,
            "msu_id": record.resolved_msu_id,
            "transcript_id": record.transcript_id,
            "sequence_type": record.sequence_type,
            "length": record.length,
            "unit": "aa" if record.sequence_type == PROTEIN else "nt",
            "source": record.source,
            "assembly": record.assembly,
            "coordinates": record.coordinates,
            "status": record.status,
            "translation_consistency": translation_status,
            "validation_note": record.validation_note,
        })
    return rows


def _csv_bytes(rows: list[dict[str, object]]) -> bytes:
    if not rows:
        return b""
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8-sig")


def _figure_bytes(fig) -> dict[str, bytes]:
    payloads: dict[str, bytes] = {}
    for extension, kwargs in (
        ("png", {"dpi": 600}),
        ("svg", {}),
        ("pdf", {}),
    ):
        output = io.BytesIO()
        fig.savefig(output, format=extension, bbox_inches="tight", facecolor="white", **kwargs)
        payloads[extension] = output.getvalue()
    return payloads


def _mapping_for_input(bundle: AnalysisBundle, input_id: str) -> dict[str, object]:
    return next((row for row in bundle.mapping_rows if str(row.get("input_id") or "") == input_id), {})


def build_sequence_relationship_figure(bundle: AnalysisBundle, input_id: str, rows: list[dict[str, object]]):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import FancyBboxPatch

    mapping = _mapping_for_input(bundle, input_id)
    ricedata = next((row for row in bundle.ricedata_rows if str(row.get("RAP_Locus") or "") == str(mapping.get("resolved_rap_gene") or "")), {})
    records = sorted(rows, key=lambda row: (TYPE_ORDER.get(str(row.get("sequence_type")), 99), str(row.get("source"))))[:14]
    with plt.rc_context({"font.family": "Arial", "pdf.fonttype": 42, "ps.fonttype": 42, "axes.unicode_minus": False}):
        fig = plt.figure(figsize=(7.15, max(4.3, 0.34 * len(records) + 2.6)), constrained_layout=True)
        grid = fig.add_gridspec(2, 1, height_ratios=[1.0, max(2.2, 0.33 * len(records))], hspace=0.08)
        identity = fig.add_subplot(grid[0])
        tracks = fig.add_subplot(grid[1])
        identity.axis("off")
        boxes = [
            (0.02, "Input", input_id, "#EDF4F3"),
            (0.35, "RAP", str(mapping.get("resolved_rap_gene") or ricedata.get("RAP_Locus") or "not resolved"), "#DDEFEA"),
            (0.68, "MSU / symbol", " / ".join(value for value in (str(mapping.get("resolved_msu_id") or ricedata.get("MSU_Locus") or ""), str(ricedata.get("GeneSymbol") or "")) if value) or "not resolved", "#F4EBD8"),
        ]
        for x, label, value, fill in boxes:
            patch = FancyBboxPatch((x, 0.28), 0.27, 0.48, boxstyle="round,pad=0.012,rounding_size=0.025", linewidth=0.9, edgecolor="#607080", facecolor=fill, transform=identity.transAxes)
            identity.add_patch(patch)
            identity.text(x + 0.018, 0.64, label, fontsize=7.3, color="#5A6673", transform=identity.transAxes, va="top")
            identity.text(x + 0.018, 0.46, value, fontsize=8.9, fontweight="bold", color="#172033", transform=identity.transAxes, va="center")
        identity.annotate("", xy=(0.35, 0.52), xytext=(0.29, 0.52), xycoords="axes fraction", arrowprops={"arrowstyle": "->", "color": "#607080", "lw": 1.0})
        identity.annotate("", xy=(0.68, 0.52), xytext=(0.62, 0.52), xycoords="axes fraction", arrowprops={"arrowstyle": "->", "color": "#607080", "lw": 1.0})
        identity.text(0.02, 0.94, "Sequence identity and asset relationship", fontsize=11.2, fontweight="bold", color="#172033", transform=identity.transAxes, va="top")

        if not records:
            tracks.axis("off")
            tracks.text(0.5, 0.5, "No sequence assets available", ha="center", va="center", color="#667085")
            return fig
        max_nt = max((int(row["length"]) for row in records if row.get("unit") == "nt"), default=1)
        max_aa = max((int(row["length"]) for row in records if row.get("unit") == "aa"), default=1)
        for y, row in enumerate(records):
            length = int(row.get("length") or 0)
            denominator = max_aa if row.get("unit") == "aa" else max_nt
            width = max(0.02, 0.72 * length / max(denominator, 1))
            tracks.barh(y, width, left=0.0, height=0.58, color=TYPE_COLORS.get(str(row.get("sequence_type")), "#8291A3"), edgecolor="white", linewidth=0.7)
            tracks.text(width + 0.015, y, f"{length:,} {row.get('unit')}  ·  {row.get('source')}", va="center", fontsize=6.8, color="#344054")
        tracks.set_yticks(range(len(records)), [str(row.get("sequence_type") or "") for row in records])
        tracks.invert_yaxis()
        tracks.set_xlim(0, 1.22)
        tracks.set_xticks([])
        tracks.tick_params(axis="y", labelsize=7.2, length=0)
        for spine in tracks.spines.values():
            spine.set_visible(False)
        tracks.set_title("Available promoter / genomic / UTR / CDS / protein assets", loc="left", fontsize=9.2, fontweight="bold", color="#172033", pad=8)
        tracks.text(0, -0.15, "Bar lengths are normalized within nucleotide or protein classes; sources with different genomic spans are not overlaid.", transform=tracks.transAxes, fontsize=6.5, color="#667085", va="top")
        return fig


def build_batch_availability_figure(rows: list[dict[str, object]]):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    inputs = list(dict.fromkeys(str(row.get("input_id") or "") for row in rows))
    types = [key for key, _ in sorted(TYPE_ORDER.items(), key=lambda item: item[1])]
    matrix = np.zeros((len(inputs), len(types)), dtype=float)
    for row in rows:
        if row.get("status") == "matched" and row.get("sequence_type") in types:
            matrix[inputs.index(str(row.get("input_id") or "")), types.index(str(row.get("sequence_type")))] = 1
    with plt.rc_context({"font.family": "Arial", "pdf.fonttype": 42, "ps.fonttype": 42}):
        fig, ax = plt.subplots(figsize=(7.15, max(3.2, 0.34 * len(inputs) + 1.8)), constrained_layout=True)
        ax.imshow(matrix, cmap=plt.matplotlib.colors.ListedColormap(["#EEF1F4", "#2A8C82"]), vmin=0, vmax=1, aspect="auto")
        ax.set_xticks(range(len(types)), types, rotation=25, ha="right")
        ax.set_yticks(range(len(inputs)), inputs)
        ax.set_title("Sequence asset availability", loc="left", fontsize=11, fontweight="bold", color="#172033")
        ax.tick_params(length=0, labelsize=7.5)
        for spine in ax.spines.values():
            spine.set_visible(False)
        return fig


def build_sequence_relationship_artifacts(bundle: AnalysisBundle) -> tuple[list[dict[str, object]], dict[str, bytes], bytes]:
    """Return plot data, SVG/PDF/600-dpi PNG figures, and a plotting-data CSV."""
    import matplotlib.pyplot as plt

    rows = build_sequence_plot_rows(bundle)
    artifacts: dict[str, bytes] = {}
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("input_id") or "input")].append(row)
    for input_id, input_rows in grouped.items():
        fig = build_sequence_relationship_figure(bundle, input_id, input_rows)
        try:
            for extension, payload in _figure_bytes(fig).items():
                artifacts[f"sequence_relationship_{safe_file_stem(input_id)}.{extension}"] = payload
        finally:
            plt.close(fig)
    if len(grouped) > 1:
        fig = build_batch_availability_figure(rows)
        try:
            for extension, payload in _figure_bytes(fig).items():
                artifacts[f"sequence_availability_matrix.{extension}"] = payload
        finally:
            plt.close(fig)
    return rows, artifacts, _csv_bytes(rows)


__all__ = ["build_sequence_plot_rows", "build_sequence_relationship_artifacts"]
