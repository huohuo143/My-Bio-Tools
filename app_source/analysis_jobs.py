"""In-process single-worker queue for background rice analysis jobs."""

from __future__ import annotations

from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import datetime
import threading
import traceback
import uuid
from typing import Callable


PHASE_WEIGHTS = OrderedDict(
    [
        ("mapping", 5.0),
        ("sequences", 30.0),
        ("ricedata", 15.0),
        ("efp", 20.0),
        ("lab_omics", 15.0),
        ("predictions", 20.0),
        ("protein_domains", 10.0),
        ("gene_structure", 10.0),
        ("promoter_regulation", 10.0),
        ("variation", 10.0),
        ("mirna_rnai", 10.0),
        ("literature_evidence", 10.0),
        ("interpretation", 10.0),
        ("report", 10.0),
    ]
)
PHASE_LABELS = OrderedDict(
    [
        ("mapping", "ID 解析与映射"),
        ("sequences", "序列获取"),
        ("ricedata", "RiceData"),
        ("efp", "Rice eFP"),
        ("lab_omics", "实验室已分析多组学"),
        ("predictions", "蛋白定位预测"),
        ("protein_domains", "蛋白结构域与功能位点"),
        ("gene_structure", "基因结构与转录本"),
        ("promoter_regulation", "启动子与上游调控"),
        ("variation", "自然变异与单倍型"),
        ("mirna_rnai", "miRNA/RNAi"),
        ("literature_evidence", "文献与遗传证据"),
        ("interpretation", "结果解读"),
        ("report", "报告生成"),
    ]
)
EFP_SOURCE_LABELS = {
    "rice_rma": "Developmental atlas (RMA)",
    "ricestress_rma": "Stress atlas (RMA)",
}
TERMINAL_STATUSES = {"completed", "completed_with_warnings", "failed", "cancelled"}


@dataclass(frozen=True)
class RiceGeneAnalysisRequest:
    project_name: str
    mode: str
    input_type: str
    text: str
    selected_types: tuple[str, ...]
    promoter_length: int
    transcript_scope: str
    selected_predictors: tuple[str, ...]
    signalp_mode: str
    cnls_cutoff: float
    nlstradamus_model: int
    nlstradamus_cutoff: float
    max_workers: int
    selected_candidate: str = ""
    include_ricedata: bool = True
    ricedata_depth: str = "adaptive"
    include_efp: bool = True
    efp_data_sources: tuple[str, ...] = ("rice_rma", "ricestress_rma")
    include_lab_omics: bool = True
    selected_deep_analyses: tuple[str, ...] = ()
    promoter_pvalue: float = 1e-4
    variation_vcf_name: str = ""
    variation_vcf_bytes: bytes = b""
    sample_groups_name: str = ""
    sample_groups_bytes: bytes = b""
    mirna_mode: str = "known_mirna"
    custom_srna_text: str = ""
    mirna_expectation: float = 5.0
    mirna_max_upe: float = 25.0
    mirna_offtargets: bool = False
    evidence_file_name: str = ""
    evidence_file_bytes: bytes = b""
    interpretation_mode: str = "rules"
    interpretation_provider: str = ""
    interpretation_base_url: str = ""
    interpretation_model: str = ""
    interpretation_api_key: str = field(default="", repr=False, compare=False)


@dataclass(frozen=True)
class ProgressItemSnapshot:
    key: str
    label: str
    status: str = "pending"
    progress: float = 0.0
    detail: str = "等待中"
    children: tuple["ProgressItemSnapshot", ...] = ()


@dataclass(frozen=True)
class AnalysisJobSnapshot:
    job_id: str
    project_name: str
    status: str
    progress: float
    stage: str
    detail: str
    created_at: str
    started_at: str = ""
    finished_at: str = ""
    queue_position: int = 0
    error: str = ""
    progress_items: tuple[ProgressItemSnapshot, ...] = ()


@dataclass
class _Job:
    snapshot: AnalysisJobSnapshot
    request: RiceGeneAnalysisRequest
    runner: "JobRunner"
    cancel_event: threading.Event = field(default_factory=threading.Event)
    future: Future | None = None
    bundle: object | None = None
    artifacts: dict[str, object] | None = None
    traceback_text: str = ""


class JobCancelled(RuntimeError):
    pass


class ProgressReporter:
    def __init__(
        self,
        manager: "AnalysisJobManager",
        job_id: str,
        selected_phases: tuple[str, ...],
        cancel_event: threading.Event,
    ) -> None:
        self.manager = manager
        self.job_id = job_id
        self.cancel_event = cancel_event
        self.selected_phases = tuple(phase for phase in PHASE_WEIGHTS if phase in selected_phases)
        self.fractions = {phase: 0.0 for phase in self.selected_phases}
        self.weight_total = sum(PHASE_WEIGHTS[phase] for phase in self.selected_phases) or 1.0

    def check_cancel(self) -> None:
        if self.cancel_event.is_set():
            raise JobCancelled("用户取消任务")

    def is_cancelled(self) -> bool:
        return self.cancel_event.is_set()

    def update(self, phase: str, completed: int | float, total: int | float, detail: str = "") -> None:
        self.check_cancel()
        if phase not in self.fractions:
            return
        denominator = max(float(total), 1.0)
        fraction = min(1.0, max(0.0, float(completed) / denominator))
        self.fractions[phase] = max(self.fractions[phase], fraction)
        progress = sum(PHASE_WEIGHTS[key] * value for key, value in self.fractions.items()) / self.weight_total
        self.manager._update_progress(
            self.job_id,
            progress=progress,
            stage=phase,
            detail=detail,
            phase_progress=fraction,
        )

    def complete(self, phase: str, detail: str = "", warning: bool = False) -> None:
        self.check_cancel()
        if phase not in self.fractions:
            return
        self.fractions[phase] = 1.0
        progress = sum(PHASE_WEIGHTS[key] * value for key, value in self.fractions.items()) / self.weight_total
        self.manager._update_progress(
            self.job_id,
            progress=progress,
            stage=phase,
            detail=detail,
            phase_progress=1.0,
            phase_status="completed_with_warnings" if warning else "completed",
        )

    def update_item(
        self,
        phase: str,
        item_key: str,
        completed: int | float,
        total: int | float,
        detail: str = "",
        warning: bool = False,
    ) -> None:
        self.check_cancel()
        denominator = max(float(total), 1.0)
        fraction = min(1.0, max(0.0, float(completed) / denominator))
        status = "running"
        if fraction >= 1.0:
            status = "completed_with_warnings" if warning else "completed"
        self.manager._update_item_progress(
            self.job_id,
            phase=phase,
            item_key=item_key,
            progress=fraction,
            detail=detail,
            status=status,
        )

    def complete_item(
        self,
        phase: str,
        item_key: str,
        detail: str = "",
        warning: bool = False,
    ) -> None:
        self.update_item(phase, item_key, 1, 1, detail, warning=warning)


JobRunner = Callable[[RiceGeneAnalysisRequest, ProgressReporter], tuple[object, dict[str, object]]]


class AnalysisJobManager:
    def __init__(self, max_history: int = 20) -> None:
        self.max_history = max(1, int(max_history))
        self._lock = threading.RLock()
        self._jobs: OrderedDict[str, _Job] = OrderedDict()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="rice-analysis")

    @staticmethod
    def _now() -> str:
        return datetime.now().astimezone().isoformat(timespec="seconds")

    def submit(self, request: RiceGeneAnalysisRequest, runner: JobRunner) -> str:
        job_id = uuid.uuid4().hex[:12]
        snapshot = AnalysisJobSnapshot(
            job_id=job_id,
            project_name=request.project_name,
            status="queued",
            progress=0.0,
            stage="queued",
            detail="等待前一个项目完成",
            created_at=self._now(),
            progress_items=self._initial_progress_items(request),
        )
        job = _Job(snapshot=snapshot, request=request, runner=runner)
        with self._lock:
            self._jobs[job_id] = job
            job.future = self._executor.submit(self._run_job, job_id)
            self._prune_locked()
        return job_id

    def _selected_phases(self, request: RiceGeneAnalysisRequest) -> tuple[str, ...]:
        phases = ["mapping", "sequences"]
        if request.include_ricedata:
            phases.append("ricedata")
        if request.include_efp:
            phases.append("efp")
        if request.include_lab_omics:
            phases.append("lab_omics")
        if request.selected_predictors:
            phases.append("predictions")
        phases.extend(phase for phase in request.selected_deep_analyses if phase in PHASE_LABELS)
        if request.interpretation_mode == "llm":
            phases.append("interpretation")
        phases.append("report")
        return tuple(phases)

    def _initial_progress_items(self, request: RiceGeneAnalysisRequest) -> tuple[ProgressItemSnapshot, ...]:
        selected = set(self._selected_phases(request))
        items: list[ProgressItemSnapshot] = []
        for phase, label in PHASE_LABELS.items():
            if phase not in selected:
                continue
            children: tuple[ProgressItemSnapshot, ...] = ()
            if phase == "efp":
                children = tuple(
                    ProgressItemSnapshot(key=source, label=EFP_SOURCE_LABELS.get(source, source))
                    for source in request.efp_data_sources
                )
            elif phase == "predictions":
                children = tuple(
                    ProgressItemSnapshot(key=predictor, label=predictor)
                    for predictor in request.selected_predictors
                )
            items.append(ProgressItemSnapshot(key=phase, label=label, children=children))
        return tuple(items)

    @staticmethod
    def _replace_progress_item(
        items: tuple[ProgressItemSnapshot, ...],
        phase: str,
        *,
        progress: float | None = None,
        detail: str | None = None,
        status: str | None = None,
        item_key: str = "",
    ) -> tuple[ProgressItemSnapshot, ...]:
        updated: list[ProgressItemSnapshot] = []
        for item in items:
            if item.key != phase:
                updated.append(item)
                continue
            if item_key:
                children = tuple(
                    replace(
                        child,
                        progress=max(child.progress, progress) if progress is not None else child.progress,
                        detail=detail if detail is not None and child.key == item_key else child.detail,
                        status=status if status is not None and child.key == item_key else child.status,
                    )
                    if child.key == item_key
                    else child
                    for child in item.children
                )
                updated.append(replace(item, children=children))
            else:
                updated.append(
                    replace(
                        item,
                        progress=max(item.progress, progress) if progress is not None else item.progress,
                        detail=detail if detail is not None else item.detail,
                        status=status if status is not None else item.status,
                    )
                )
        return tuple(updated)

    def _run_job(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            if job.cancel_event.is_set():
                job.snapshot = replace(
                    job.snapshot,
                    status="cancelled",
                    stage="cancelled",
                    detail="任务在排队时已取消",
                    finished_at=self._now(),
                )
                return
            job.snapshot = replace(
                job.snapshot,
                status="running",
                stage="mapping",
                detail="正在验证输入与解析 ID",
                started_at=self._now(),
                progress_items=self._replace_progress_item(
                    job.snapshot.progress_items,
                    "mapping",
                    status="running",
                    detail="正在验证输入与解析 ID",
                ),
            )
        reporter = ProgressReporter(self, job_id, self._selected_phases(job.request), job.cancel_event)
        try:
            bundle, artifacts = job.runner(job.request, reporter)
            reporter.check_cancel()
            warnings = bool(getattr(bundle, "warnings", []))
            with self._lock:
                job.bundle = bundle
                job.artifacts = artifacts
                job.snapshot = replace(
                    job.snapshot,
                    status="completed_with_warnings" if warnings else "completed",
                    progress=1.0,
                    stage="completed",
                    detail="分析与报告已生成" if not warnings else "已完成，请查看警告",
                    finished_at=self._now(),
                )
        except JobCancelled:
            with self._lock:
                progress_items = self._replace_progress_item(
                    job.snapshot.progress_items,
                    job.snapshot.stage,
                    status="cancelled",
                    detail="任务已取消",
                )
                job.snapshot = replace(
                    job.snapshot,
                    status="cancelled",
                    detail="任务已取消",
                    finished_at=self._now(),
                    progress_items=progress_items,
                )
        except Exception as exc:
            with self._lock:
                job.traceback_text = traceback.format_exc()
                error = f"{type(exc).__name__}: {exc}"
                progress_items = self._replace_progress_item(
                    job.snapshot.progress_items,
                    job.snapshot.stage,
                    status="failed",
                    detail=error,
                )
                job.snapshot = replace(
                    job.snapshot,
                    status="failed",
                    detail=error,
                    error=error,
                    finished_at=self._now(),
                    progress_items=progress_items,
                )
        finally:
            # API keys are only needed by the worker and must not remain in job history.
            with self._lock:
                job.request = replace(job.request, interpretation_api_key="")
                self._prune_locked()

    def _update_progress(
        self,
        job_id: str,
        progress: float,
        stage: str,
        detail: str,
        phase_progress: float | None = None,
        phase_status: str | None = None,
    ) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.snapshot.status not in {"queued", "running"}:
                return
            job.snapshot = replace(
                job.snapshot,
                progress=max(job.snapshot.progress, min(1.0, progress)),
                stage=stage,
                detail=detail or job.snapshot.detail,
                progress_items=self._replace_progress_item(
                    job.snapshot.progress_items,
                    stage,
                    progress=phase_progress,
                    detail=detail or None,
                    status=phase_status or "running",
                ),
            )

    def _update_item_progress(
        self,
        job_id: str,
        phase: str,
        item_key: str,
        progress: float,
        detail: str,
        status: str,
    ) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.snapshot.status not in {"queued", "running"}:
                return
            job.snapshot = replace(
                job.snapshot,
                progress_items=self._replace_progress_item(
                    job.snapshot.progress_items,
                    phase,
                    progress=min(1.0, max(0.0, progress)),
                    detail=detail or None,
                    status=status,
                    item_key=item_key,
                ),
            )

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.snapshot.status in TERMINAL_STATUSES:
                return False
            job.cancel_event.set()
            if job.snapshot.status == "queued" and job.future and job.future.cancel():
                job.snapshot = replace(
                    job.snapshot,
                    status="cancelled",
                    detail="任务在排队时已取消",
                    finished_at=self._now(),
                    progress_items=self._replace_progress_item(
                        job.snapshot.progress_items,
                        "mapping",
                        status="cancelled",
                        detail="任务在排队时已取消",
                    ),
                )
            else:
                job.snapshot = replace(
                    job.snapshot,
                    detail="正在等待当前网络请求结束后取消…",
                    progress_items=self._replace_progress_item(
                        job.snapshot.progress_items,
                        job.snapshot.stage,
                        detail="正在等待当前网络请求结束后取消…",
                    ),
                )
            return True

    def retry(self, job_id: str) -> str | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.snapshot.status not in {"failed", "cancelled"}:
                return None
            request = replace(job.request, project_name=f"{job.request.project_name} · 重试")
            runner = job.runner
        return self.submit(request, runner)

    def snapshots(self) -> list[AnalysisJobSnapshot]:
        with self._lock:
            queued_ids = [job_id for job_id, job in self._jobs.items() if job.snapshot.status == "queued"]
            result: list[AnalysisJobSnapshot] = []
            for job_id, job in reversed(self._jobs.items()):
                position = queued_ids.index(job_id) + 1 if job_id in queued_ids else 0
                result.append(replace(job.snapshot, queue_position=position))
            return result

    def get_request(self, job_id: str) -> RiceGeneAnalysisRequest | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return job.request if job else None

    def get_result(self, job_id: str) -> tuple[object | None, dict[str, object] | None, str]:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None, None, ""
            return job.bundle, job.artifacts, job.traceback_text

    def _prune_locked(self) -> None:
        if len(self._jobs) <= self.max_history:
            return
        for job_id in list(self._jobs):
            if len(self._jobs) <= self.max_history:
                break
            if self._jobs[job_id].snapshot.status in TERMINAL_STATUSES:
                self._jobs.pop(job_id, None)

    def shutdown(self, wait: bool = True) -> None:
        self._executor.shutdown(wait=wait, cancel_futures=True)


JOB_MANAGER = AnalysisJobManager(max_history=20)


__all__ = [
    "AnalysisJobManager",
    "AnalysisJobSnapshot",
    "EFP_SOURCE_LABELS",
    "JOB_MANAGER",
    "JobCancelled",
    "PHASE_LABELS",
    "ProgressItemSnapshot",
    "ProgressReporter",
    "RiceGeneAnalysisRequest",
]
