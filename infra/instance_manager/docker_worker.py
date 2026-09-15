"""Local Docker execution for worker agents."""

from __future__ import annotations

import os
from typing import Any


class DockerWorker:
    def __init__(self) -> None:
        import docker  # type: ignore

        self.client = docker.from_env()
        self.docker = docker

    def create_instance(self, payload: dict[str, Any]) -> dict[str, Any]:
        image = payload["image"]
        name = payload["name"]
        network_name = payload["network_name"]
        internal_port = int(payload.get("internal_port", 80))
        public_port = int(payload["public_port"])
        env = dict(payload.get("environment") or {})
        labels = dict(payload.get("labels") or {})
        labels.setdefault("ctfd_target_managed", "true")
        labels.setdefault("ctfd_target_instance", payload.get("instance_id", name))

        network = self._ensure_network(network_name, labels)
        security_opt = payload.get("security_opt")
        if security_opt is None:
            security_opt = ["no-new-privileges:true"]
        cap_drop = payload.get("cap_drop")
        if cap_drop is None:
            cap_drop = ["ALL"]
        run_kwargs = {
            "detach": True,
            "network": network.name,
            "ports": {f"{internal_port}/tcp": ("0.0.0.0", public_port)},
            "environment": env,
            "labels": labels,
            "mem_limit": payload.get("memory_limit", "256m"),
            "cpu_period": 100000,
            "cpu_quota": int(payload.get("cpu_quota", 50000)),
            "pids_limit": int(payload.get("pids_limit", 256)),
            "read_only": bool(payload.get("read_only", False)),
            "security_opt": security_opt,
            "cap_drop": cap_drop,
            "restart_policy": {"Name": payload.get("restart_policy", "no")},
            "log_config": self.docker.types.LogConfig(
                type="json-file",
                config={
                    "max-size": payload.get("log_max_size", "10m"),
                    "max-file": payload.get("log_max_file", "3"),
                },
            ),
        }
        if payload.get("shm_size"):
            run_kwargs["shm_size"] = payload.get("shm_size")
        container = self.client.containers.run(image, name=name, **run_kwargs)
        return {
            "container_id": container.id,
            "container_name": container.name,
            "network_name": network.name,
            "public_port": public_port,
            "status": "created",
        }

    def remove_instance(self, container_name: str, network_name: str | None = None) -> dict[str, Any]:
        removed = False
        try:
            container = self.client.containers.get(container_name)
            container.remove(force=True)
            removed = True
        except Exception:
            pass
        if network_name:
            self._remove_network_if_empty(network_name)
        return {"removed": removed}

    def status(self, container_name: str) -> dict[str, Any]:
        container = self.client.containers.get(container_name)
        container.reload()
        return {
            "container_id": container.id,
            "container_name": container.name,
            "status": container.status,
            "health": ((container.attrs.get("State") or {}).get("Health") or {}).get("Status"),
        }

    def _ensure_network(self, name: str, labels: dict[str, str]):
        try:
            return self.client.networks.get(name)
        except Exception:
            return self.client.networks.create(
                name,
                driver=os.environ.get("WORKER_NETWORK_DRIVER", "bridge"),
                internal=bool(int(os.environ.get("WORKER_TARGET_NETWORK_INTERNAL", "0"))),
                labels=labels,
            )

    def _remove_network_if_empty(self, name: str) -> None:
        try:
            network = self.client.networks.get(name)
            network.reload()
            if not (network.attrs.get("Containers") or {}):
                network.remove()
        except Exception:
            pass
