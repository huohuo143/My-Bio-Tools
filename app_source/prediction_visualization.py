"""Publication-ready protein localization prediction figures."""

from __future__ import annotations

import io
from collections import defaultdict

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch, Rectangle

from plot_style import CVD_PALETTE, GRID, INK, LIGHT, MUTED, OTHER, PUBLICATION_RC, add_axis_title, style_axis
from rice_gene_core import PredictionRegion, PredictionResult, safe_file_stem

plt.rcParams.update(PUBLICATION_RC)


COLORS = {
    "signal peptide": CVD_PALETTE[4],
    "signal": CVD_PALETTE[4],
    "n-region": CVD_PALETTE[5],
    "h-region": CVD_PALETTE[2],
    "c-region": CVD_PALETTE[3],
    "tm helix": CVD_PALETTE[0],
    "transmembrane": CVD_PALETTE[0],
    "beta strand": CVD_PALETTE[1],
    "inside": "#8DA0AE",
    "outside": "#44AA99",
    "ctp": "#009E73",
    "mtp": "#0072B2",
    "ltp": "#CC79A7",
    "nls": "#AA4499",
}
DEFAULT_COLOR = OTHER
SUCCESS = {"matched", "partial"}
STATUS_STYLES = {
    "detected": ("#DCFCE7", "#166534"),
    "not_detected": ("#EAF2F8", "#24556F"),
    "unresolved": ("#FFF3D6", "#8A5A00"),
    "failed": ("#FDECEC", "#A61B29"),
}
PROVIDER_LABELS = {
    "biolib": "BioLib",
    "dtu_web": "DTU",
    "nls_mapper_web": "cNLS Mapper",
    "local": "Local",
}


def _region_color(region_type: str) -> str:
    label = region_type.casefold().replace("_", " ").replace("-", " ")
    for key, color in COLORS.items():
        if key in label:
            return color
    return DEFAULT_COLOR


def _figure_bytes(figure) -> tuple[bytes, bytes]:
    svg = io.BytesIO()
    png = io.BytesIO()
    figure.savefig(svg, format="svg", bbox_inches="tight", facecolor="white")
    figure.savefig(png, format="png", dpi=600, bbox_inches="tight", facecolor="white")
    plt.close(figure)
    return svg.getvalue(), png.getvalue()


def _protein_length(results: list[PredictionResult], known_length: int = 0) -> int:
    region_end = max((region.end for result in results for region in result.regions), default=0)
    return max(known_length, region_end, 1)


def _draw_region(axis, region: PredictionRegion, row: int, protein_length: int) -> None:
    start = max(1, int(region.start))
    end = max(start, int(region.end))
    axis.add_patch(
        Rectangle(
            (start, row - 0.22),
            max(1, end - start + 1),
            0.44,
            facecolor=_region_color(region.region_type),
            edgecolor="white",
            linewidth=0.5,
            zorder=3,
        )
    )
    label = region.region_type.replace("_", " ").replace("-", " ")
    if label.casefold() == "inside":
        label = "inside topology"
    if end - start + 1 >= max(16, protein_length * 0.10):
        axis.text(
            (start + end) / 2,
            row,
            label,
            ha="center",
            va="center",
            fontsize=6.2,
            color="white",
            fontweight="medium",
            clip_on=True,
            zorder=4,
        )


def _provider_label(provider: str) -> str:
    return PROVIDER_LABELS.get(provider, provider.replace("_", " ").title()) if provider else ""


def _result_display(result: PredictionResult) -> tuple[str, str]:
    """Return a concise interpretation label and visual state without raw errors."""
    if result.status not in SUCCESS:
        return "Service unavailable", "failed"

    tool = result.tool.casefold()
    classification = (result.classification or result.summary or "").strip()
    normalized = classification.casefold().replace("_", " ").replace("-", " ")
    region_labels = [region.region_type.casefold().replace("_", " ").replace("-", " ") for region in result.regions]

    if "signalp" in tool and normalized in {"other", "no sp", "no signal peptide"}:
        return "No signal peptide", "not_detected"
    if "tmhmm" in tool and ("no tm" in normalized or "glob" in normalized):
        return "No TM helix", "not_detected"
    if "nls" in tool and "no nls" in normalized:
        return "No NLS detected", "not_detected"
    if normalized in {"result returned", "returned", "matched"} and not result.regions:
        return "No parsed feature", "unresolved"

    positive_terms = ("signal", "tm helix", "transmembrane", "nls", "targeting peptide", "ctp", "mtp", "ltp")
    if any(any(term in label for term in positive_terms) for label in region_labels):
        return classification or "Feature detected", "detected"
    if result.regions and all(label in {"inside", "outside"} for label in region_labels):
        return "No TM helix", "not_detected"
    if result.regions:
        return classification or "Feature detected", "detected"
    if classification:
        return classification[:28], "unresolved"
    return "No positional feature", "unresolved"


def build_combined_figure(
    protein_id: str,
    results: list[PredictionResult],
    protein_length: int = 0,
):
    length = _protein_length(results, protein_length)
    height = max(3.4, 0.68 * len(results) + 1.8)
    figure, (tool_axis, track_axis, status_axis) = plt.subplots(
        1,
        3,
        figsize=(9.4, height),
        sharey=True,
        constrained_layout=True,
        gridspec_kw={"width_ratios": [2.1, 6.9, 2.6], "wspace": 0.02},
    )
    layout_engine = figure.get_layout_engine()
    if layout_engine is not None:
        layout_engine.set(rect=(0.02, 0.03, 0.98, 0.88), w_pad=0.03, h_pad=0.03)
    figure.patch.set_facecolor("white")
    for axis in (tool_axis, track_axis, status_axis):
        axis.set_facecolor("white")
    used_colors: dict[str, str] = {}
    states = []
    for row, result in enumerate(results):
        if row % 2 == 0:
            for axis in (tool_axis, track_axis, status_axis):
                axis.axhspan(row - 0.48, row + 0.48, color=LIGHT, zorder=0)

        tool_axis.text(0.98, row, result.tool, ha="right", va="center", fontsize=8.0, fontweight="semibold", color=INK)
        track_axis.hlines(row, 1, length, color="#CBD5E1", linewidth=1.8, zorder=1)
        if result.status in SUCCESS and result.regions:
            for region in result.regions:
                _draw_region(track_axis, region, row, length)
                used_colors[region.region_type.replace("_", " ")] = _region_color(region.region_type)
                region_label = region.region_type.casefold().replace("_", " ")
                if region.end and ("signal peptide" in region_label or "targeting peptide" in region_label):
                    position = float(region.end) + 0.5
                    track_axis.vlines(position, row - 0.30, row + 0.30, color="#A61B29", linewidth=1.0, zorder=5)
                    track_axis.text(position, row - 0.32, "CS", ha="center", va="bottom", fontsize=5.8, color="#A61B29")

        label, state = _result_display(result)
        states.append(state)
        fill, text_color = STATUS_STYLES[state]
        status_axis.text(
            0.04,
            row - 0.09,
            label,
            ha="left",
            va="center",
            fontsize=7.1,
            color=text_color,
            fontweight="semibold",
            bbox={"boxstyle": "round,pad=0.28", "facecolor": fill, "edgecolor": "none"},
        )
        provider = _provider_label(result.provider)
        if provider:
            status_axis.text(0.04, row + 0.26, f"Source · {provider}", ha="left", va="center", fontsize=5.9, color="#667085")

    row_count = max(1, len(results))
    for axis in (tool_axis, track_axis, status_axis):
        axis.set_ylim(row_count - 0.5, -0.5)
        axis.set_yticks([])
    tool_axis.set_xlim(0, 1)
    status_axis.set_xlim(0, 1)
    track_axis.set_xlim(1, length)
    track_axis.set_xlabel("Amino-acid position")
    tool_axis.set_title("Prediction tool", loc="right", fontweight="bold", color="#475569", pad=7)
    status_axis.set_title("Interpretation", loc="left", fontweight="bold", color="#475569", pad=7)
    track_axis.set_title(f"Protein sequence · {length} aa", loc="left", fontweight="bold", color="#475569", pad=7)
    for axis in (tool_axis, status_axis):
        axis.axis("off")
    track_axis.spines[["top", "right", "left"]].set_visible(False)
    track_axis.tick_params(axis="y", length=0)
    track_axis.grid(axis="x", color=GRID, linewidth=0.6, zorder=0)
    completed = sum(state in {"detected", "not_detected"} for state in states)
    unavailable = states.count("failed")
    unresolved = states.count("unresolved")
    figure.suptitle(
        f"Protein localization prediction · {protein_id}",
        x=0.02,
        y=0.98,
        ha="left",
        fontsize=10.8,
        fontweight="bold",
        color=INK,
    )
    figure.text(
        0.02,
        0.925,
        f"{completed} interpreted  ·  {unavailable} unavailable  ·  {unresolved} unresolved",
        ha="left",
        va="top",
        fontsize=7.2,
        color=MUTED,
    )
    if len(used_colors) > 1:
        handles = [Patch(facecolor=color, label=label) for label, color in sorted(used_colors.items())]
        track_axis.legend(
            handles=handles,
            loc="upper center",
            bbox_to_anchor=(0.5, -0.16),
            ncol=min(5, len(handles)),
            frameon=False,
        )
    return figure


def build_probability_figure(result: PredictionResult):
    labels = list(result.probabilities)
    values = [float(result.probabilities[label]) for label in labels]
    figure, axis = plt.subplots(figsize=(7.2, max(2.8, 0.40 * len(labels) + 1.6)), constrained_layout=True)
    figure.patch.set_facecolor("white")
    axis.set_facecolor("white")
    colors = [_region_color(label) for label in labels]
    axis.barh(range(len(labels)), values, color=colors, edgecolor="white", linewidth=0.5, height=0.56)
    axis.set_yticks(range(len(labels)), labels)
    axis.invert_yaxis()
    maximum = max(values, default=1.0)
    axis.set_xlim(0, 1.0 if maximum <= 1.0 else maximum * 1.12)
    axis.set_xlabel("Probability" if maximum <= 1.0 else "Score")
    add_axis_title(axis, result.protein_id, f"{result.tool} prediction scores")
    for row, value in enumerate(values):
        axis.text(value + maximum * 0.015, row, f"{value:.3g}", va="center", fontsize=6.7, color=MUTED)
    style_axis(axis, grid_axis="x")
    return figure


def build_prediction_chart_artifacts(
    predictions: list[PredictionResult],
    protein_sequences: dict[str, str] | None = None,
) -> dict[str, bytes]:
    """Return per-protein combined tracks and available score charts as SVG/600-dpi PNG."""
    artifacts: dict[str, bytes] = {}
    grouped: dict[str, list[PredictionResult]] = defaultdict(list)
    for result in predictions:
        grouped[result.protein_id].append(result)
    for protein_id, results in grouped.items():
        stem = safe_file_stem(protein_id, "protein")
        length = len((protein_sequences or {}).get(protein_id, ""))
        svg, png = _figure_bytes(build_combined_figure(protein_id, results, length))
        artifacts[f"combined_{stem}.svg"] = svg
        artifacts[f"combined_{stem}.png"] = png
        for result in results:
            if not result.probabilities:
                continue
            score_stem = f"scores_{stem}_{safe_file_stem(result.tool, 'tool')}"
            score_svg, score_png = _figure_bytes(build_probability_figure(result))
            artifacts[f"{score_stem}.svg"] = score_svg
            artifacts[f"{score_stem}.png"] = score_png
    return artifacts


__all__ = [
    "build_combined_figure",
    "build_prediction_chart_artifacts",
    "build_probability_figure",
]
