"""Resource-aware scheduler for disposable CTF instances."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class WorkerState:
    worker_id: str
    public_ip: str
    runtime_platforms: list[str]
    cpu_runtime_allocated: float
    ram_runtime_allocated_mb: int
    disk_free_mb: int
    container_limit: int
    port_range_start: int
    port_range_end: int
    used_cpu: float = 0.0
    used_ram_mb: int = 0
    used_disk_mb: int = 0
    container_count: int = 0
    used_ports: set[int] = field(default_factory=set)
    status: str = "ready"
    draining: bool = False
    labels: dict[str, str] = field(default_factory=dict)
    features: dict[str, bool] = field(default_factory=dict)
    recent_failures: int = 0

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "WorkerState":
        return cls(
            worker_id=payload["worker_id"],
            public_ip=payload.get("public_ip", "127.0.0.1"),
            runtime_platforms=list(payload.get("runtime_platforms") or []),
            cpu_runtime_allocated=float(payload.get("cpu_runtime_allocated") or 1),
            ram_runtime_allocated_mb=int(payload.get("ram_runtime_allocated_mb") or 0),
            disk_free_mb=int(payload.get("disk_free_mb") or 0),
            container_limit=int(payload.get("container_limit") or 0),
            port_range_start=int(payload.get("port_range_start") or 20000),
            port_range_end=int(payload.get("port_range_end") or 20999),
            status=payload.get("status", "ready"),
            draining=bool(payload.get("draining", False)),
            labels=dict(payload.get("labels") or {}),
            features=dict(payload.get("features") or {}),
            recent_failures=int(payload.get("recent_failures") or 0),
        )

    def available_ports(self) -> list[int]:
        return [
            port
            for port in range(self.port_range_start, self.port_range_end + 1)
            if port not in self.used_ports
        ]


@dataclass
class InstanceRequest:
    image: str
    image_platform: str
    cpu_request: float
    memory_mb: int
    disk_mb: int
    internal_port: int = 80
    required_features: list[str] = field(default_factory=list)


@dataclass
class Placement:
    worker_id: str
    public_ip: str
    public_port: int
    score: float
    reasons: list[str] = field(default_factory=list)


def platform_score(worker: WorkerState, image_platform: str) -> tuple[bool, float, str]:
    requested = (image_platform or "").strip()
    if not requested or requested == "multi":
        return True, 1.0, "multi-platform image"
    if requested in worker.runtime_platforms:
        return True, 1.0, "native platform"
    emulated = requested + "-emulated"
    if emulated in worker.runtime_platforms:
        return True, 0.55, "emulated platform"
    return False, 0.0, f"image platform {requested} unsupported"


def hard_rejections(worker: WorkerState, req: InstanceRequest) -> list[str]:
    reasons = []
    if worker.status != "ready":
        reasons.append("worker not ready")
    if worker.draining:
        reasons.append("worker draining")
    ok, _, why = platform_score(worker, req.image_platform)
    if not ok:
        reasons.append(why)
    for feature in req.required_features:
        if not worker.features.get(feature):
            reasons.append(f"missing feature {feature}")
    if worker.cpu_runtime_allocated - worker.used_cpu < req.cpu_request:
        reasons.append("insufficient cpu")
    if worker.ram_runtime_allocated_mb - worker.used_ram_mb < req.memory_mb:
        reasons.append("insufficient ram")
    if worker.disk_free_mb - worker.used_disk_mb < req.disk_mb:
        reasons.append("insufficient disk")
    if worker.container_count >= worker.container_limit:
        reasons.append("container limit reached")
    if not worker.available_ports():
        reasons.append("no free target ports")
    return reasons


def score_worker(worker: WorkerState, req: InstanceRequest) -> float:
    _, arch_weight, _ = platform_score(worker, req.image_platform)
    free_cpu_ratio = max(0.0, (worker.cpu_runtime_allocated - worker.used_cpu) / max(worker.cpu_runtime_allocated, 1.0))
    free_ram_ratio = max(0.0, (worker.ram_runtime_allocated_mb - worker.used_ram_mb) / max(worker.ram_runtime_allocated_mb, 1))
    free_disk_ratio = max(0.0, (worker.disk_free_mb - worker.used_disk_mb) / max(worker.disk_free_mb, 1))
    container_ratio = 1.0 - min(1.0, worker.container_count / max(worker.container_limit, 1))
    failure_penalty = min(0.4, worker.recent_failures * 0.05)
    return (
        (free_cpu_ratio * 0.30)
        + (free_ram_ratio * 0.35)
        + (free_disk_ratio * 0.10)
        + (container_ratio * 0.15)
        + (arch_weight * 0.10)
        - failure_penalty
    )


def select_worker(workers: list[WorkerState], req: InstanceRequest) -> Placement:
    candidates: list[tuple[float, WorkerState]] = []
    rejected: list[str] = []
    for worker in workers:
        reasons = hard_rejections(worker, req)
        if reasons:
            rejected.append(f"{worker.worker_id}: {', '.join(reasons)}")
            continue
        candidates.append((score_worker(worker, req), worker))
    if not candidates:
        detail = "; ".join(rejected) if rejected else "no workers registered"
        raise RuntimeError(f"no eligible worker: {detail}")
    candidates.sort(key=lambda item: item[0], reverse=True)
    score, chosen = candidates[0]
    port = chosen.available_ports()[0]
    _, _, platform_reason = platform_score(chosen, req.image_platform)
    return Placement(
        worker_id=chosen.worker_id,
        public_ip=chosen.public_ip,
        public_port=port,
        score=round(score, 4),
        reasons=[platform_reason],
    )

