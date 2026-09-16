"""Worker agent heartbeat and local Docker execution API.

Docker execution remains local to the worker, so CTFd and the manager never
need a worker Docker socket.
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.request

from flask import Flask, jsonify, request

from .discovery import detect_worker_capabilities
from .docker_worker import DockerWorker
from .security import sign_body, verify_signature


def post_json(url: str, payload: dict, secret: str) -> tuple[int, str]:
    body = json.dumps(payload, sort_keys=True).encode("utf-8")
    ts, sig = sign_body(secret, body)
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-CTF-Timestamp": ts,
            "X-CTF-Signature": sig,
        },
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.status, resp.read().decode("utf-8", errors="replace")


def create_app() -> Flask:
    app = Flask(__name__)
    service_secret = os.environ.get("INSTANCE_MANAGER_SECRET", "change-me")

    def require_signature():
        if os.environ.get("WORKER_DISABLE_AUTH") == "1":
            return True, None
        ts = request.headers.get("X-CTF-Timestamp", "")
        sig = request.headers.get("X-CTF-Signature", "")
        if verify_signature(service_secret, request.get_data() or b"", ts, sig):
            return True, None
        return False, (jsonify({"success": False, "error": "invalid signature"}), 401)

    @app.get("/health")
    def health():
        caps = detect_worker_capabilities().to_dict()
        return jsonify({"success": True, "worker_id": caps["worker_id"], "docker_available": caps["docker_available"]})

    @app.get("/capabilities")
    def capabilities():
        return jsonify({"success": True, "capabilities": detect_worker_capabilities().to_dict()})


    @app.get("/inventory")
    def inventory():
        # Inventory is read-only. Keep it unauthenticated on the isolated LAN by
        # default so the manager can poll workers with a simple GET. Set
        # WORKER_AUTH_INVENTORY=1 if this endpoint is exposed beyond the lab LAN.
        if os.environ.get("WORKER_AUTH_INVENTORY") == "1":
            ok, error = require_signature()
            if not ok:
                return error
        try:
            import docker  # type: ignore

            client = docker.from_env()
            containers = []
            for container in client.containers.list(all=True):
                labels = container.labels or {}
                managed = labels.get("ctfd_target_managed") == "true"
                compose_service = labels.get("com.docker.compose.service", "")
                if not managed and compose_service not in ("worker-agent", "vbank-ctf", "vbank-analytics", "kali"):
                    continue
                ports = []
                for container_port, bindings in ((container.attrs.get("NetworkSettings") or {}).get("Ports") or {}).items():
                    for binding in bindings or []:
                        ports.append({
                            "container": container_port,
                            "host_ip": binding.get("HostIp"),
                            "host_port": binding.get("HostPort"),
                        })
                containers.append({
                    "name": container.name,
                    "image": (container.attrs.get("Config") or {}).get("Image") or "",
                    "status": container.status,
                    "id": container.id[:12],
                    "role": labels.get("ctfd_target_role") or compose_service or "system",
                    "username": labels.get("ctfd_target_user") or "",
                    "challenge_id": labels.get("ctfd_target_challenge") or "",
                    "ports": ports,
                })
            images = []
            wanted_prefixes = ("vbank-", "kali-ctf", "ctf-main-local-worker-agent")
            for image in client.images.list():
                tags = image.tags or [image.short_id.replace("sha256:", "sha256:")]
                if not any(any(tag.startswith(prefix) for prefix in wanted_prefixes) for tag in tags):
                    continue
                images.append({
                    "tags": tags,
                    "id": image.short_id.replace("sha256:", ""),
                    "size_bytes": int((image.attrs or {}).get("Size") or 0),
                })
            containers.sort(key=lambda item: (item["role"], item["name"]))
            images.sort(key=lambda item: item["tags"][0] if item.get("tags") else item.get("id", ""))
            return jsonify({"success": True, "containers": containers, "images": images})
        except Exception as exc:
            return jsonify({"success": False, "error": str(exc), "containers": [], "images": []}), 500

    @app.post("/instances")
    def create_instance():
        ok, error = require_signature()
        if not ok:
            return error
        payload = request.get_json(force=True, silent=False)
        try:
            result = DockerWorker().create_instance(payload)
            return jsonify({"success": True, "result": result}), 201
        except Exception as exc:
            return jsonify({"success": False, "error": str(exc)}), 500

    @app.get("/instances/<container_name>/status")
    def instance_status(container_name: str):
        # Status is intentionally unauthenticated by default so the manager can
        # poll workers with a simple GET on an isolated management network.
        # Set WORKER_AUTH_STATUS=1 if you expose this beyond that network.
        if os.environ.get("WORKER_AUTH_STATUS") == "1":
            ok, error = require_signature()
            if not ok:
                return error
        try:
            return jsonify({"success": True, "result": DockerWorker().status(container_name)})
        except Exception as exc:
            return jsonify({"success": False, "error": str(exc)}), 404

    @app.delete("/instances/<container_name>")
    def delete_instance(container_name: str):
        ok, error = require_signature()
        if not ok:
            return error
        network_name = request.args.get("network_name")
        try:
            return jsonify({"success": True, "result": DockerWorker().remove_instance(container_name, network_name)})
        except Exception as exc:
            return jsonify({"success": False, "error": str(exc)}), 500

    return app


def main() -> int:
    manager = os.environ.get("INSTANCE_MANAGER_URL", "http://127.0.0.1:8088").rstrip("/")
    secret = os.environ.get("INSTANCE_MANAGER_SECRET", "change-me")
    interval = int(os.environ.get("WORKER_HEARTBEAT_INTERVAL", "15"))
    once = os.environ.get("WORKER_ONCE", "0") == "1"

    def heartbeat_loop() -> None:
        while True:
            payload = detect_worker_capabilities().to_dict()
            try:
                status, body = post_json(f"{manager}/workers/heartbeat", payload, secret)
                print(json.dumps({"status": status, "response": body, "worker_id": payload["worker_id"]}), flush=True)
                if once:
                    return
            except Exception as exc:
                print(json.dumps({"status": "heartbeat_error", "error": str(exc), "worker_id": payload.get("worker_id")}), flush=True)
                if once:
                    return
            time.sleep(interval)

    if os.environ.get("WORKER_SERVE", "0") == "1":
        threading.Thread(target=heartbeat_loop, daemon=True, name="worker-heartbeat").start()
        app = create_app()
        app.run(host=os.environ.get("WORKER_AGENT_HOST", "0.0.0.0"), port=int(os.environ.get("WORKER_AGENT_PORT", "8090")))
        return 0
    heartbeat_loop()
    return 0


app = create_app()


if __name__ == "__main__":
    raise SystemExit(main())
