"""InterPro domain, family and functional-site analysis."""

from __future__ import annotations

from collections import defaultdict
import hashlib
import io
import os
import re
import time
from typing import Callable, Iterable

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch, Rectangle
import requests

from plot_style import CVD_PALETTE, GRID, INK, LIGHT, MUTED, OTHER, add_axis_title, publication_context, style_axis


MATCHES_API = "https://www.ebi.ac.uk/interpro/matches/api"
IPRSCAN_REST = "https://www.ebi.ac.uk/Tools/services/rest/iprscan5"
INTERPRO_URL = "https://www.ebi.ac.uk/interpro/search/"


def protein_md5(sequence: str) -> str:
    normalized = re.sub(r"\s+", "", sequence).upper().rstrip("*")
    return hashlib.md5(normalized.encode("ascii", errors="strict")).hexdigest().upper()


def parse_matches_payload(
    payload: dict[str, object],
    protein_id: str,
    protein_length: int,
    source_url: str,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    domains: list[dict[str, object]] = []
    sites: list[dict[str, object]] = []
    for match in payload.get("matches", []):
        if not isinstance(match, dict):
            continue
        signature = match.get("signature") if isinstance(match.get("signature"), dict) else {}
        entry = signature.get("entry") if isinstance(signature.get("entry"), dict) else {}
        release = signature.get("signatureLibraryRelease") if isinstance(signature.get("signatureLibraryRelease"), dict) else {}
        accession = str(signature.get("accession") or entry.get("accession") or "")
        name = str(signature.get("name") or entry.get("name") or signature.get("description") or "")
        description = str(entry.get("description") or signature.get("description") or "")
        entry_type = str(entry.get("type") or signature.get("type") or "")
        database = str(release.get("library") or match.get("source") or "")
        database_version = str(release.get("version") or "")
        go_terms = entry.get("goXRefs") if isinstance(entry.get("goXRefs"), list) else []
        pathways = entry.get("pathwayXRefs") if isinstance(entry.get("pathwayXRefs"), list) else []
        go_text = "; ".join(
            str(item.get("id") or item.get("name") or "") for item in go_terms if isinstance(item, dict)
        )
        pathway_text = "; ".join(
            str(item.get("id") or item.get("name") or "") for item in pathways if isinstance(item, dict)
        )
        locations = match.get("locations") if isinstance(match.get("locations"), list) else []
        for location_index, location in enumerate(locations, start=1):
            if not isinstance(location, dict):
                continue
            start = location.get("start")
            end = location.get("end")
            domains.append(
                {
                    "protein_id": protein_id,
                    "protein_length_aa": protein_length,
                    "database": database,
                    "database_version": database_version,
                    "accession": accession,
                    "name": name,
                    "description": description,
                    "feature_type": entry_type,
                    "start": start,
                    "end": end,
                    "score": location.get("score", match.get("score")),
                    "evalue": location.get("evalue", match.get("evalue")),
                    "go_terms": go_text,
                    "pathways": pathway_text,
                    "source_url": source_url,
                    "status": "matched",
                    "error": "",
                }
            )
            location_sites = location.get("sites") if isinstance(location.get("sites"), list) else []
            for site in location_sites:
                if not isinstance(site, dict):
                    continue
                description_text = str(site.get("description") or site.get("name") or "functional site")
                site_locations = site.get("siteLocations") if isinstance(site.get("siteLocations"), list) else []
                if not site_locations:
                    site_locations = [site]
                for site_location in site_locations:
                    if not isinstance(site_location, dict):
                        continue
                    sites.append(
                        {
                            "protein_id": protein_id,
                            "database": database,
                            "accession": accession,
                            "site_type": str(site.get("numLocations") and "functional_site" or entry_type or "site"),
                            "description": description_text,
                            "start": site_location.get("start"),
                            "end": site_location.get("end", site_location.get("start")),
                            "residue": str(site_location.get("residue") or ""),
                            "source_url": source_url,
                            "status": "matched",
                            "error": "",
                        }
                    )
        top_sites = match.get("sites") if isinstance(match.get("sites"), list) else []
        for site in top_sites:
            if not isinstance(site, dict):
                continue
            for site_location in site.get("siteLocations", []) if isinstance(site.get("siteLocations"), list) else []:
                if not isinstance(site_location, dict):
                    continue
                sites.append(
                    {
                        "protein_id": protein_id,
                        "database": database,
                        "accession": accession,
                        "site_type": "functional_site",
                        "description": str(site.get("description") or "functional site"),
                        "start": site_location.get("start"),
                        "end": site_location.get("end", site_location.get("start")),
                        "residue": str(site_location.get("residue") or ""),
                        "source_url": source_url,
                        "status": "matched",
                        "error": "",
                    }
                )
    return domains, sites


def _iprscan_submit(
    protein_id: str,
    sequence: str,
    session: requests.Session,
    poll_timeout: float,
) -> tuple[dict[str, object] | None, str, str, dict[str, bytes]]:
    """Submit unmatched sequence to EBI REST; failures remain isolated."""
    email = os.environ.get("MY_BIO_TOOLS_CONTACT_EMAIL", "").strip()
    data = {
        "title": f"MyBioTools-{protein_id}",
        "stype": "p",
        "goterms": "true",
        "pathways": "true",
        "sequence": f">{protein_id}\n{sequence}\n",
    }
    if email:
        data["email"] = email
    response = session.post(f"{IPRSCAN_REST}/run", data=data, timeout=(10, 60))
    response.raise_for_status()
    job_id = response.text.strip()
    if not job_id or "<" in job_id:
        raise ValueError("InterProScan 未返回有效 job ID。")
    result_url = f"{IPRSCAN_REST}/result/{job_id}/json"
    deadline = time.monotonic() + max(30.0, poll_timeout)
    status = "PENDING"
    while time.monotonic() < deadline:
        status_response = session.get(f"{IPRSCAN_REST}/status/{job_id}", timeout=(5, 20))
        status_response.raise_for_status()
        status = status_response.text.strip().upper()
        if status == "FINISHED":
            result_response = session.get(result_url, timeout=(10, 90))
            result_response.raise_for_status()
            payload = result_response.json()
            if isinstance(payload, list) and payload:
                payload = payload[0]
            return payload if isinstance(payload, dict) else None, job_id, result_url, {
                f"{protein_id}_interproscan.json": result_response.content
            }
        if status in {"ERROR", "FAILURE", "NOT_FOUND"}:
            raise RuntimeError(f"InterProScan job {job_id} 状态：{status}")
        time.sleep(3.0)
    return None, job_id, result_url, {f"{protein_id}_interproscan_status.txt": status.encode("utf-8")}


def _parse_iprscan_json(
    payload: dict[str, object], protein_id: str, protein_length: int, source_url: str
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Translate InterProScan JSON into the same compact matches schema."""
    results = payload.get("results") if isinstance(payload.get("results"), list) else []
    normalized_matches: list[dict[str, object]] = []
    for result in results:
        if not isinstance(result, dict):
            continue
        for match in result.get("matches", []) if isinstance(result.get("matches"), list) else []:
            if not isinstance(match, dict):
                continue
            signature = match.get("signature") if isinstance(match.get("signature"), dict) else {}
            entry = signature.get("entry") if isinstance(signature.get("entry"), dict) else {}
            locations = []
            for location in match.get("locations", []) if isinstance(match.get("locations"), list) else []:
                if not isinstance(location, dict):
                    continue
                locations.append(
                    {
                        "start": location.get("start"),
                        "end": location.get("end"),
                        "score": location.get("score"),
                        "evalue": location.get("evalue"),
                        "sites": location.get("sites", []),
                    }
                )
            normalized_matches.append(
                {
                    "signature": {
                        "accession": signature.get("accession"),
                        "name": signature.get("name"),
                        "description": signature.get("description"),
                        "type": entry.get("type"),
                        "entry": entry,
                        "signatureLibraryRelease": signature.get("signatureLibraryRelease", {}),
                    },
                    "locations": locations,
                }
            )
    return parse_matches_payload({"matches": normalized_matches}, protein_id, protein_length, source_url)


def analyze_protein_domains(
    proteins: Iterable[tuple[str, str]],
    progress_callback: Callable[[int, int, str], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    session: requests.Session | None = None,
    poll_timeout: float = 180.0,
) -> tuple[list[dict[str, object]], list[dict[str, object]], dict[str, bytes], list[str]]:
    items = [(str(identifier), re.sub(r"\s+", "", str(sequence)).upper().rstrip("*")) for identifier, sequence in proteins]
    client = session or requests.Session()
    client.headers.update({"User-Agent": "MyBioTools/1.9.8", "Accept": "application/json"})
    domains: list[dict[str, object]] = []
    sites: list[dict[str, object]] = []
    raw_artifacts: dict[str, bytes] = {}
    warnings: list[str] = []
    for index, (protein_id, sequence) in enumerate(items, start=1):
        if cancel_check and cancel_check():
            break
        digest = protein_md5(sequence)
        url = f"{MATCHES_API}/matches/{digest}"
        try:
            response = client.get(url, timeout=(5, 35))
            if response.status_code == 404:
                payload, job_id, result_url, raw = _iprscan_submit(protein_id, sequence, client, poll_timeout)
                raw_artifacts.update(raw)
                if payload is None:
                    warnings.append(f"{protein_id} InterProScan 仍在运行：{job_id}；结果入口 {result_url}")
                    domains.append(
                        {
                            "protein_id": protein_id,
                            "protein_length_aa": len(sequence),
                            "source_url": result_url,
                            "provider_job_id": job_id,
                            "status": "pending_external",
                            "error": "外部任务超过本次轮询时间。",
                        }
                    )
                else:
                    parsed_domains, parsed_sites = _parse_iprscan_json(payload, protein_id, len(sequence), result_url)
                    domains.extend(parsed_domains)
                    sites.extend(parsed_sites)
            else:
                response.raise_for_status()
                raw_artifacts[f"{protein_id}_matches.json"] = response.content
                parsed_domains, parsed_sites = parse_matches_payload(response.json(), protein_id, len(sequence), response.url)
                domains.extend(parsed_domains)
                sites.extend(parsed_sites)
                if not parsed_domains:
                    warnings.append(f"{protein_id} InterPro 返回成功但没有结构域命中。")
        except Exception as exc:
            warnings.append(f"{protein_id} 结构域分析失败：{type(exc).__name__}: {exc}")
            domains.append(
                {
                    "protein_id": protein_id,
                    "protein_length_aa": len(sequence),
                    "source_url": url,
                    "status": "failed",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
        if progress_callback:
            progress_callback(index, len(items), protein_id)
    return domains, sites, raw_artifacts, warnings


def _figure_bytes(figure) -> tuple[bytes, bytes, bytes]:
    svg, pdf, png = io.BytesIO(), io.BytesIO(), io.BytesIO()
    figure.savefig(svg, format="svg", bbox_inches="tight", facecolor="white")
    figure.savefig(png, format="png", dpi=600, bbox_inches="tight", facecolor="white")
    try:
        figure.savefig(pdf, format="pdf", bbox_inches="tight", facecolor="white")
    except (ImportError, ModuleNotFoundError):
        # A frozen app can still produce the DOCX-ready PNG and editable SVG
        # if a packaging regression omits matplotlib's optional PDF backend.
        pdf = io.BytesIO()
    plt.close(figure)
    return svg.getvalue(), pdf.getvalue(), png.getvalue()


def build_domain_artifacts(
    domain_rows: list[dict[str, object]], site_rows: list[dict[str, object]]
) -> dict[str, bytes]:
    artifacts: dict[str, bytes] = {}
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in domain_rows:
        if row.get("status") == "matched" and row.get("start") and row.get("end"):
            grouped[str(row.get("protein_id"))].append(row)
    site_lookup: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in site_rows:
        if row.get("status") == "matched" and row.get("start"):
            site_lookup[str(row.get("protein_id"))].append(row)
    palette = {
        "Domain": CVD_PALETTE[2],
        "Family": CVD_PALETTE[0],
        "Repeat": CVD_PALETTE[4],
        "Region": CVD_PALETTE[3],
        "Homologous_superfamily": "#64748B",
    }
    for protein_id, rows in grouped.items():
        length = max(int(row.get("protein_length_aa") or 0) for row in rows)
        rows = sorted(rows, key=lambda row: (int(row.get("start") or 0), int(row.get("end") or 0)))
        track_rows: dict[str, list[dict[str, object]]] = defaultdict(list)
        for row in rows:
            track = str(row.get("database") or row.get("feature_type") or "Other")
            track_rows[track].append(row)
        ordered_tracks = sorted(
            track_rows,
            key=lambda track: (
                min(int(row.get("start") or 0) for row in track_rows[track]),
                track,
            ),
        )[:10]
        with publication_context():
            figure, axis = plt.subplots(
                figsize=(7.4, max(3.0, 0.46 * len(ordered_tracks) + 1.85)),
                constrained_layout=True,
            )
            for track_index, track in enumerate(ordered_tracks, start=1):
                y = len(ordered_tracks) - track_index + 1
                if track_index % 2:
                    axis.axhspan(y - 0.42, y + 0.42, color=LIGHT, zorder=0)
                axis.hlines(y, 1, max(length, 1), color="#C8D1DB", linewidth=1.15, zorder=1)
                for row in track_rows[track][:18]:
                    start, end = int(row["start"]), int(row["end"])
                    feature_type = str(row.get("feature_type") or "Region")
                    color = palette.get(feature_type, OTHER)
                    span = max(1, end - start + 1)
                    axis.add_patch(
                        Rectangle(
                            (start, y - 0.17),
                            span,
                            0.34,
                            facecolor=color,
                            edgecolor="white",
                            linewidth=0.45,
                            alpha=0.96,
                            zorder=2,
                        )
                    )
                    label = str(row.get("name") or row.get("accession") or "feature")
                    if span >= max(18, length * 0.11):
                        axis.text(
                            start + span / 2,
                            y,
                            label[:24],
                            ha="center",
                            va="center",
                            fontsize=6.3,
                            color="white" if feature_type in {"Domain", "Family", "Region"} else INK,
                            clip_on=True,
                            zorder=3,
                        )
            site_y = len(ordered_tracks) + 0.75
            for site in site_lookup.get(protein_id, [])[:25]:
                position = int(site.get("start") or 0)
                axis.vlines(position, 0.55, site_y, color=CVD_PALETTE[1], linewidth=0.75)
                axis.scatter([position], [site_y], color=CVD_PALETTE[1], s=18, edgecolor="white", linewidth=0.5, zorder=4)
            axis.set_xlim(1, max(length, 1))
            axis.set_ylim(0.35, len(ordered_tracks) + 1.25)
            axis.set_yticks(range(1, len(ordered_tracks) + 1), ordered_tracks[::-1])
            axis.set_xlabel("Amino-acid position")
            add_axis_title(axis, protein_id, f"Integrated protein architecture  ·  {length} aa")
            style_axis(axis, grid_axis="x")
            axis.tick_params(axis="y", labelsize=7.1)
            legend = [Patch(color=value, label=key) for key, value in palette.items()]
            if site_lookup.get(protein_id):
                legend.append(Patch(color=CVD_PALETTE[1], label="Functional site"))
            axis.legend(
                handles=legend,
                frameon=False,
                ncol=3,
                loc="upper right",
                bbox_to_anchor=(1, 1.12),
                labelcolor=MUTED,
                columnspacing=0.9,
                handlelength=1.2,
            )
            svg, pdf, png = _figure_bytes(figure)
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", protein_id).strip("_") or "protein"
        artifacts[f"protein_domains_{safe}.svg"] = svg
        if pdf:
            artifacts[f"protein_domains_{safe}.pdf"] = pdf
        artifacts[f"protein_domains_{safe}.png"] = png
    return artifacts


__all__ = [
    "INTERPRO_URL",
    "MATCHES_API",
    "analyze_protein_domains",
    "build_domain_artifacts",
    "parse_matches_payload",
    "protein_md5",
]
