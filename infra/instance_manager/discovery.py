"""Cross-platform worker capability discovery.

This module intentionally avoids assuming the host OS. It discovers the host
and the container runtime separately because Docker Desktop and WSL2 workers
often have a different runtime architecture/resource limit than the physical
machine.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import socket
import subprocess
from dataclasses import dataclass, field
from typing import Any


def _run(args: list[str], timeout: int = 5) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except Exception as exc:
        return 127, "", str(exc)


def normalize_arch(value: str) -> str:
    value = (value or "").lower()
    if value in ("x86_64", "amd64"):
        return "amd64"
    if value in ("aarch64", "arm64"):
        return "arm64"
    return value or "unknown"


def os_family() -> str:
    name = platform.system().lower()
    if name == "darwin":
        return "macos"
    if name == "windows":
        return "windows"
    if name == "linux":
        return "linux"
    return name or "unknown"


def lan_ip() -> str:
    override = os.environ.get("WORKER_PUBLIC_IP") or os.environ.get("HOST_IP")
    if override:
        return override.strip()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("1.1.1.1", 80))
        return sock.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        sock.close()


def memory_total_mb() -> int:
    try:
        import psutil  # type: ignore

        return int(psutil.virtual_memory().total / (1024 * 1024))
    except Exception:
        pass
    if os_family() == "linux":
        try:
            with open("/proc/meminfo", encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("MemTotal:"):
                        return int(int(line.split()[1]) / 1024)
        except Exception:
            pass
    return 0


def disk_free_mb(path: str | None = None) -> int:
    target = path or os.environ.get("DOCKER_DATA_PATH") or os.getcwd()
    try:
        usage = shutil.disk_usage(target)
        return int(usage.free / (1024 * 1024))
    except Exception:
        return 0


def docker_info() -> dict[str, Any]:
    if shutil.which("docker"):
        code, out, err = _run(["docker", "info", "--format", "{{json .}}"], timeout=10)
        if code == 0 and out:
            try:
                data = json.loads(out)
                data["available"] = True
                return data
            except json.JSONDecodeError:
                return {"available": False, "error": "docker info returned non-json output"}
        return {"available": False, "error": err or out or "docker info failed"}

    try:
        import docker  # type: ignore

        client = docker.from_env()
        data = client.info()
        data["available"] = True
        return data
    except Exception as exc:
        return {"available": False, "error": f"docker unavailable: {exc}"}


def docker_platforms(info: dict[str, Any], host_arch: str) -> list[str]:
    if not info.get("available"):
        return []
    runtime_os = (info.get("OSType") or "linux").lower()
    runtime_arch = normalize_arch(info.get("Architecture") or host_arch)
    platforms = [f"{runtime_os}/{runtime_arch}"]

    # Docker Desktop may support emulation, but the CLI does not expose one
    # universal boolean for it. Let operators override/confirm this explicitly.
    extra = os.environ.get("WORKER_EXTRA_PLATFORMS", "")
    for raw in extra.split(","):
        value = raw.strip()
        if value and value not in platforms:
            platforms.append(value)

    if host_arch == "arm64" and os.environ.get("WORKER_ASSUME_AMD64_EMULATION", "0") == "1":
        value = f"{runtime_os}/amd64-emulated"
        if value not in platforms:
            platforms.append(value)
    return platforms


@dataclass
class WorkerCapabilities:
    worker_id: str
    os_family: str
    os_version: str
    host_arch: str
    public_ip: str
    runtime: str
    runtime_os: str
    runtime_arch: str
    runtime_platforms: list[str]
    cpu_total: int
    cpu_runtime_allocated: float
    ram_total_mb: int
    ram_runtime_allocated_mb: int
    disk_free_mb: int
    port_range_start: int
    port_range_end: int
    container_limit: int
    labels: dict[str, str] = field(default_factory=dict)
    features: dict[str, bool] = field(default_factory=dict)
    docker_available: bool = False
    docker_error: str = ""
    agent_url: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "worker_id": self.worker_id,
            "os_family": self.os_family,
            "os_version": self.os_version,
            "host_arch": self.host_arch,
            "public_ip": self.public_ip,
            "runtime": self.runtime,
            "runtime_os": self.runtime_os,
            "runtime_arch": self.runtime_arch,
            "runtime_platforms": self.runtime_platforms,
            "cpu_total": self.cpu_total,
            "cpu_runtime_allocated": self.cpu_runtime_allocated,
            "ram_total_mb": self.ram_total_mb,
            "ram_runtime_allocated_mb": self.ram_runtime_allocated_mb,
            "disk_free_mb": self.disk_free_mb,
            "port_range_start": self.port_range_start,
            "port_range_end": self.port_range_end,
            "container_limit": self.container_limit,
            "labels": self.labels,
            "features": self.features,
            "docker_available": self.docker_available,
            "docker_error": self.docker_error,
            "agent_url": self.agent_url,
        }


def detect_worker_capabilities() -> WorkerCapabilities:
    family = os_family()
    host_arch = normalize_arch(platform.machine())
    info = docker_info()
    runtime = "docker-desktop" if family in ("macos", "windows") else "docker-engine"
    if os.environ.get("WORKER_RUNTIME"):
        runtime = os.environ["WORKER_RUNTIME"]

    runtime_os = (info.get("OSType") or "linux").lower() if info.get("available") else "linux"
    runtime_arch = normalize_arch(info.get("Architecture") or host_arch)
    cpu_total = os.cpu_count() or 1
    ram_total = memory_total_mb()

    # Docker Desktop/WSL2 may cap resources below physical host totals. Operators
    # can set exact values; otherwise discovery uses conservative host-level data.
    cpu_runtime = float(os.environ.get("WORKER_RUNTIME_CPUS") or info.get("NCPU") or cpu_total)
    runtime_ram = info.get("MemTotal") if info.get("available") else None
    if runtime_ram and int(runtime_ram) > 1024 * 1024:
        runtime_ram = int(int(runtime_ram) / (1024 * 1024))
    ram_runtime = int(os.environ.get("WORKER_RUNTIME_RAM_MB") or runtime_ram or ram_total)

    start = int(os.environ.get("WORKER_TARGET_PORT_START", "20000"))
    end = int(os.environ.get("WORKER_TARGET_PORT_END", "20999"))
    labels: dict[str, str] = {}
    for raw in os.environ.get("WORKER_LABELS", "").split(","):
        if "=" in raw:
            key, value = raw.split("=", 1)
            labels[key.strip()] = value.strip()

    worker_id = os.environ.get("WORKER_ID")
    if not worker_id:
        worker_id = f"{socket.gethostname()}-{family}-{host_arch}".lower()
    public_ip = os.environ.get("WORKER_PUBLIC_IP") or lan_ip()
    agent_port = int(os.environ.get("WORKER_AGENT_PORT", "8090"))
    agent_url = os.environ.get("WORKER_AGENT_URL") or f"http://{public_ip}:{agent_port}"

    features = {
        "linux_containers": runtime_os == "linux",
        "pids_limit": True,
        "read_only_rootfs": True,
        "seccomp": runtime_os == "linux",
        "apparmor": family == "linux",
        "host_firewall_required": family in ("macos", "windows"),
    }

    return WorkerCapabilities(
        worker_id=worker_id,
        os_family=family,
        os_version=platform.version(),
        host_arch=host_arch,
        public_ip=public_ip,
        runtime=runtime,
        runtime_os=runtime_os,
        runtime_arch=runtime_arch,
        runtime_platforms=docker_platforms(info, host_arch),
        cpu_total=cpu_total,
        cpu_runtime_allocated=cpu_runtime,
        ram_total_mb=ram_total,
        ram_runtime_allocated_mb=ram_runtime,
        disk_free_mb=disk_free_mb(),
        port_range_start=start,
        port_range_end=end,
        container_limit=int(os.environ.get("WORKER_CONTAINER_LIMIT", "80")),
        labels=labels,
        features=features,
        docker_available=bool(info.get("available")),
        docker_error=str(info.get("error") or ""),
        agent_url=agent_url,
    )


def main() -> int:
    print(json.dumps(detect_worker_capabilities().to_dict(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
