"""Official-server adapters and local NLStradamus execution."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from itertools import groupby
import os
from pathlib import Path
import re
import subprocess
import tempfile
import threading
import time
from typing import Callable
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from rice_gene_core import (
    PredictionExecution,
    PredictionProviderAttempt,
    PredictionRegion,
    PredictionResult,
    normalize_protein,
    safe_file_stem,
)

try:
    import biolib
except ImportError:  # pragma: no cover - exercised by packaged-dependency checks
    biolib = None


DTU_SUBMIT_URL = "https://services.healthtech.dtu.dk/cgi-bin/webface2.cgi"
TOOL_URLS = {
    "SignalP 6.0": "https://services.healthtech.dtu.dk/services/SignalP-6.0/",
    "TMHMM 2.0": "https://services.healthtech.dtu.dk/services/TMHMM-2.0/",
    "DeepTMHMM 1.0": "https://services.healthtech.dtu.dk/services/DeepTMHMM-1.0/",
    "TargetP 2.0": "https://services.healthtech.dtu.dk/services/TargetP-2.0/",
    "cNLS Mapper": "https://nls-mapper.iab.keio.ac.jp/cgi-bin/NLS_Mapper_form.cgi",
    "NLStradamus 1.8": "https://pmc.ncbi.nlm.nih.gov/articles/PMC2711084/",
}
PREDICTORS = tuple(TOOL_URLS)
DTU_DIRECT_TIMEOUT_SECONDS = 120
BIOLIB_TIMEOUT_SECONDS = 900
BIOLIB_APPS = {
    "SignalP 6.0": ("DTU/SignalP_6", "--fastafile"),
    "DeepTMHMM 1.0": ("DTU/DeepTMHMM", "--fasta"),
}

DTU_CONFIGS = {
    "SignalP 6.0": "/var/www/services/services/SignalP-6.0/webface.cf",
    "TMHMM 2.0": "/var/www/services/services/TMHMM-2.0/webface.cf",
    "DeepTMHMM 1.0": "/var/www/services/services/DeepTMHMM-1.0/webface.cf",
    "TargetP 2.0": "/var/www/services/services/TargetP-2.0/webface.cf",
}

_service_locks = {name: threading.Lock() for name in PREDICTORS}
_biolib_cwd_lock = threading.Lock()


def create_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=1,
        connect=1,
        read=1,
        backoff_factor=0.8,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "POST"}),
    )
    session.mount("https://", HTTPAdapter(max_retries=retry, pool_connections=2, pool_maxsize=2))
    session.headers.update(
        {
            "User-Agent": "MyBioTools/1.9.1 (+academic rice protein analysis)",
            "Accept": "text/html, text/plain, application/xhtml+xml",
        }
    )
    return session


def fasta_text(protein_id: str, protein: str) -> str:
    return f">{protein_id}\n{protein}\n"


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _safe_error(value: object) -> str:
    """Remove credentials from provider errors before UI/report serialization."""
    text = str(value)
    token = os.environ.get("BIOLIB_TOKEN", "")
    if token:
        text = text.replace(token, "[REDACTED]")
    return re.sub(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+", r"\1[REDACTED]", text)


def _job_id(value: str) -> str:
    match = re.search(r"[?&]jobid=([A-Za-z0-9]+)", value)
    return match.group(1) if match else ""


def _artifact_name(url: str, fallback: str) -> str:
    name = Path(urlparse(url).path).name or fallback
    return safe_file_stem(name, fallback)


def _dtu_form_fields(
    tool: str,
    fasta: str,
    parameters: dict[str, object] | None = None,
) -> dict[str, str]:
    options = dict(parameters or {})
    fields = {"configfile": DTU_CONFIGS[tool]}
    if tool == "TMHMM 2.0":
        fields.update({"SEQ": fasta, "outform": "-noplot"})
    else:
        fields["fasta"] = fasta
    if tool == "SignalP 6.0":
        fields.update(
            {
                "organism": str(options.get("organism", "Eukarya")),
                "format": "long",
                "mode": str(options.get("mode", "fast")),
            }
        )
    elif tool == "TargetP 2.0":
        fields.update({"organism": "pl", "format": "long"})
    return fields


def _extract_refresh_url(response: requests.Response) -> str:
    soup = BeautifulSoup(response.text, "html.parser")
    refresh = soup.find("meta", attrs={"http-equiv": re.compile("refresh", re.I)})
    if refresh and refresh.get("content"):
        match = re.search(r"url\s*=\s*([^;]+)$", str(refresh["content"]), re.I)
        if match:
            return urljoin(response.url, match.group(1).strip(" '\""))
    candidates: list[str] = []
    for link in soup.find_all("a", href=True):
        href = urljoin(response.url, str(link["href"]))
        label = link.get_text(" ", strip=True).casefold()
        if any(token in href.casefold() for token in ("job", "result", "tmp", "webface")) or any(
            token in label for token in ("job", "result", "status", "continue")
        ):
            candidates.append(href)
    return candidates[0] if candidates else response.url


def _collect_result_artifacts(
    session: requests.Session,
    response: requests.Response,
) -> tuple[str, str, dict[str, bytes]]:
    soup = BeautifulSoup(response.text, "html.parser")
    page_text = soup.get_text("\n", strip=True)
    raw_parts = [page_text]
    artifacts: dict[str, bytes] = {}
    artifact_urls = [urljoin(response.url, str(link["href"])) for link in soup.find_all("a", href=True)]
    artifact_urls.extend(
        urljoin(response.url, match.group(1))
        for match in re.finditer(
            r"[\"']([^\"']+\.(?:json|csv|txt|tsv|gff3?|out|pred|short|png|svg|eps|pdf|zip))[\"']",
            response.text,
            re.I,
        )
    )
    for index, href in enumerate(dict.fromkeys(artifact_urls), start=1):
        folded = href.casefold()
        suffixes = (".json", ".csv", ".txt", ".tsv", ".gff", ".gff3", ".out", ".pred", ".short", ".png", ".svg", ".eps", ".pdf", ".zip")
        if not folded.endswith(suffixes):
            continue
        try:
            artifact = session.get(href, timeout=(5, 30))
            artifact.raise_for_status()
            payload = artifact.content
            name = _artifact_name(href, f"artifact_{index}")
            if name in artifacts:
                name = f"{index}_{name}"
            artifacts[name] = payload
            if folded.endswith((".json", ".csv", ".txt", ".tsv", ".gff", ".gff3", ".out", ".pred", ".short")):
                raw_parts.append(f"SOURCE {href}\n{artifact.text}")
        except requests.RequestException:
            continue
    return "\n\n".join(part for part in raw_parts if part), response.text, artifacts


def _collect_result_text(session: requests.Session, response: requests.Response) -> tuple[str, str]:
    raw_text, raw_html, _ = _collect_result_artifacts(session, response)
    return raw_text, raw_html


def _parse_regions(tool: str, text: str, protein: str) -> list[PredictionRegion]:
    regions: list[PredictionRegion] = []
    patterns = [
        ("TMhelix", r"(?:TMhelix|transmembrane(?:\s+helix)?)\s*[:\t ]+(\d+)\s*(?:-|\.\.|\s)\s*(\d+)"),
        ("signal peptide", r"(?:signal peptide|Sec/SPI|SP)\s*[:\t ]+(\d+)\s*(?:-|\.\.|\s)\s*(\d+)"),
        ("NLS", r"(?:NLS|nuclear localization signal)\s*[:\t ]+(\d+)\s*(?:-|\.\.|\s)\s*(\d+)"),
    ]
    for region_type, pattern in patterns:
        for match in re.finditer(pattern, text, re.I):
            start, end = int(match.group(1)), int(match.group(2))
            if 1 <= start <= end <= len(protein):
                regions.append(
                    PredictionRegion(
                        region_type=region_type,
                        start=start,
                        end=end,
                        sequence=protein[start - 1:end],
                    )
                )
    if tool == "TMHMM 2.0":
        for match in re.finditer(r"(?:^|\n)\S+\s+TMHMM\S*\s+(inside|outside|TMhelix)\s+(\d+)\s+(\d+)", text, re.I):
            region_type = match.group(1)
            start, end = int(match.group(2)), int(match.group(3))
            key = (region_type.casefold(), start, end)
            if 1 <= start <= end <= len(protein) and not any(
                (r.region_type.casefold(), r.start, r.end) == key for r in regions
            ):
                regions.append(PredictionRegion(region_type, start, end, sequence=protein[start - 1:end]))
        for match in re.finditer(r"\bTMhelix\s+(\d+)\s+(\d+)", text, re.I):
            start, end = int(match.group(1)), int(match.group(2))
            key = ("TMhelix", start, end)
            if 1 <= start <= end <= len(protein) and not any((r.region_type, r.start, r.end) == key for r in regions):
                regions.append(PredictionRegion("TMhelix", start, end, sequence=protein[start - 1:end]))
    return regions


def _parse_gff_regions(text: str, proteins: dict[str, str]) -> dict[str, list[PredictionRegion]]:
    regions: dict[str, list[PredictionRegion]] = {}
    for line in text.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        fields = [item.strip() for item in line.split("\t")]
        if len(fields) >= 5 and fields[3].isdigit() and fields[4].isdigit():
            protein_id, region_type = fields[0], fields[2]
            start, end = int(fields[3]), int(fields[4])
            note = fields[8] if len(fields) > 8 else ""
        elif len(fields) >= 4 and fields[2].isdigit() and fields[3].isdigit():
            protein_id, region_type = fields[0], fields[1]
            start, end = int(fields[2]), int(fields[3])
            note = fields[4] if len(fields) > 4 else ""
        else:
            continue
        protein = proteins.get(protein_id, "")
        if protein and 1 <= start <= end <= len(protein):
            regions.setdefault(protein_id, []).append(
                PredictionRegion(
                    region_type=region_type,
                    start=start,
                    end=end,
                    sequence=protein[start - 1:end],
                    note=note,
                )
            )
    return regions


def _parse_probability_table(text: str) -> dict[str, dict[str, object]]:
    rows: dict[str, dict[str, object]] = {}
    header: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("SOURCE ") or stripped.startswith("##"):
            header = []
            continue
        candidate = stripped.lstrip("#").strip()
        if "\t" in candidate and re.search(r"\bPrediction\b", candidate, re.I):
            header = [item.strip() for item in candidate.split("\t")]
            continue
        fields = [item.strip() for item in stripped.split("\t")]
        if len(fields) < 2 or fields[0].casefold() in {"id", "name"}:
            continue
        if not header:
            if re.match(r"^[A-Za-z0-9_.|:-]+$", fields[0]) and re.search(
                r"^(?:OTHER|NO_SP|Sec/SPI|Sec/SPII|Tat/SPI|Tat/SPII|Sec/SPIII|SP|mTP|cTP|lTP)$",
                fields[1],
                re.I,
            ):
                rows[fields[0]] = {"classification": fields[1], "probabilities": {}, "cleavage_site": None}
            continue
        if len(fields) > len(header):
            continue
        if len(fields) < len(header):
            fields.extend([""] * (len(header) - len(fields)))
        record = dict(zip(header, fields))
        protein_id = fields[0]
        prediction_key = next((key for key in header if key.casefold() == "prediction"), header[1])
        probabilities: dict[str, float] = {}
        for key, value in record.items():
            if key in {header[0], prediction_key} or not value:
                continue
            try:
                number = float(value)
            except ValueError:
                continue
            if 0.0 <= number <= 1.0:
                probabilities[key] = number
        cleavage = None
        for value in record.values():
            match = re.search(r"(?:CS pos|cleavage(?: site)?)\s*[:=]?\s*(\d+)", value, re.I)
            if match:
                cleavage = int(match.group(1))
                break
        rows[protein_id] = {
            "classification": record.get(prediction_key, ""),
            "probabilities": probabilities,
            "cleavage_site": cleavage,
        }
    return rows


_DEEP_TOPOLOGY_LABELS = {
    "I": "inside",
    "O": "outside",
    "M": "TMhelix",
    "B": "beta strand",
    "S": "signal peptide",
    "P": "periplasm",
}


def _parse_deep_topology(
    text: str,
    proteins: dict[str, str],
) -> tuple[dict[str, str], dict[str, list[PredictionRegion]]]:
    classifications: dict[str, str] = {}
    regions: dict[str, list[PredictionRegion]] = {}
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for index, line in enumerate(lines):
        if not line.startswith(">") or index + 2 >= len(lines):
            continue
        header = line[1:].strip()
        parts = [part.strip() for part in header.split("|")]
        protein_id = parts[0].split()[0]
        classifications[protein_id] = parts[1] if len(parts) > 1 else ""
        topology = lines[index + 2].strip()
        protein = proteins.get(protein_id, "")
        if not protein or len(topology) != len(protein):
            continue
        topology_classes = []
        if "S" in topology.upper():
            topology_classes.append("signal peptide")
        if "M" in topology.upper():
            topology_classes.append("alpha-helical TM")
        if "B" in topology.upper():
            topology_classes.append("beta-barrel TM")
        if not topology_classes:
            topology_classes.append("globular")
        if not classifications[protein_id] or "topology" in classifications[protein_id].casefold():
            classifications[protein_id] = "; ".join(topology_classes)
        cursor = 1
        for label, chunk in groupby(topology):
            length = len(list(chunk))
            end = cursor + length - 1
            regions.setdefault(protein_id, []).append(
                PredictionRegion(
                    region_type=_DEEP_TOPOLOGY_LABELS.get(label.upper(), label),
                    start=cursor,
                    end=end,
                    sequence=protein[cursor - 1:end],
                    note=f"DeepTMHMM topology code {label}",
                )
            )
            cursor = end + 1
    return classifications, regions


def _classification(tool: str, text: str) -> tuple[str, str]:
    condensed = " ".join(text.split())
    if tool == "SignalP 6.0":
        match = re.search(r"\b(Sec/SPIII|Sec/SPII|Sec/SPI|Tat/SPII|Tat/SPI|OTHER|NO_SP)\b", text, re.I)
        label = match.group(1) if match else "result returned"
        return label, f"SignalP classification: {label}"
    if tool == "TargetP 2.0":
        match = re.search(r"\b(lTP|cTP|mTP|SP|OTHER)\b", text, re.I)
        label = match.group(1) if match else "result returned"
        return label, f"TargetP classification: {label}"
    if tool == "TMHMM 2.0":
        match = re.search(r"Number of predicted TMHs\s*:\s*(\d+)", text, re.I)
        count = int(match.group(1)) if match else len(re.findall(r"\bTMhelix\b", text, re.I))
        return ("TM protein" if count else "no TM helix"), f"Predicted transmembrane helices: {count}"
    if tool == "DeepTMHMM 1.0":
        labels = [label for label in ("SP", "TM", "BETA", "GLOB") if re.search(rf"\b{label}\b", text, re.I)]
        label = "/".join(labels) if labels else "result returned"
        return label, f"DeepTMHMM classification: {label}"
    if tool == "cNLS Mapper":
        if re.search(r"no\s+(?:potential\s+)?NLS|not\s+found", text, re.I):
            return "no cNLS", "No cNLS above the selected cutoff."
        scores = [float(value) for value in re.findall(r"(?:score|Score)\s*[:=]?\s*(\d+(?:\.\d+)?)", text)]
        if scores:
            return "cNLS detected", f"Highest cNLS Mapper score: {max(scores):.1f}"
        return "result returned", condensed[:240]
    return "result returned", condensed[:240]


def _parse_cnls_result(html: str, protein: str) -> tuple[list[PredictionRegion], str, str]:
    soup = BeautifulSoup(html, "html.parser")
    regions: list[PredictionRegion] = []
    for table in soup.find_all("table"):
        heading = table.get_text(" ", strip=True)
        if "Predicted monopartite NLS" in heading:
            region_type = "monopartite NLS"
        elif "Predicted bipartite NLS" in heading:
            region_type = "bipartite NLS"
        else:
            continue
        for row in table.find_all("tr"):
            cells = [cell.get_text(" ", strip=True) for cell in row.find_all("td")]
            if len(cells) < 3 or not cells[0] or not cells[1]:
                continue
            position = re.search(r"\d+", cells[0])
            score_match = re.search(r"\d+(?:\.\d+)?", cells[2])
            sequence = re.sub(r"[^A-Za-z]", "", cells[1]).upper()
            if not position or not sequence:
                continue
            start = int(position.group())
            end = start + len(sequence) - 1
            score = float(score_match.group()) if score_match else None
            if 1 <= start <= end <= len(protein):
                regions.append(
                    PredictionRegion(
                        region_type=region_type,
                        start=start,
                        end=end,
                        score=score,
                        sequence=protein[start - 1:end],
                    )
                )
    if regions:
        scores = [region.score for region in regions if region.score is not None]
        highest = max(scores) if scores else None
        summary = f"Predicted {len(regions)} cNLS region(s)"
        if highest is not None:
            summary += f"; highest score {highest:.1f}."
        else:
            summary += "."
        return regions, "cNLS detected", summary
    text = soup.get_text(" ", strip=True)
    classification, summary = _classification("cNLS Mapper", text)
    return _parse_regions("cNLS Mapper", text, protein), classification, summary


def _dtu_page_state(html: str) -> str:
    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    headline = text[:300]
    match = re.match(r"^[A-Za-z0-9]+\s+(queued|running|failed|finished|completed)\b", headline, re.I)
    if match:
        return match.group(1).casefold()
    if re.search(r"\b(failed run|unfortunately, your job failed|job failed|submission failed)\b", headline, re.I):
        return "failed"
    if re.search(r"\b(queued|running|please wait|not finished)\b", headline, re.I):
        return "running"
    return "completed"


def _build_dtu_results(
    tool: str,
    proteins: list[tuple[str, str]],
    parameters: dict[str, object],
    raw_text: str,
    raw_html: str,
    result_url: str,
    attempt: PredictionProviderAttempt,
) -> list[PredictionResult]:
    protein_map = dict(proteins)
    table = _parse_probability_table(raw_text)
    gff_regions = _parse_gff_regions(raw_text, protein_map)
    deep_classes, deep_regions = _parse_deep_topology(raw_text, protein_map)
    results: list[PredictionResult] = []
    for protein_id, protein in proteins:
        info = table.get(protein_id, {})
        if tool == "DeepTMHMM 1.0":
            classification = deep_classes.get(protein_id, "")
            regions = deep_regions.get(protein_id, []) or gff_regions.get(protein_id, [])
        else:
            classification = str(info.get("classification") or "")
            regions = gff_regions.get(protein_id, [])
            if tool == "SignalP 6.0" and classification.upper() == "SP":
                probabilities = dict(info.get("probabilities") or {})
                candidates = [(key, value) for key, value in probabilities.items() if key.casefold() != "other"]
                if candidates:
                    label = max(candidates, key=lambda item: item[1])[0]
                    match = re.search(r"\(([^)]+)\)", label)
                    classification = match.group(1) if match else label
        if not classification and len(proteins) == 1:
            classification, _ = _classification(tool, raw_text)
        if not regions and len(proteins) == 1:
            regions = _parse_regions(tool, raw_text, protein)
        cleavage = info.get("cleavage_site")
        if isinstance(cleavage, int) and 1 <= cleavage <= len(protein) and not any(
            "signal" in region.region_type.casefold() for region in regions
        ):
            regions.append(
                PredictionRegion(
                    "signal/targeting peptide",
                    1,
                    cleavage,
                    sequence=protein[:cleavage],
                    note="Predicted cleavage position",
                )
            )
        usable = bool(classification and classification.casefold() != "result returned") or bool(regions) or bool(info)
        status = "matched" if usable else "failed"
        summary = _classification(tool, classification or raw_text)[1] if usable else ""
        results.append(
            PredictionResult(
                protein_id=protein_id,
                tool=tool,
                version=tool.rsplit(" ", 1)[-1],
                status=status,
                classification=classification,
                summary=summary,
                parameters=dict(parameters),
                regions=regions,
                result_url=result_url,
                raw_text=raw_text,
                raw_html=raw_html,
                error="" if usable else "DTU 返回页面中没有可解析的结构化结果。",
                provider=attempt.provider,
                provider_job_id=attempt.job_id,
                probabilities=dict(info.get("probabilities") or {}),
                attempts=[attempt],
            )
        )
    return results


def _failed_execution(
    tool: str,
    proteins: list[tuple[str, str]],
    parameters: dict[str, object],
    status: str,
    error: str,
    attempt: PredictionProviderAttempt,
    raw_html: str = "",
) -> PredictionExecution:
    return PredictionExecution(
        results=[
            PredictionResult(
                protein_id=protein_id,
                tool=tool,
                version=tool.rsplit(" ", 1)[-1],
                status=status,
                parameters=dict(parameters),
                result_url=TOOL_URLS[tool],
                raw_text="Manual submission FASTA\n" + fasta_text(protein_id, protein),
                raw_html=raw_html,
                error=error,
                provider=attempt.provider,
                provider_job_id=attempt.job_id,
                attempts=[attempt],
            )
            for protein_id, protein in proteins
        ]
    )


def run_dtu_batch(
    tool: str,
    proteins: list[tuple[str, str]],
    parameters: dict[str, object] | None = None,
    timeout_seconds: int = DTU_DIRECT_TIMEOUT_SECONDS,
    cancel_check: Callable[[], bool] | None = None,
) -> PredictionExecution:
    options = dict(parameters or {})
    valid: list[tuple[str, str]] = []
    invalid: list[PredictionResult] = []
    for protein_id, protein in proteins:
        normalized, errors = normalize_protein(protein)
        if errors:
            invalid.append(
                PredictionResult(
                    protein_id=protein_id,
                    tool=tool,
                    version=tool.rsplit(" ", 1)[-1],
                    status="invalid_input",
                    parameters=options,
                    raw_text=fasta_text(protein_id, protein),
                    error="；".join(errors),
                )
            )
        else:
            valid.append((protein_id, normalized))
    if not valid:
        return PredictionExecution(results=invalid)

    started = _now()
    session = create_session()
    response: requests.Response | None = None
    job_url = TOOL_URLS[tool]
    try:
        combined = "".join(fasta_text(protein_id, protein) for protein_id, protein in valid)
        fields = _dtu_form_fields(tool, combined, options)
        response = session.post(
            DTU_SUBMIT_URL,
            files={key: (None, value) for key, value in fields.items()},
            timeout=(10, 60),
        )
        response.raise_for_status()
        job_url = _extract_refresh_url(response)
        deadline = time.monotonic() + max(0, timeout_seconds)
        while True:
            if cancel_check and cancel_check():
                attempt = PredictionProviderAttempt(
                    "dtu_web", "cancelled", _job_id(job_url), job_url, "用户取消。", started, _now()
                )
                execution = _failed_execution(tool, valid, options, "cancelled", "用户取消。", attempt)
                execution.results.extend(invalid)
                return execution
            state = _dtu_page_state(response.text)
            if state == "failed":
                attempt = PredictionProviderAttempt(
                    "dtu_web", "failed", _job_id(job_url), job_url, "官网任务返回失败状态。", started, _now()
                )
                execution = _failed_execution(
                    tool, valid, options, "failed", "官网任务返回失败状态。", attempt, response.text
                )
                execution.results.extend(invalid)
                return execution
            if state == "completed" and (response.url != DTU_SUBMIT_URL or job_url != DTU_SUBMIT_URL):
                raw_text, raw_html, artifacts = _collect_result_artifacts(session, response)
                attempt = PredictionProviderAttempt(
                    "dtu_web", "matched", _job_id(job_url), response.url, "", started, _now()
                )
                results = _build_dtu_results(tool, valid, options, raw_text, raw_html, response.url, attempt)
                if all(item.status == "matched" for item in results):
                    prefixed = {
                        f"dtu/{safe_file_stem(tool)}/{name}": payload for name, payload in artifacts.items()
                    }
                    return PredictionExecution(results=results + invalid, raw_artifacts=prefixed)
                error = "DTU 任务完成，但至少一个蛋白没有可解析结果。"
                failed_attempt = PredictionProviderAttempt(
                    "dtu_web", "failed", attempt.job_id, attempt.url, error, started, _now()
                )
                execution = _failed_execution(tool, valid, options, "failed", error, failed_attempt, raw_html)
                execution.raw_artifacts = {
                    f"dtu/{safe_file_stem(tool)}/{name}": payload for name, payload in artifacts.items()
                }
                execution.results.extend(invalid)
                return execution
            if time.monotonic() >= deadline:
                attempt = PredictionProviderAttempt(
                    "dtu_web", "timeout", _job_id(job_url), job_url, f"等待超过 {timeout_seconds} 秒。", started, _now()
                )
                execution = _failed_execution(
                    tool, valid, options, "timeout", f"官网任务等待超过 {timeout_seconds} 秒。", attempt
                )
                execution.results.extend(invalid)
                return execution
            time.sleep(5)
            response = session.get(job_url, timeout=(10, 60))
            response.raise_for_status()
            job_url = _extract_refresh_url(response)
    except requests.RequestException as exc:
        error = f"官网请求失败：{type(exc).__name__}: {exc}"
        attempt = PredictionProviderAttempt(
            "dtu_web", "failed", _job_id(job_url), job_url, error, started, _now()
        )
        execution = _failed_execution(
            tool, valid, options, "failed", error, attempt, response.text if response is not None else ""
        )
        execution.results.extend(invalid)
        return execution


def run_dtu_prediction(
    tool: str,
    protein_id: str,
    protein: str,
    parameters: dict[str, object] | None = None,
    timeout_seconds: int = 900,
) -> PredictionResult:
    execution = run_dtu_batch(tool, [(protein_id, protein)], parameters, timeout_seconds)
    return execution.results[0]


def run_cnls_mapper(
    protein_id: str,
    protein: str,
    cutoff: float = 5.0,
    linker: str = "Within terminal 60-amino-acid regions",
) -> PredictionResult:
    started = _now()
    result = PredictionResult(
        protein_id=protein_id,
        tool="cNLS Mapper",
        version="web 2012-11-07",
        parameters={"cutoff": cutoff, "long_linker": linker},
        result_url=TOOL_URLS["cNLS Mapper"],
        provider="nls_mapper_web",
    )
    normalized, errors = normalize_protein(protein)
    if errors:
        result.status = "invalid_input"
        result.error = "；".join(errors)
        result.raw_text = fasta_text(protein_id, protein)
        return result
    try:
        response = create_session().post(
            "https://nls-mapper.iab.keio.ac.jp/cgi-bin/NLS_Mapper_y.cgi",
            data={"typedseq": normalized, "cut_off": str(cutoff), "linker": linker, ".submit": "Predict NLS"},
            timeout=(10, 90),
        )
        response.raise_for_status()
        text = BeautifulSoup(response.text, "html.parser").get_text("\n", strip=True)
        regions, classification, summary = _parse_cnls_result(response.text, normalized)
        result.status = "matched"
        result.classification = classification
        result.summary = summary
        result.regions = regions
        result.raw_text = text
        result.raw_html = response.text
        result.result_url = response.url
    except requests.RequestException as exc:
        result.status = "failed"
        result.error = f"官网请求失败：{type(exc).__name__}: {exc}"
        result.raw_text = "Manual submission FASTA\n" + fasta_text(protein_id, normalized)
    result.attempts = [
        PredictionProviderAttempt(
            result.provider,
            result.status,
            result.provider_job_id,
            result.result_url,
            result.error,
            started,
            _now(),
        )
    ]
    return result


def nlstradamus_binary() -> Path | None:
    base = Path(__file__).resolve().parent / "vendor" / "nlstradamus"
    candidates = [
        base / "bin" / ("NLStradamus.exe" if os.name == "nt" else "NLStradamus"),
        base / ("NLStradamus.exe" if os.name == "nt" else "NLStradamus"),
    ]
    return next((path for path in candidates if path.is_file() and (os.name == "nt" or os.access(path, os.X_OK))), None)


def run_nlstradamus(
    protein_id: str,
    protein: str,
    model: int = 1,
    cutoff: float = 0.6,
) -> PredictionResult:
    started = _now()
    result = PredictionResult(
        protein_id=protein_id,
        tool="NLStradamus 1.8",
        version="1.8",
        parameters={"model": model, "posterior_cutoff": cutoff},
        result_url=TOOL_URLS["NLStradamus 1.8"],
        provider="local",
    )
    normalized, errors = normalize_protein(protein)
    if errors:
        result.status = "invalid_input"
        result.error = "；".join(errors)
        result.raw_text = fasta_text(protein_id, protein)
        return result
    binary = nlstradamus_binary()
    if binary is None:
        result.status = "unavailable"
        result.error = "当前平台未找到已编译的 NLStradamus 1.8 辅助程序。"
        result.raw_text = fasta_text(protein_id, normalized)
        result.attempts = [
            PredictionProviderAttempt("local", result.status, error=result.error, started_at=started, finished_at=_now())
        ]
        return result
    try:
        with tempfile.TemporaryDirectory(prefix="mybiotools-nlstradamus-") as directory:
            fasta_path = Path(directory) / "query.fasta"
            fasta_path.write_text(fasta_text(protein_id, normalized), encoding="utf-8")
            completed = subprocess.run(
                [str(binary), "-i", str(fasta_path), "-t", str(cutoff), "-m", str(model), "-tab"],
                check=False,
                capture_output=True,
                text=True,
                timeout=120,
            )
        if completed.returncode != 0:
            result.status = "failed"
            result.error = completed.stderr.strip() or f"NLStradamus 返回码 {completed.returncode}。"
            result.attempts = [
                PredictionProviderAttempt("local", result.status, error=result.error, started_at=started, finished_at=_now())
            ]
            return result
        raw = completed.stdout.strip()
        result.raw_text = raw
        regions: list[PredictionRegion] = []
        for line in raw.splitlines():
            if not line.strip() or line.casefold().startswith("sequence"):
                continue
            fields = line.split("\t")
            if len(fields) < 6:
                continue
            if fields[1].casefold() == "posterior":
                try:
                    score = float(fields[2])
                    start, end = int(fields[3]), int(fields[4])
                except ValueError:
                    continue
                if 1 <= start <= end <= len(normalized):
                    regions.append(PredictionRegion("NLS", start, end, score, normalized[start - 1:end]))
        result.regions = regions
        result.status = "matched"
        result.classification = "NLS detected" if regions else "no NLS"
        result.summary = f"NLStradamus predicted {len(regions)} NLS region(s) at cutoff {cutoff}."
    except (OSError, subprocess.SubprocessError) as exc:
        result.status = "failed"
        result.error = f"本地预测失败：{type(exc).__name__}: {exc}"
        if not result.raw_text:
            result.raw_text = fasta_text(protein_id, normalized)
    result.attempts = [
        PredictionProviderAttempt("local", result.status, error=result.error, started_at=started, finished_at=_now())
    ]
    return result


def _biolib_job_id(job: object) -> str:
    for name in ("id", "job_id", "result_id", "uri"):
        value = getattr(job, name, "")
        if value:
            return str(value).rsplit("/", 1)[-1]
    return ""


def _biolib_stdout(job: object) -> str:
    getter = getattr(job, "get_stdout", None)
    if not callable(getter):
        return ""
    try:
        output = getter()
        return output.decode("utf-8", "replace") if isinstance(output, bytes) else str(output)
    except Exception:
        return ""


def _collect_biolib_outputs(job: object) -> dict[str, bytes]:
    artifacts: dict[str, bytes] = {}
    lister = getattr(job, "list_output_files", None)
    if not callable(lister):
        return artifacts
    try:
        files = lister() or []
    except Exception:
        return artifacts
    for index, item in enumerate(files, start=1):
        name = str(getattr(item, "path", "") or item)
        try:
            handle = job.get_output_file(name)
            payload = handle.get_data()
            if isinstance(payload, str):
                payload = payload.encode("utf-8")
            if not isinstance(payload, bytes):
                payload = bytes(payload)
            safe_parts = [safe_file_stem(part, f"item_{index}") for part in Path(name).parts if part not in {"/", ".", ".."}]
            artifacts["/".join(safe_parts) or f"item_{index}"] = payload
        except Exception:
            continue
    return artifacts


def _biolib_text(artifacts: dict[str, bytes], stdout: str) -> str:
    parts = [stdout] if stdout else []
    text_suffixes = (".txt", ".tsv", ".csv", ".gff", ".gff3", ".3line", ".json", ".out")
    for name, payload in artifacts.items():
        if name.casefold().endswith(text_suffixes):
            parts.append(f"SOURCE {name}\n{payload.decode('utf-8', 'replace')}")
    return "\n\n".join(parts)


def _build_biolib_results(
    tool: str,
    proteins: list[tuple[str, str]],
    parameters: dict[str, object],
    raw_text: str,
    app_url: str,
    attempt: PredictionProviderAttempt,
) -> list[PredictionResult]:
    protein_map = dict(proteins)
    table = _parse_probability_table(raw_text)
    gff_regions = _parse_gff_regions(raw_text, protein_map)
    deep_classes, deep_regions = _parse_deep_topology(raw_text, protein_map)
    results: list[PredictionResult] = []
    for protein_id, protein in proteins:
        info = table.get(protein_id, {})
        if tool == "DeepTMHMM 1.0":
            classification = deep_classes.get(protein_id, "")
            regions = deep_regions.get(protein_id, []) or gff_regions.get(protein_id, [])
        else:
            classification = str(info.get("classification") or "")
            regions = gff_regions.get(protein_id, [])
            if tool == "SignalP 6.0" and classification.upper() == "SP":
                probabilities = dict(info.get("probabilities") or {})
                candidates = [(key, value) for key, value in probabilities.items() if key.casefold() != "other"]
                if candidates:
                    label = max(candidates, key=lambda item: item[1])[0]
                    match = re.search(r"\(([^)]+)\)", label)
                    classification = match.group(1) if match else label
        cleavage = info.get("cleavage_site")
        if isinstance(cleavage, int) and 1 <= cleavage <= len(protein) and not regions:
            regions = [
                PredictionRegion(
                    "signal peptide",
                    1,
                    cleavage,
                    sequence=protein[:cleavage],
                    note="Predicted cleavage position",
                )
            ]
        usable = bool(classification) or bool(regions) or bool(info)
        results.append(
            PredictionResult(
                protein_id=protein_id,
                tool=tool,
                version=tool.rsplit(" ", 1)[-1],
                status="matched" if usable else "failed",
                classification=classification,
                summary=_classification(tool, classification or raw_text)[1] if usable else "",
                parameters=dict(parameters),
                regions=regions,
                result_url=app_url,
                raw_text=raw_text,
                error="" if usable else "BioLib 返回文件中没有可解析的结构化结果。",
                provider="biolib",
                provider_job_id=attempt.job_id,
                fallback_used=True,
                probabilities=dict(info.get("probabilities") or {}),
                attempts=[attempt],
            )
        )
    return results


def run_biolib_batch(
    tool: str,
    proteins: list[tuple[str, str]],
    parameters: dict[str, object] | None = None,
    timeout_seconds: int = BIOLIB_TIMEOUT_SECONDS,
    cancel_check: Callable[[], bool] | None = None,
) -> PredictionExecution:
    options = dict(parameters or {})
    app_uri, fasta_flag = BIOLIB_APPS[tool]
    app_url = f"https://dtu.biolib.com/{'SignalP-6' if tool == 'SignalP 6.0' else 'DeepTMHMM'}/"
    started = _now()
    if biolib is None:
        error = "当前运行时缺少 pybiolib，无法使用 BioLib 备用通道。"
        attempt = PredictionProviderAttempt("biolib", "unavailable", "", app_url, error, started, _now())
        return _failed_execution(tool, proteins, options, "failed", error, attempt)

    valid: list[tuple[str, str]] = []
    invalid: list[PredictionResult] = []
    for protein_id, protein in proteins:
        normalized, errors = normalize_protein(protein)
        if errors:
            invalid.append(
                PredictionResult(
                    protein_id=protein_id,
                    tool=tool,
                    version=tool.rsplit(" ", 1)[-1],
                    status="invalid_input",
                    parameters=options,
                    raw_text=fasta_text(protein_id, protein),
                    error="；".join(errors),
                )
            )
        else:
            valid.append((protein_id, normalized))
    if not valid:
        return PredictionExecution(results=invalid)

    job = None
    artifacts: dict[str, bytes] = {}
    try:
        with tempfile.TemporaryDirectory(prefix="mybiotools-biolib-") as directory:
            path = Path(directory) / "input.fasta"
            path.write_text("".join(fasta_text(protein_id, protein) for protein_id, protein in valid), encoding="utf-8")
            app = biolib.load(app_uri)
            args = f"{fasta_flag} input.fasta"
            if tool == "SignalP 6.0":
                mode = str(options.get("mode", "fast"))
                args += f" --output_dir output --format all --organism eukarya --mode {mode}"
            with _biolib_cwd_lock:
                previous = os.getcwd()
                try:
                    os.chdir(directory)
                    try:
                        job = app.cli(args=args, blocking=False)
                    except TypeError:
                        job = app.cli(args=args)
                finally:
                    os.chdir(previous)
            if cancel_check and cancel_check():
                error = "用户取消。"
                attempt = PredictionProviderAttempt(
                    "biolib", "cancelled", _biolib_job_id(job), app_url, error, started, _now()
                )
                execution = _failed_execution(tool, valid, options, "cancelled", error, attempt)
                execution.results.extend(invalid)
                return execution
            deadline = time.monotonic() + max(0, timeout_seconds)
            is_finished = getattr(job, "is_finished", None)
            if callable(is_finished):
                while not is_finished():
                    if cancel_check and cancel_check():
                        error = "用户取消；已停止本地轮询，将忽略外站迟到结果。"
                        attempt = PredictionProviderAttempt(
                            "biolib", "cancelled", _biolib_job_id(job), app_url, error, started, _now()
                        )
                        execution = _failed_execution(tool, valid, options, "cancelled", error, attempt)
                        execution.results.extend(invalid)
                        return execution
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"BioLib 等待超过 {timeout_seconds} 秒。")
                    time.sleep(1.0)
            else:
                waiter = getattr(job, "wait", None)
                if callable(waiter):
                    waiter(timeout=timeout_seconds)
            exit_getter = getattr(job, "get_exit_code", None)
            if callable(exit_getter):
                exit_code = exit_getter()
                if exit_code not in (None, 0):
                    raise RuntimeError(f"BioLib job exit code {exit_code}: {_biolib_stdout(job)[:500]}")
            artifacts = _collect_biolib_outputs(job)
            raw_text = _biolib_text(artifacts, _biolib_stdout(job))
            if not artifacts:
                raise RuntimeError(f"BioLib 未返回输出文件：{raw_text[:500]}")
            attempt = PredictionProviderAttempt(
                "biolib", "matched", _biolib_job_id(job), app_url, "", started, _now()
            )
            results = _build_biolib_results(tool, valid, options, raw_text, app_url, attempt)
            if not all(result.status == "matched" for result in results):
                raise RuntimeError("BioLib 输出存在无法解析的蛋白记录。")
            prefixed = {
                f"biolib/{safe_file_stem(tool)}/{name}": payload for name, payload in artifacts.items()
            }
            return PredictionExecution(results=results + invalid, raw_artifacts=prefixed)
    except Exception as exc:
        error = _safe_error(f"BioLib 备用任务失败：{type(exc).__name__}: {exc}")
        attempt = PredictionProviderAttempt(
            "biolib", "failed", _biolib_job_id(job) if job is not None else "", app_url, error, started, _now()
        )
        execution = _failed_execution(tool, valid, options, "failed", error, attempt)
        execution.raw_artifacts = {
            f"biolib/{safe_file_stem(tool)}/{name}": payload for name, payload in artifacts.items()
        }
        execution.results.extend(invalid)
        return execution


def run_resilient_batch(
    tool: str,
    proteins: list[tuple[str, str]],
    parameters: dict[str, object] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    status_callback: Callable[[str], None] | None = None,
) -> PredictionExecution:
    if status_callback:
        status_callback("正在提交并等待 DTU 官网")
    direct = run_dtu_batch(
        tool,
        proteins,
        parameters,
        timeout_seconds=DTU_DIRECT_TIMEOUT_SECONDS,
        cancel_check=cancel_check,
    )
    if all(result.status in {"matched", "invalid_input"} for result in direct.results):
        return direct
    if any(result.status == "cancelled" for result in direct.results) or (cancel_check and cancel_check()):
        return direct
    if status_callback:
        status_callback("DTU 不可用，正在切换 BioLib 备用通道")
    fallback = run_biolib_batch(
        tool,
        proteins,
        parameters,
        timeout_seconds=BIOLIB_TIMEOUT_SECONDS,
        cancel_check=cancel_check,
    )
    direct_by_id = {result.protein_id: result for result in direct.results}
    for result in fallback.results:
        previous = direct_by_id.get(result.protein_id)
        if previous:
            result.attempts = [*previous.attempts, *result.attempts]
            result.fallback_used = True
    fallback.raw_artifacts = {**direct.raw_artifacts, **fallback.raw_artifacts}
    return fallback


def run_one_prediction(
    tool: str,
    protein_id: str,
    protein: str,
    options: dict[str, object] | None = None,
) -> PredictionResult:
    options = dict(options or {})
    with _service_locks[tool]:
        if tool in DTU_CONFIGS:
            return run_dtu_prediction(tool, protein_id, protein, options)
        if tool == "cNLS Mapper":
            return run_cnls_mapper(
                protein_id,
                protein,
                cutoff=float(options.get("cutoff", 5.0)),
                linker=str(options.get("linker", "Within terminal 60-amino-acid regions")),
            )
        if tool == "NLStradamus 1.8":
            return run_nlstradamus(
                protein_id,
                protein,
                model=int(options.get("model", 1)),
                cutoff=float(options.get("cutoff", 0.6)),
            )
    raise ValueError(f"未知预测工具：{tool}")


def run_selected_predictions(
    proteins: list[tuple[str, str]],
    selected_tools: list[str] | tuple[str, ...],
    tool_options: dict[str, dict[str, object]] | None = None,
    max_workers: int = 2,
    progress_callback: Callable[[int, int, str], None] | None = None,
    item_progress_callback: Callable[[str, int, int, str, bool], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> PredictionExecution:
    total_tasks = len(proteins) * len(selected_tools)
    results: dict[tuple[str, str], PredictionResult] = {}
    raw_artifacts: dict[str, bytes] = {}
    tool_totals = {tool: len(proteins) for tool in selected_tools}
    tool_completed = {tool: 0 for tool in selected_tools}
    tool_failed = {tool: 0 for tool in selected_tools}

    def provider_status(tool: str, label: str) -> None:
        if item_progress_callback:
            item_progress_callback(tool, 0, tool_totals[tool], label, False)

    with ThreadPoolExecutor(max_workers=max(1, min(2, max_workers))) as executor:
        futures = {}
        for tool in selected_tools:
            if tool in BIOLIB_APPS:
                future = executor.submit(
                    run_resilient_batch,
                    tool,
                    proteins,
                    (tool_options or {}).get(tool, {}),
                    cancel_check,
                    lambda label, selected_tool=tool: provider_status(selected_tool, label),
                )
                futures[future] = ("", tool, True)
            else:
                for protein_id, sequence in proteins:
                    future = executor.submit(
                        run_one_prediction,
                        tool,
                        protein_id,
                        sequence,
                        (tool_options or {}).get(tool, {}),
                    )
                    futures[future] = (protein_id, tool, False)
        completed = 0
        for future in as_completed(futures):
            protein_id, tool, is_batch = futures[future]
            try:
                value = future.result()
                if is_batch:
                    execution = value
                    raw_artifacts.update(execution.raw_artifacts)
                    batch_results = execution.results
                else:
                    batch_results = [value]
            except Exception as exc:
                affected = proteins if is_batch else [(protein_id, dict(proteins).get(protein_id, ""))]
                batch_results = [
                    PredictionResult(
                        protein_id=item_id,
                        tool=tool,
                        version=tool.rsplit(" ", 1)[-1],
                        status="failed",
                        result_url=TOOL_URLS.get(tool, ""),
                        error=f"未处理异常：{type(exc).__name__}: {exc}",
                    )
                    for item_id, _ in affected
                ]
            for item in batch_results:
                results[(item.protein_id, tool)] = item
                completed += 1
                tool_completed[tool] += 1
                if item.status not in {"matched", "partial"}:
                    tool_failed[tool] += 1
            if item_progress_callback:
                item_progress_callback(
                    tool,
                    tool_completed[tool],
                    tool_totals[tool],
                    f"{tool_completed[tool]}/{tool_totals[tool]}：已取得结构化结果",
                    bool(tool_failed[tool]),
                )
            if progress_callback:
                progress_callback(completed, total_tasks, f"{tool_completed[tool]}/{tool_totals[tool]} · {tool}")
            if cancel_check and cancel_check():
                for pending in futures:
                    pending.cancel()
                break
    ordered: list[PredictionResult] = []
    for protein_id, _ in proteins:
        for tool in selected_tools:
            ordered.append(
                results.get(
                    (protein_id, tool),
                    PredictionResult(
                        protein_id=protein_id,
                        tool=tool,
                        version=tool.rsplit(" ", 1)[-1],
                        status="cancelled" if cancel_check and cancel_check() else "failed",
                        result_url=TOOL_URLS.get(tool, ""),
                        error="用户取消。" if cancel_check and cancel_check() else "任务未返回结果。",
                    ),
                )
            )
    return PredictionExecution(results=ordered, raw_artifacts=raw_artifacts)


__all__ = [
    "PREDICTORS",
    "TOOL_URLS",
    "create_session",
    "run_biolib_batch",
    "run_dtu_batch",
    "run_cnls_mapper",
    "run_dtu_prediction",
    "run_nlstradamus",
    "run_resilient_batch",
    "run_selected_predictions",
]
