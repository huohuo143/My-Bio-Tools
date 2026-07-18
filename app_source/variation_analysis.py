"""IRGSP-aware VCF annotation and conservative haplotype summaries."""

from __future__ import annotations

import gzip
import io
import csv
import re
from collections import Counter
from datetime import datetime, timezone

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from bs4 import BeautifulSoup
import requests

from plot_style import ACCENT, CVD_PALETTE, INK, LIGHT, MUTED, OTHER, PUBLICATION_RC, add_axis_title, style_axis

plt.rcParams.update(PUBLICATION_RC)


RICEVARMAP_V3_URL = "https://ricevarmap.ncpgr.cn/v3/"


def _decode(payload: bytes, filename: str) -> str:
    if filename.lower().endswith(".gz") or payload[:2] == b"\x1f\x8b":
        return gzip.decompress(payload).decode("utf-8", errors="replace")
    return payload.decode("utf-8", errors="replace")


def _info_map(text: str) -> dict[str, str]:
    result = {}
    for token in text.split(";"):
        key, separator, value = token.partition("=")
        result[key] = value if separator else "1"
    return result


def _consequence(info: dict[str, str]) -> tuple[str, str]:
    annotation = info.get("ANN") or info.get("CSQ") or ""
    terms = re.findall(r"(?:missense_variant|synonymous_variant|stop_gained|stop_lost|start_lost|frameshift_variant|inframe_insertion|inframe_deletion|splice_[^|,]+)", annotation)
    amino = next((part for part in re.split(r"[|,]", annotation) if re.fullmatch(r"p\.[A-Za-z*]+\d+[A-Za-z*=]+", part)), "")
    return ",".join(dict.fromkeys(terms)), amino


def _feature_for_position(position: int, features: list[dict[str, object]], gene_start: int, gene_end: int, strand: int, promoter_length: int) -> str:
    overlapping = [str(row.get("feature_type") or "") for row in features if int(row.get("start") or 0) <= position <= int(row.get("end") or -1)]
    if overlapping:
        priority = ["CDS", "five_prime_UTR", "three_prime_UTR", "exon"]
        return next((item for item in priority if item in overlapping), overlapping[0])
    if strand >= 0 and gene_start - promoter_length <= position < gene_start:
        return "promoter"
    if strand < 0 and gene_end < position <= gene_end + promoter_length:
        return "promoter"
    if gene_start <= position <= gene_end:
        return "intron"
    return "outside_gene"


def parse_vcf(
    payload: bytes,
    filename: str,
    *,
    input_id: str,
    rap_gene: str,
    transcript_id: str,
    gene_start: int,
    gene_end: int,
    strand: int,
    features: list[dict[str, object]],
    promoter_length: int = 2000,
    reference_sequence: str = "",
    assembly: str = "IRGSP-1.0",
    sample_groups_payload: bytes = b"",
    sample_groups_filename: str = "",
) -> tuple[list[dict[str, object]], list[dict[str, object]], list[str]]:
    text = _decode(payload, filename)
    samples: list[str] = []
    rows: list[dict[str, object]] = []
    warnings: list[str] = []
    queried_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    for line in text.splitlines():
        if line.startswith("##reference=") and "IRGSP" not in line.upper() and "NIPPONBARE" not in line.upper():
            warnings.append(f"VCF 声明的参考版本未明确为 IRGSP-1.0：{line.split('=', 1)[1]}")
        if line.startswith("#CHROM"):
            samples = line.split("\t")[9:]
            continue
        if not line or line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) < 8:
            continue
        chrom, position_text, variant_id, ref, alt, qual, filt, info_text = fields[:8]
        try: position = int(position_text)
        except ValueError: continue
        if position < gene_start - promoter_length or position > gene_end + promoter_length:
            continue
        info = _info_map(info_text)
        consequences, amino = _consequence(info)
        ref_status = "not_checked"
        if reference_sequence and gene_start <= position <= gene_end and position - gene_start + len(ref) <= len(reference_sequence):
            expected = reference_sequence[position - gene_start: position - gene_start + len(ref)].upper()
            ref_status = "matched" if expected == ref.upper() else "ref_mismatch"
        genotype_fields = fields[9:]
        format_keys = fields[8].split(":") if len(fields) > 8 else []
        gt_index = format_keys.index("GT") if "GT" in format_keys else 0
        genotypes: dict[str, str] = {}
        allele_numbers: list[int] = []
        missing = 0
        for sample, value in zip(samples, genotype_fields):
            parts = value.split(":")
            gt = parts[gt_index] if gt_index < len(parts) else "."
            genotypes[sample] = gt
            if "." in gt:
                missing += 1
            else:
                allele_numbers.extend(int(number) for number in re.split(r"[/|]", gt) if number.isdigit())
        alt_count = sum(number > 0 for number in allele_numbers)
        af = alt_count / len(allele_numbers) if allele_numbers else info.get("AF", "")
        maf = min(float(af), 1 - float(af)) if isinstance(af, float) else ""
        missing_rate = missing / len(samples) if samples else ""
        status = "ref_mismatch" if ref_status == "ref_mismatch" else "annotated"
        row = {
            "input_id": input_id, "rap_gene": rap_gene, "transcript_id": transcript_id,
            "assembly": assembly, "chrom": chrom, "position": position,
            "variant_id": variant_id if variant_id != "." else f"{chrom}:{position}:{ref}:{alt}",
            "ref": ref, "alt": alt, "region": _feature_for_position(position, features, gene_start, gene_end, strand, promoter_length),
            "allele_frequency": af, "missing_rate": missing_rate, "maf": maf,
            "coding_consequence": consequences, "amino_acid_change": amino,
            "database_function_score": info.get("SIFT") or info.get("CADD") or "",
            "ref_validation": ref_status, "status": status,
            "source_url": f"uploaded://{filename}", "queried_at": queried_at,
            "parameters": f"missing<=0.20;MAF>=0.01;assembly={assembly}",
            "error": "REF does not match IRGSP-1.0; consequence interpretation stopped" if status == "ref_mismatch" else "",
            "_genotypes": genotypes,
        }
        if status == "ref_mismatch":
            row["coding_consequence"] = ""; row["amino_acid_change"] = ""
        rows.append(row)
    eligible = [row for row in rows if row["status"] != "ref_mismatch" and samples and float(row.get("missing_rate") or 0) <= 0.20 and float(row.get("maf") or 0) >= 0.01]
    haplotypes: dict[tuple[str, ...], list[str]] = {}
    for sample in samples:
        hap = tuple(str(row["_genotypes"].get(sample, ".")) for row in eligible)
        if hap and all("." not in gt for gt in hap):
            haplotypes.setdefault(hap, []).append(sample)
    group_map: dict[str, str] = {}
    if sample_groups_payload:
        group_rows = csv.DictReader(io.StringIO(sample_groups_payload.decode("utf-8-sig", errors="replace")))
        for group_row in group_rows:
            sample = str(group_row.get("sample") or group_row.get("Sample") or "").strip()
            group = str(group_row.get("group") or group_row.get("Group") or "").strip()
            if sample and group: group_map[sample] = group
    summary = [{
        "input_id": input_id, "rap_gene": rap_gene, "transcript_id": transcript_id,
        "haplotype": f"H{index}", "genotype_pattern": "|".join(hap),
        "sample_count": len(members), "sample_frequency": len(members) / len(samples) if samples else "",
        "samples": ",".join(members),
        "subgroup_frequency": ";".join(
            f"{group}:{sum(group_map.get(sample) == group for sample in members)}/{sum(group_map.get(sample) == group for sample in samples)}"
            for group in sorted(set(group_map.values())) if sum(group_map.get(sample) == group for sample in samples)
        ),
        "filtered_variant_count": len(eligible), "status": "calculated",
        "source_url": f"uploaded://{filename}", "queried_at": queried_at,
    } for index, (hap, members) in enumerate(sorted(haplotypes.items(), key=lambda item: -len(item[1])), 1)]
    for row in rows:
        row.pop("_genotypes", None)
    if samples and not eligible:
        warnings.append("VCF 中没有通过缺失率≤20%、MAF≥1% 且 REF 校验的位点，未生成单倍型。")
    if not samples:
        warnings.append("VCF 无样本基因型矩阵，仅输出变异注释，不生成单倍型。")
    return rows, summary, warnings


def fetch_ricevarmap_variants(msu_ids: list[str], session: requests.Session | None = None) -> tuple[list[dict[str, object]], dict[str, bytes], list[str]]:
    """Query the official v3 gene page; return warnings instead of guessing when WAF/schema blocks it."""
    client = session or requests.Session(); rows, raw, warnings = [], {}, []
    queried_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    for msu_id in dict.fromkeys(value for value in msu_ids if value):
        url = requests.compat.urljoin(RICEVARMAP_V3_URL, f"vars_in_gene/?gene={msu_id}")
        try:
            response = client.get(url, timeout=(8, 45), headers={"User-Agent": "MyBioTools/1.9.1"})
            response.raise_for_status(); raw[f"{msu_id}/ricevarmap_v3.html"] = response.content
            soup = BeautifulSoup(response.text, "html.parser")
            parsed = 0
            for table in soup.find_all("table"):
                headers = [cell.get_text(" ", strip=True).casefold() for cell in table.find_all("th")]
                if not headers or not any("pos" in header for header in headers): continue
                for tr in table.find_all("tr"):
                    cells = [cell.get_text(" ", strip=True) for cell in tr.find_all("td")]
                    if len(cells) != len(headers): continue
                    record = dict(zip(headers, cells)); position = next((value for key, value in record.items() if "pos" in key), "")
                    if not str(position).isdigit(): continue
                    rows.append({"input_id": msu_id, "rap_gene": "", "transcript_id": "", "assembly": "IRGSP-1.0", "chrom": next((value for key, value in record.items() if "chr" in key), ""), "position": int(position), "variant_id": next((value for key, value in record.items() if "variant" in key or key == "id"), ""), "ref": next((value for key, value in record.items() if "primary allele" in key or key == "ref"), ""), "alt": next((value for key, value in record.items() if "secondary allele" in key or key == "alt"), ""), "region": "", "allele_frequency": next((value for key, value in record.items() if "frequency" in key), ""), "coding_consequence": next((value for key, value in record.items() if "effect" in key), ""), "amino_acid_change": "", "database_function_score": "", "ref_validation": "database_IRGSP", "status": "database_record", "source_url": response.url, "queried_at": queried_at, "parameters": "RiceVarMap v3 Nipponbare/IRGSP-1.0", "error": ""}); parsed += 1
            if not parsed: warnings.append(f"{msu_id} RiceVarMap v3 页面未返回可解析变异（可能无记录、WAF 或页面改版）。")
        except Exception as exc:
            warnings.append(f"{msu_id} RiceVarMap v3 失败：{type(exc).__name__}: {exc}")
    return rows, raw, warnings


def build_variation_artifacts(variants: list[dict[str, object]], haplotypes: list[dict[str, object]]) -> dict[str, bytes]:
    artifacts: dict[str, bytes] = {}

    def save_figure(figure, stem: str) -> None:
        # Keep DOCX-ready PNG and editable SVG available even if a frozen
        # distribution accidentally omits matplotlib's PDF backend.
        for suffix, fmt, dpi in (("svg", "svg", None), ("png", "png", 600), ("pdf", "pdf", None)):
            try:
                buffer = io.BytesIO()
                figure.savefig(buffer, format=fmt, dpi=dpi, bbox_inches="tight", facecolor="white")
                artifacts[f"{stem}.{suffix}"] = buffer.getvalue()
            except (ImportError, ModuleNotFoundError):
                if fmt != "pdf":
                    raise
        plt.close(figure)

    def as_float(value: object) -> float | None:
        try:
            parsed = float(value)
            return parsed if 0 <= parsed <= 1 else None
        except (TypeError, ValueError):
            return None

    if variants:
        positions = [int(row["position"]) for row in variants if str(row.get("position") or "").isdigit()]
        if positions:
            region_counts = Counter(str(row.get("region") or "unclassified") for row in variants)
            top_regions = [name for name, _ in region_counts.most_common(7)]
            color_map = {name: CVD_PALETTE[index % len(CVD_PALETTE)] for index, name in enumerate(top_regions)}
            color_map["Other"] = OTHER
            frequencies = [as_float(row.get("allele_frequency")) for row in variants]
            has_frequency = any(value is not None for value in frequencies)
            y_values = [value if value is not None else 0 for value in frequencies]
            colors = [
                color_map.get(str(row.get("region") or "unclassified"), color_map["Other"])
                for row in variants
            ]

            figure, (track_axis, count_axis) = plt.subplots(
                2,
                1,
                figsize=(7.4, 4.75),
                constrained_layout=True,
                gridspec_kw={"height_ratios": [1.45, 1], "hspace": 0.12},
            )
            baseline = 0 if has_frequency else 0.18
            marker_y = y_values if has_frequency else [0.62] * len(positions)
            track_axis.vlines(positions, baseline, marker_y, color=colors, linewidth=0.8, alpha=0.72)
            track_axis.scatter(positions, marker_y, c=colors, s=26, edgecolor="white", linewidth=0.35, zorder=3)
            track_axis.set_xlabel("IRGSP-1.0 genomic position")
            add_axis_title(
                track_axis,
                "Variant landscape",
                f"Analyzed gene region  ·  n = {len(positions):,} variants  ·  IRGSP-1.0",
            )
            if has_frequency:
                track_axis.set_ylabel("ALT allele frequency")
                track_axis.set_ylim(-0.03, 1.03)
            else:
                track_axis.set_yticks([])
                track_axis.set_ylim(0, 0.95)
                track_axis.text(
                    0.99,
                    0.08,
                    "Allele frequency unavailable",
                    transform=track_axis.transAxes,
                    ha="right",
                    va="bottom",
                    color=MUTED,
                    fontsize=6.8,
                )
            style_axis(track_axis, grid_axis="x", hide_left=not has_frequency)

            displayed_regions = region_counts.most_common(8)
            labels = [name for name, _ in displayed_regions][::-1]
            values = [count for _, count in displayed_regions][::-1]
            count_axis.barh(
                labels,
                values,
                color=[color_map.get(name, color_map["Other"]) for name in labels],
                height=0.56,
                edgecolor="white",
                linewidth=0.45,
            )
            count_axis.set_xlabel("Variant count")
            count_axis.set_title("Variants by annotated region", loc="left", fontsize=8.6, color=INK, pad=7)
            style_axis(count_axis, grid_axis="x")
            maximum = max(values or [1])
            count_axis.set_xlim(0, maximum * 1.13)
            for index, value in enumerate(values):
                count_axis.text(value + maximum * 0.022, index, str(value), va="center", fontsize=6.5, color=MUTED)
            save_figure(figure, "variant_overview")

    if haplotypes:
        displayed = sorted(
            haplotypes,
            key=lambda row: (-int(row.get("sample_count") or 0), str(row.get("haplotype") or "")),
        )[:15]
        labels = [str(row.get("haplotype") or f"H{index + 1}") for index, row in enumerate(displayed)]
        counts = [int(row.get("sample_count") or 0) for row in displayed]
        frequencies = [as_float(row.get("sample_frequency")) for row in displayed]
        use_frequency = any(value is not None for value in frequencies)
        values = [value or 0 for value in frequencies] if use_frequency else counts
        figure, axis = plt.subplots(
            figsize=(7.4, max(3.1, 0.32 * len(displayed) + 1.75)),
            constrained_layout=True,
        )
        order = list(range(len(labels) - 1, -1, -1))
        display_values = [values[index] for index in order]
        maximum = max(display_values or [1])
        axis.barh(
            [labels[index] for index in order],
            display_values,
            color=ACCENT,
            height=0.56,
            edgecolor="white",
            linewidth=0.45,
        )
        axis.set_xlabel("Sample frequency" if use_frequency else "Sample count")
        if use_frequency:
            axis.set_xlim(0, max(1.0, maximum * 1.13))
        else:
            axis.set_xlim(0, maximum * 1.13)
        add_axis_title(axis, "Haplotype distribution", f"Top {len(displayed)} haplotypes ranked by sample support")
        style_axis(axis, grid_axis="x")
        for display_index, source_index in enumerate(order):
            label = f"{values[source_index]:.1%}" if use_frequency else str(counts[source_index])
            axis.text(values[source_index] + maximum * 0.022, display_index, label, va="center", fontsize=6.7, color=MUTED)
        save_figure(figure, "haplotype_frequency")

        parsed_groups: dict[str, dict[str, float]] = {}
        group_names: set[str] = set()
        for row in displayed:
            haplotype = str(row.get("haplotype") or "")
            for token in str(row.get("subgroup_frequency") or "").split(";"):
                group, separator, fraction = token.partition(":")
                numerator, slash, denominator = fraction.partition("/")
                if not separator or not slash:
                    continue
                try:
                    parsed_groups.setdefault(haplotype, {})[group] = int(numerator) / int(denominator)
                    group_names.add(group)
                except (ValueError, ZeroDivisionError):
                    continue
        if group_names:
            ordered_groups = sorted(group_names)
            matrix = [
                [parsed_groups.get(haplotype, {}).get(group, 0.0) for group in ordered_groups]
                for haplotype in labels
            ]
            figure, axis = plt.subplots(
                figsize=(max(5.8, min(7.4, 0.68 * len(ordered_groups) + 2.3)), max(3.0, 0.36 * len(labels) + 1.8)),
                constrained_layout=True,
            )
            image = axis.imshow(matrix, cmap="viridis", vmin=0, vmax=1, aspect="auto", interpolation="nearest")
            axis.set_xticks(range(len(ordered_groups)), ordered_groups, rotation=35, ha="right")
            axis.set_yticks(range(len(labels)), labels)
            add_axis_title(axis, "Haplotype × population", "Within-group frequency")
            for row_index, values_row in enumerate(matrix):
                for column_index, value in enumerate(values_row):
                    axis.text(
                        column_index,
                        row_index,
                        f"{value:.0%}",
                        ha="center",
                        va="center",
                        fontsize=6.4,
                        color="white" if value < 0.72 else INK,
                    )
            for spine in axis.spines.values():
                spine.set_visible(False)
            colorbar = figure.colorbar(image, ax=axis, fraction=0.035, pad=0.03, label="Within-group frequency")
            colorbar.outline.set_visible(False)
            save_figure(figure, "haplotype_group_heatmap")
    return artifacts
