"""PlantRegMap promoter TFBS prediction and publication-ready tracks."""

from __future__ import annotations

import io
import json
import re
from collections import Counter
from datetime import datetime, timezone

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import requests
from bs4 import BeautifulSoup

from plot_style import CVD_PALETTE, INK, LIGHT, MUTED, OTHER, add_axis_title, publication_context, style_axis


PLANTREGMAP_URL = "https://plantregmap.gao-lab.org/binding_site_prediction.php"
SPECIES = "Oryza_sativa_subsp._japonica"


def _json_array_after_data(html: str) -> list[list[object]]:
    match = re.search(r'["\']data["\']\s*:\s*', html)
    if not match:
        return []
    start = html.find("[", match.end())
    if start < 0:
        return []
    depth = 0
    quote = ""
    escaped = False
    for index in range(start, len(html)):
        char = html[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            continue
        if char in {'"', "'"}:
            quote = char
        elif char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                payload = re.sub(r",\s*]", "]", html[start:index + 1])
                try:
                    return json.loads(payload)
                except json.JSONDecodeError:
                    return []
    return []


def parse_plantregmap_html(
    html: str,
    *,
    input_id: str,
    rap_gene: str,
    transcript_id: str,
    promoter_length: int,
    pvalue: float,
    source_url: str = PLANTREGMAP_URL,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    queried_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    hits: list[dict[str, object]] = []
    for raw in _json_array_after_data(html):
        if len(raw) < 8:
            continue
        motif_model = str(raw[0])
        motif, _, model = motif_model.partition("##")
        position = str(raw[3])
        coordinates = [int(value) for value in re.findall(r"\d+", position)[:2]]
        if len(coordinates) != 2:
            continue
        start, end = coordinates
        try:
            observed_p = float(raw[5])
        except (TypeError, ValueError):
            observed_p = None
        hits.append({
            "input_id": input_id, "rap_gene": rap_gene, "transcript_id": transcript_id,
            "assembly": "IRGSP-1.0", "motif": motif, "tf_model": model,
            "tf": motif, "tf_family": str(raw[1]), "sequence_id": str(raw[2]),
            "start": start, "end": end, "strand": str(raw[4]),
            "relative_start": start - promoter_length - 1,
            "relative_end": end - promoter_length - 1,
            "p_value": observed_p, "q_value": raw[6], "matched_sequence": str(raw[7]),
            "evidence_class": "motif-based prediction", "status": "matched",
            "source_url": source_url, "queried_at": queried_at,
            "parameters": json.dumps({"species": SPECIES, "p_value": pvalue, "promoter_length": promoter_length}, ensure_ascii=False),
            "error": "",
        })
    grouped: dict[tuple[str, str], list[dict[str, object]]] = {}
    for hit in hits:
        grouped.setdefault((str(hit["tf"]), str(hit["tf_family"])), []).append(hit)
    candidates = []
    for (tf, family), rows in grouped.items():
        p_values = [float(row["p_value"]) for row in rows if row.get("p_value") is not None]
        candidates.append({
            "input_id": input_id, "rap_gene": rap_gene, "transcript_id": transcript_id,
            "tf": tf, "tf_family": family, "hit_count": len(rows),
            "best_p_value": min(p_values) if p_values else "",
            "nearest_tss_bp": min(abs(int(row["relative_end"])) for row in rows),
            "evidence_class": "motif-based prediction", "source_url": source_url,
            "queried_at": queried_at, "status": "predicted", "error": "",
        })
    candidates.sort(key=lambda row: (-int(row["hit_count"]), float(row["best_p_value"] or 1), int(row["nearest_tss_bp"])))
    for rank, row in enumerate(candidates, 1):
        row["rank"] = rank
    return hits, candidates


def predict_tfbs(promoters: list[dict[str, object]], pvalue: float = 1e-4, session: requests.Session | None = None) -> tuple[list[dict[str, object]], list[dict[str, object]], dict[str, bytes], list[str]]:
    client = session or requests.Session()
    all_hits, all_candidates, raw, warnings = [], [], {}, []
    for item in promoters:
        sequence = str(item.get("sequence") or "").upper()
        transcript = str(item.get("transcript_id") or item.get("input_id") or "query")
        if not sequence:
            warnings.append(f"{transcript} 无可用 promoter 序列，未运行 PlantRegMap。")
            continue
        fasta = f">{transcript}\n{sequence}\n".encode()
        try:
            landing = client.get(PLANTREGMAP_URL, timeout=30)
            landing.raise_for_status()
            form = BeautifulSoup(landing.text, "html.parser").find("form", id="main_input_form")
            action = str(form.get("action") or PLANTREGMAP_URL) if form is not None else PLANTREGMAP_URL
            if not action.startswith("http"):
                action = requests.compat.urljoin(PLANTREGMAP_URL, action)
            response = client.post(action, data={"species": SPECIES, "input_seq1": "", "pvalue1": f"{pvalue:g}", "prediction": "Prediction"}, files={"input_file1": ("promoter.fa", fasta, "text/plain")}, timeout=90)
            response.raise_for_status()
            raw[f"{transcript}/plantregmap_result.html"] = response.content
            hits, candidates = parse_plantregmap_html(response.text, input_id=str(item.get("input_id") or transcript), rap_gene=str(item.get("rap_gene") or ""), transcript_id=transcript, promoter_length=len(sequence), pvalue=pvalue, source_url=response.url)
            all_hits.extend(hits); all_candidates.extend(candidates)
            if not hits:
                warnings.append(f"{transcript} PlantRegMap 未返回可解析 TFBS（可能为无命中或页面格式变化）。")
        except Exception as exc:
            warnings.append(f"{transcript} PlantRegMap 失败：{type(exc).__name__}: {exc}")
    return all_hits, all_candidates, raw, warnings


def build_tfbs_artifacts(rows: list[dict[str, object]]) -> dict[str, bytes]:
    artifacts: dict[str, bytes] = {}
    by_transcript: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        by_transcript.setdefault(str(row.get("transcript_id") or "query"), []).append(row)
    for transcript, hits in by_transcript.items():
        family_counts = Counter(str(row.get("tf_family") or "Unknown") for row in hits)
        top_families = [name for name, _ in family_counts.most_common(9)]
        colors = {family: CVD_PALETTE[index % len(CVD_PALETTE)] for index, family in enumerate(top_families)}
        colors["Other"] = OTHER
        with publication_context():
            fig, (track_ax, summary_ax) = plt.subplots(
                2,
                1,
                figsize=(7.4, 4.65),
                constrained_layout=True,
                gridspec_kw={"height_ratios": [1.25, 1], "hspace": 0.12},
            )
            track_ax.set_facecolor("white")
            track_ax.axhline(0, color="#7A8794", lw=1.3)
            for row in hits:
                x = (int(row["relative_start"]) + int(row["relative_end"])) / 2
                family = str(row.get("tf_family") or "Unknown")
                color = colors.get(family, colors["Other"])
                track_ax.vlines(x, 0, 0.62, color=color, lw=0.8, alpha=0.82)
                track_ax.scatter([x], [0.62], s=22, color=color, edgecolor="white", linewidth=0.45, zorder=3)
            track_ax.axvspan(-500, 0, color="#FFF3D6", alpha=0.75, zorder=0)
            track_ax.axvline(0, color=CVD_PALETTE[1], ls="--", lw=1.0)
            track_ax.text(0, 0.76, "TSS", color=CVD_PALETTE[1], ha="center", va="bottom", fontsize=7.2, fontweight="bold")
            track_ax.set_xlabel("Position relative to TSS (bp)")
            track_ax.set_yticks([])
            track_ax.set_ylim(-0.06, 0.9)
            add_axis_title(
                track_ax,
                transcript,
                "Promoter TFBS landscape  ·  motif-based prediction, not experimental validation",
            )
            style_axis(track_ax, grid_axis="x")

            displayed = family_counts.most_common(10)
            labels = [name for name, _ in displayed][::-1]
            values = [count for _, count in displayed][::-1]
            summary_ax.barh(
                labels,
                values,
                color=[colors.get(name, colors["Other"]) for name in labels],
                height=0.56,
                edgecolor="white",
                linewidth=0.5,
            )
            summary_ax.set_xlabel("Predicted TFBS count")
            summary_ax.set_title("Most represented TF families", loc="left", fontsize=8.6, color=INK, pad=7)
            style_axis(summary_ax, grid_axis="x")
            maximum = max(values or [1])
            summary_ax.set_xlim(0, maximum * 1.13)
            for index, value in enumerate(values):
                summary_ax.text(value + maximum * 0.022, index, str(value), va="center", fontsize=6.5, color=MUTED)
        stem = re.sub(r"[^A-Za-z0-9._-]+", "_", transcript)
        for suffix, fmt, dpi in (("svg", "svg", None), ("png", "png", 600), ("pdf", "pdf", None)):
            try:
                buffer = io.BytesIO()
                fig.savefig(buffer, format=fmt, dpi=dpi, bbox_inches="tight", facecolor="white")
                artifacts[f"tfbs_{stem}.{suffix}"] = buffer.getvalue()
            except (ImportError, ModuleNotFoundError):
                if fmt != "pdf":
                    raise
        plt.close(fig)
    return artifacts
