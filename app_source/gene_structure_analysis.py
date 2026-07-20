"""IRGSP-1.0 gene-model retrieval and transcript-oriented visualization."""

from __future__ import annotations

from collections import defaultdict
import io
from typing import Callable, Iterable
from urllib.parse import quote

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch, Rectangle
import requests

from plot_style import CVD_PALETTE, INK, LIGHT, MUTED, add_axis_title, publication_context, style_axis


ENSEMBL_REST_URL = "https://rest.ensembl.org"
EXPECTED_ASSEMBLY = "IRGSP-1.0"
SOURCE_URL = "https://rest.ensembl.org/"


def _as_int(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def parse_gene_model(
    gene: dict[str, object],
    input_id: str,
    transcript_scope: str,
    source_url: str,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Normalize one expanded Ensembl lookup response without mixing assemblies."""
    assembly = str(gene.get("assembly_name") or "")
    if assembly != EXPECTED_ASSEMBLY:
        raise ValueError(f"注释版本为 {assembly or '未知'}，预期 {EXPECTED_ASSEMBLY}。")
    gene_id = str(gene.get("id") or "")
    gene_start = _as_int(gene.get("start"))
    gene_end = _as_int(gene.get("end"))
    strand = _as_int(gene.get("strand"))
    if not gene_id or gene_start is None or gene_end is None or strand not in {1, -1}:
        raise ValueError("Ensembl gene model 缺少 ID、坐标或链方向。")

    transcripts = [item for item in gene.get("Transcript", []) if isinstance(item, dict)]
    canonical = str(gene.get("canonical_transcript") or "").split(".", 1)[0]
    if transcript_scope.startswith("仅 canonical") or transcript_scope.startswith("Canonical"):
        chosen = [
            item
            for item in transcripts
            if str(item.get("id") or "").split(".", 1)[0] == canonical or bool(item.get("is_canonical"))
        ]
        transcripts = chosen[:1] or transcripts[:1]

    transcript_rows: list[dict[str, object]] = []
    feature_rows: list[dict[str, object]] = []
    for transcript in transcripts:
        transcript_id = str(transcript.get("id") or "")
        tx_start = _as_int(transcript.get("start"))
        tx_end = _as_int(transcript.get("end"))
        translation = transcript.get("Translation") if isinstance(transcript.get("Translation"), dict) else {}
        protein_length = _as_int(translation.get("length"))
        is_canonical = (
            bool(transcript.get("is_canonical"))
            or str(transcript_id).split(".", 1)[0] == canonical
        )
        transcript_rows.append(
            {
                "input_id": input_id,
                "rap_gene": gene_id,
                "transcript_id": transcript_id,
                "is_canonical": is_canonical,
                "biotype": str(transcript.get("biotype") or ""),
                "chromosome": str(gene.get("seq_region_name") or ""),
                "gene_start": gene_start,
                "gene_end": gene_end,
                "transcript_start": tx_start,
                "transcript_end": tx_end,
                "strand": strand,
                "assembly": assembly,
                "translation_id": str(translation.get("id") or ""),
                "protein_length_aa": protein_length,
                "source_url": source_url,
                "status": "matched",
                "error": "",
            }
        )

        exons = [item for item in transcript.get("Exon", []) if isinstance(item, dict)]
        exons.sort(key=lambda item: int(item.get("start", 0)), reverse=strand == -1)
        for exon_number, exon in enumerate(exons, start=1):
            start = _as_int(exon.get("start"))
            end = _as_int(exon.get("end"))
            if start is None or end is None:
                continue
            feature_rows.append(
                {
                    "input_id": input_id,
                    "rap_gene": gene_id,
                    "transcript_id": transcript_id,
                    "feature_type": "exon",
                    "feature_id": str(exon.get("id") or ""),
                    "feature_number": exon_number,
                    "start": start,
                    "end": end,
                    "strand": strand,
                    "assembly": assembly,
                    "source_url": source_url,
                }
            )

        utrs = [item for item in transcript.get("UTR", []) if isinstance(item, dict)]
        for index, utr in enumerate(utrs, start=1):
            start = _as_int(utr.get("start"))
            end = _as_int(utr.get("end"))
            if start is None or end is None:
                continue
            raw_type = str(utr.get("type") or utr.get("object_type") or "UTR").casefold()
            feature_type = "5UTR" if "five" in raw_type or "5" in raw_type else "3UTR" if "three" in raw_type or "3" in raw_type else "UTR"
            feature_rows.append(
                {
                    "input_id": input_id,
                    "rap_gene": gene_id,
                    "transcript_id": transcript_id,
                    "feature_type": feature_type,
                    "feature_id": f"{transcript_id}:{feature_type}:{index}",
                    "feature_number": index,
                    "start": start,
                    "end": end,
                    "strand": strand,
                    "assembly": assembly,
                    "source_url": source_url,
                }
            )

        cds_start = _as_int(translation.get("start"))
        cds_end = _as_int(translation.get("end"))
        if cds_start is not None and cds_end is not None:
            low, high = sorted((cds_start, cds_end))
            cds_number = 0
            for exon in exons:
                exon_start = _as_int(exon.get("start"))
                exon_end = _as_int(exon.get("end"))
                if exon_start is None or exon_end is None:
                    continue
                start = max(low, exon_start)
                end = min(high, exon_end)
                if start > end:
                    continue
                cds_number += 1
                feature_rows.append(
                    {
                        "input_id": input_id,
                        "rap_gene": gene_id,
                        "transcript_id": transcript_id,
                        "feature_type": "CDS",
                        "feature_id": str(translation.get("id") or ""),
                        "feature_number": cds_number,
                        "start": start,
                        "end": end,
                        "strand": strand,
                        "assembly": assembly,
                        "source_url": source_url,
                    }
                )
    return transcript_rows, feature_rows


def fetch_gene_models(
    targets: Iterable[tuple[str, str]],
    transcript_scope: str,
    progress_callback: Callable[[int, int, str], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    session: requests.Session | None = None,
) -> tuple[list[dict[str, object]], list[dict[str, object]], list[str]]:
    unique = list(dict.fromkeys((str(input_id), str(gene_id)) for input_id, gene_id in targets if gene_id))
    transcript_rows: list[dict[str, object]] = []
    feature_rows: list[dict[str, object]] = []
    warnings: list[str] = []
    client = session or requests.Session()
    client.headers.update({"Accept": "application/json", "User-Agent": "MyBioTools/1.9.8"})
    for index, (input_id, gene_id) in enumerate(unique, start=1):
        if cancel_check and cancel_check():
            break
        url = f"{ENSEMBL_REST_URL}/lookup/id/{quote(gene_id, safe='')}"
        try:
            response = client.get(url, params={"expand": 1, "utr": 1}, timeout=(5, 35))
            response.raise_for_status()
            rows, features = parse_gene_model(response.json(), input_id, transcript_scope, response.url)
            transcript_rows.extend(rows)
            feature_rows.extend(features)
        except Exception as exc:
            warnings.append(f"{gene_id} 基因结构获取失败：{type(exc).__name__}: {exc}")
            transcript_rows.append(
                {
                    "input_id": input_id,
                    "rap_gene": gene_id,
                    "transcript_id": "",
                    "assembly": EXPECTED_ASSEMBLY,
                    "source_url": url,
                    "status": "failed",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
        if progress_callback:
            progress_callback(index, len(unique), gene_id)
    return transcript_rows, feature_rows, warnings


def _figure_bytes(figure) -> tuple[bytes, bytes, bytes]:
    svg = io.BytesIO()
    pdf = io.BytesIO()
    png = io.BytesIO()
    figure.savefig(svg, format="svg", bbox_inches="tight", facecolor="white")
    figure.savefig(png, format="png", dpi=600, bbox_inches="tight", facecolor="white")
    try:
        figure.savefig(pdf, format="pdf", bbox_inches="tight", facecolor="white")
    except (ImportError, ModuleNotFoundError):
        pdf = io.BytesIO()
    plt.close(figure)
    return svg.getvalue(), pdf.getvalue(), png.getvalue()


def build_gene_structure_artifacts(
    transcript_rows: list[dict[str, object]],
    feature_rows: list[dict[str, object]],
) -> dict[str, bytes]:
    """Render compact transcript tracks with every gene displayed 5-prime to 3-prime."""
    artifacts: dict[str, bytes] = {}
    by_gene: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in transcript_rows:
        if row.get("status") == "matched" and row.get("transcript_id"):
            by_gene[str(row.get("rap_gene"))].append(row)
    feature_lookup: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
    for row in feature_rows:
        feature_lookup[(str(row.get("rap_gene")), str(row.get("transcript_id")))].append(row)

    colors = {
        "exon": "#B8C2CC",
        "CDS": CVD_PALETTE[2],
        "5UTR": CVD_PALETTE[4],
        "3UTR": CVD_PALETTE[3],
        "UTR": "#7A8794",
    }
    for gene_id, transcripts in by_gene.items():
        transcripts.sort(key=lambda row: (not bool(row.get("is_canonical")), str(row.get("transcript_id"))))
        gene_start = int(transcripts[0]["gene_start"])
        gene_end = int(transcripts[0]["gene_end"])
        strand = int(transcripts[0]["strand"])
        width = max(1, gene_end - gene_start + 1)
        height = max(2.4, 0.62 * len(transcripts) + 1.5)
        with publication_context():
            figure, axis = plt.subplots(figsize=(7.4, height), constrained_layout=True)
            y_positions: list[int] = []
            y_labels: list[str] = []
            for index, transcript in enumerate(transcripts):
                y = len(transcripts) - index
                if index % 2 == 0:
                    axis.axhspan(y - 0.4, y + 0.4, color=LIGHT, zorder=0)
                axis.hlines(y, 1, width, color="#7A8794", linewidth=0.75, zorder=1)
                tx_id = str(transcript["transcript_id"])
                y_positions.append(y)
                y_labels.append(tx_id + ("  · canonical" if transcript.get("is_canonical") else ""))
                for feature in feature_lookup.get((gene_id, tx_id), []):
                    start = int(feature["start"])
                    end = int(feature["end"])
                    if strand == 1:
                        display_start, display_end = start - gene_start + 1, end - gene_start + 1
                    else:
                        display_start, display_end = gene_end - end + 1, gene_end - start + 1
                    kind = str(feature.get("feature_type") or "exon")
                    feature_height = 0.28 if kind == "exon" else 0.46 if kind == "CDS" else 0.20
                    axis.add_patch(
                        Rectangle(
                            (display_start, y - feature_height / 2),
                            max(1, display_end - display_start + 1),
                            feature_height,
                            facecolor=colors.get(kind, "#6B7280"),
                            edgecolor="white" if kind != "exon" else "#667085",
                            linewidth=0.45,
                            zorder=3 if kind != "exon" else 2,
                        )
                    )
            axis.set_xlim(0, width)
            axis.set_ylim(0.4, len(transcripts) + 1.1)
            axis.set_yticks(y_positions, y_labels)
            axis.set_xlabel("Transcript-oriented position (bp; 5′ → 3′)")
            add_axis_title(
                axis,
                gene_id,
                f"Transcript models  ·  {EXPECTED_ASSEMBLY}  ·  displayed 5′ → 3′",
            )
            style_axis(axis, grid_axis="x")
            axis.tick_params(axis="y", labelsize=7.0, colors=INK)
            axis.legend(
                handles=[Patch(facecolor=colors[key], label=key) for key in ("exon", "CDS", "5UTR", "3UTR")],
                frameon=False,
                ncol=4,
                loc="upper right",
                bbox_to_anchor=(1, 1.11),
                labelcolor=MUTED,
                columnspacing=0.9,
                handlelength=1.2,
            )
            svg, pdf, png = _figure_bytes(figure)
        stem = gene_id.replace("/", "_")
        artifacts[f"gene_structure_{stem}.svg"] = svg
        if pdf:
            artifacts[f"gene_structure_{stem}.pdf"] = pdf
        artifacts[f"gene_structure_{stem}.png"] = png
    return artifacts


__all__ = [
    "EXPECTED_ASSEMBLY",
    "SOURCE_URL",
    "build_gene_structure_artifacts",
    "fetch_gene_models",
    "parse_gene_model",
]
