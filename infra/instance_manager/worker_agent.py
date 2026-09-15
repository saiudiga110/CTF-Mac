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
            status, body = post_json(f"{manager}/workers/heartbeat", payload, secret)
            print(json.dumps({"status": status, "response": body, "worker_id": payload["worker_id"]}))
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
