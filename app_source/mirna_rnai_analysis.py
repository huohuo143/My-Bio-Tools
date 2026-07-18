"""psRNATarget adapter for known/custom small-RNA target prediction."""

from __future__ import annotations

import io
import re
from datetime import datetime, timezone

from bs4 import BeautifulSoup
import requests


PSRNATARGET_URL = "https://www.zhaolab.org/psRNATarget/analysis"


def parse_psrnatarget_html(html: str, source_url: str, input_id: str = "") -> list[dict[str, object]]:
    soup = BeautifulSoup(html, "html.parser")
    queried_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    aliases = {
        "smallrna": "small_rna", "mirna": "small_rna", "target": "target_transcript",
        "expectation": "expectation", "upe": "upe", "start": "target_start", "end": "target_end",
        "inhibition": "inhibition", "alignment": "alignment",
    }
    rows = []
    for table in soup.find_all("table"):
        headers = [re.sub(r"\W+", "", cell.get_text(" ", strip=True)).casefold() for cell in table.find_all("th")]
        if not headers or not any("expectation" in header for header in headers):
            continue
        for tr in table.find_all("tr"):
            cells = [cell.get_text(" ", strip=True) for cell in tr.find_all("td")]
            if len(cells) != len(headers):
                continue
            parsed = {}
            for header, value in zip(headers, cells):
                key = next((mapped for token, mapped in aliases.items() if token in header), header or "field")
                parsed[key] = value
            rows.append({"input_id": input_id, **parsed, "prediction_type": "computational prediction", "evidence_status": "计算预测", "source_url": source_url, "queried_at": queried_at, "status": "matched", "error": ""})
    return rows


def run_psrnatarget(
    targets: list[dict[str, str]],
    *,
    mode: str,
    small_rna_text: str = "",
    expectation: float = 5.0,
    max_upe: float = 25.0,
    known_mirna_library: str = "Oryza sativa",
    off_target: bool = False,
    session: requests.Session | None = None,
) -> tuple[list[dict[str, object]], list[dict[str, object]], dict[str, bytes], list[str]]:
    client = session or requests.Session(); result_rows, off_rows, raw, warnings = [], [], {}, []
    function = "2" if mode == "known_mirna" else "3"
    for target in targets:
        transcript = target.get("transcript_id") or target.get("input_id") or "target"
        sequence = re.sub(r"\s+", "", target.get("sequence", "")).upper().replace("T", "U")
        if not sequence:
            warnings.append(f"{transcript} 无 transcript 序列，未运行 psRNATarget。")
            continue
        try:
            landing_url = f"{PSRNATARGET_URL}?function={function}"
            landing = client.get(landing_url, timeout=30); landing.raise_for_status()
            soup = BeautifulSoup(landing.text, "html.parser")
            form = soup.find("form")
            if form is None:
                raise ValueError("psRNATarget submission form not found")
            action = requests.compat.urljoin(landing_url, form.get("action") or landing_url)
            data = {}
            for field in form.find_all(["input", "select", "textarea"]):
                name = field.get("name")
                if not name or field.get("type") in {"file", "submit"}:
                    continue
                if field.name == "select":
                    option = field.find("option", selected=True) or field.find("option")
                    data[name] = option.get("value", "") if option else ""
                else:
                    data[name] = field.get("value", "")
            for key in list(data):
                lower = key.casefold()
                if "expect" in lower: data[key] = str(expectation)
                elif "upe" in lower: data[key] = str(max_upe)
                elif mode == "known_mirna" and ("srna" in lower or "mirna" in lower): data[key] = known_mirna_library
            files = {"target_input": ("target.fa", f">{transcript}\n{sequence}\n".encode(), "text/plain")}
            file_fields = [field.get("name") for field in form.find_all("input", {"type": "file"}) if field.get("name")]
            if file_fields:
                files = {file_fields[-1]: ("target.fa", f">{transcript}\n{sequence}\n".encode(), "text/plain")}
                if mode != "known_mirna" and len(file_fields) > 1:
                    files[file_fields[0]] = ("small_rna.fa", small_rna_text.encode(), "text/plain")
            response = client.post(action, data=data, files=files, timeout=120); response.raise_for_status()
            stem = re.sub(r"[^A-Za-z0-9._-]+", "_", transcript)
            raw[f"{stem}/psrnatarget_submission.html"] = response.content
            rows = parse_psrnatarget_html(response.text, response.url, target.get("input_id", transcript))
            result_rows.extend(rows)
            if not rows:
                warnings.append(f"{transcript} psRNATarget 任务已提交但未取得可解析的即时结果；已保留 result URL 和原始状态。")
                result_rows.append({"input_id": target.get("input_id", transcript), "small_rna": "", "target_transcript": transcript, "prediction_type": "computational prediction", "evidence_status": "计算预测", "provider_job_id": "", "result_url": response.url, "source_url": response.url, "queried_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"), "status": "submitted_no_immediate_result", "error": ""})
            if off_target and mode != "known_mirna":
                off_rows.append({"input_id": target.get("input_id", transcript), "small_rna": small_rna_text.splitlines()[0] if small_rna_text else "", "target_transcript": transcript, "status": "not_run_library_requires_provider_job", "source_url": response.url, "evidence_status": "计算预测", "error": "首版仅保留官方脱靶任务状态，不自动设计新 siRNA。"})
        except Exception as exc:
            warnings.append(f"{transcript} psRNATarget 失败：{type(exc).__name__}: {exc}")
    return result_rows, off_rows, raw, warnings
