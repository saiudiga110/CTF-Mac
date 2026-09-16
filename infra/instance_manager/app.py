"""Production-oriented Instance Manager API.

Run:
  python -m infra.instance_manager.app
"""

from __future__ import annotations

import os
import secrets
import threading
import time
import json
import urllib.error
import urllib.request
from typing import Any

from flask import Flask, jsonify, request

from .scheduler import InstanceRequest, WorkerState, select_worker
from .security import sign_body
from .security import verify_signature
from .store import Store


def create_app() -> Flask:
    app = Flask(__name__)
    store = Store(os.environ.get("INSTANCE_MANAGER_DB", "/data/instance-manager.sqlite3"))
    service_secret = os.environ.get("INSTANCE_MANAGER_SECRET", "change-me")
    worker_stale_after_seconds = int(os.environ.get("INSTANCE_MANAGER_WORKER_STALE_AFTER_SECONDS", "90"))
    allocation_lock = threading.Lock()

    def signed_worker_post(url: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        ts, sig = sign_body(service_secret, body)
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
        with urllib.request.urlopen(req, timeout=int(os.environ.get("INSTANCE_MANAGER_WORKER_CREATE_TIMEOUT", "90"))) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))

    def signed_worker_delete(url: str) -> tuple[int, dict[str, Any]]:
        body = b""
        ts, sig = sign_body(service_secret, body)
        req = urllib.request.Request(
            url,
            data=body,
            method="DELETE",
            headers={
                "X-CTF-Timestamp": ts,
                "X-CTF-Signature": sig,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return 404, {"success": True, "not_found": True}
            raise

    def worker_get_json(url: str, timeout: int | None = None) -> dict[str, Any]:
        if timeout is None:
            timeout = int(os.environ.get("INSTANCE_MANAGER_WORKER_READ_TIMEOUT", "30"))
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def require_signature() -> tuple[bool, Any]:
        if os.environ.get("INSTANCE_MANAGER_DISABLE_AUTH") == "1":
            return True, None
        ts = request.headers.get("X-CTF-Timestamp", "")
        sig = request.headers.get("X-CTF-Signature", "")
        ok = verify_signature(service_secret, request.get_data() or b"", ts, sig)
        if not ok:
            return False, (jsonify({"success": False, "error": "invalid signature"}), 401)
        return True, None

    def _safe_network_part(value: Any, default: str = "0") -> str:
        raw = str(value if value not in (None, "") else default)
        return "".join(ch if ch.isalnum() else "-" for ch in raw.lower()) or default

    def _shared_network_name(payload: dict[str, Any]) -> str:
        user_id = _safe_network_part(payload.get("ctfd_user_id"))
        challenge_id = _safe_network_part(payload.get("challenge_id"))
        return f"ctfd-net-u{user_id}-c{challenge_id}"

    def _is_terminal(inst: dict[str, Any]) -> bool:
        return inst.get("status") in ("stopped", "deleted", "failed", "expired")

    def _delete_worker_instance(inst: dict[str, Any]) -> None:
        if os.environ.get("INSTANCE_MANAGER_DEPLOY_MODE", "allocate") != "worker":
            return
        if not inst.get("container_name"):
            return
        worker_payload = next((row for row in store.list_workers() if row["worker_id"] == inst.get("worker_id")), None)
        if not worker_payload or not worker_payload.get("agent_url"):
            return
        url = worker_payload["agent_url"].rstrip("/") + f"/instances/{inst['container_name']}?network_name={inst.get('network_name', '')}"
        signed_worker_delete(url)

    def _stop_instance_record(instance_id: str, final_status: str = "stopped", final_health: str | None = None, force: bool = False) -> dict[str, Any]:
        inst = store.get_instance(instance_id)
        if not inst:
            return {"success": False, "error": "instance not found", "status_code": 404}
        if _is_terminal(inst):
            return {"success": True, "status": inst.get("status"), "id": instance_id, "already_terminal": True}
        try:
            _delete_worker_instance(inst)
        except Exception as exc:
            store.event("instance_stop_failed", str(exc), instance_id=instance_id, worker_id=inst.get("worker_id"))
            if not force:
                return {"success": False, "error": "worker stop failed", "detail": str(exc), "status_code": 502}
        inst["status"] = final_status
        inst["health"] = final_health or final_status
        store.update_instance(instance_id, inst, final_status)
        store.event("instance_" + final_status, "instance " + final_status, instance_id=instance_id, worker_id=inst.get("worker_id"))
        return {"success": True, "status": final_status, "id": instance_id}

    def _expire_due_instances() -> int:
        now = time.time()
        expired = 0
        for inst in store.list_instances():
            if _is_terminal(inst):
                continue
            expires_at = float(inst.get("expires_at") or 0)
            if not expires_at or expires_at > now:
                continue
            force = (now - expires_at) > 300
            result = _stop_instance_record(str(inst["id"]), "expired", "expired", force=force)
            if result.get("success"):
                expired += 1
        return expired

    def _expiry_reaper_loop() -> None:
        interval = max(5, int(os.environ.get("INSTANCE_MANAGER_REAPER_INTERVAL_SECONDS", "30") or "30"))
        while True:
            try:
                count = _expire_due_instances()
                if count:
                    store.event("expiry_reaper", f"expired {count} instance(s)")
            except Exception as exc:
                store.event("expiry_reaper_error", str(exc))
            time.sleep(interval)

    if os.environ.get("INSTANCE_MANAGER_DISABLE_REAPER", "0").lower() not in ("1", "true", "yes", "on"):
        threading.Thread(target=_expiry_reaper_loop, daemon=True, name="instance-expiry-reaper").start()

    def current_worker_states() -> list[WorkerState]:
        now = time.time()
        workers = {}
        for row in store.list_workers():
            heartbeat_age = now - float(row.get("last_heartbeat") or 0)
            if heartbeat_age > worker_stale_after_seconds:
                continue
            workers[row["worker_id"]] = WorkerState.from_payload(row)
        for inst in store.list_instances():
            if _is_terminal(inst):
                continue
            expires_at = float(inst.get("expires_at") or 0)
            if expires_at and expires_at <= now:
                continue
            worker = workers.get(inst.get("worker_id"))
            if not worker:
                continue
            profile = inst.get("resource_profile") or {}
            worker.used_cpu += float(profile.get("cpu_request") or 0)
            worker.used_ram_mb += int(profile.get("memory_mb") or 0)
            worker.used_disk_mb += int(profile.get("disk_mb") or 0)
            worker.container_count += 1
            if inst.get("public_port"):
                worker.used_ports.add(int(inst["public_port"]))
        return list(workers.values())

    @app.get("/health")
    def health():
        return jsonify({"success": True, "service": "instance-manager", "time": int(time.time())})

    @app.post("/workers/heartbeat")
    def worker_heartbeat():
        ok, error = require_signature()
        if not ok:
            return error
        payload = request.get_json(force=True, silent=False)
        if not payload.get("worker_id"):
            return jsonify({"success": False, "error": "worker_id required"}), 400
        store.upsert_worker(payload)
        store.event("worker_heartbeat", "worker heartbeat accepted", worker_id=payload["worker_id"])
        return jsonify({"success": True, "worker_id": payload["worker_id"]})

    @app.get("/nodes")
    def nodes():
        ok, error = require_signature()
        if not ok:
            return error
        nodes = []
        for worker in current_worker_states():
            row = dict(worker.__dict__)
            row["used_ports"] = sorted(row.get("used_ports") or [])
            nodes.append(row)
        return jsonify({"success": True, "nodes": nodes})

    @app.post("/instances")
    def create_instance():
        with allocation_lock:
            return _create_instance_locked()

    def _create_instance_locked():
        ok, error = require_signature()
        if not ok:
            return error
        payload = request.get_json(force=True, silent=False)
        req = InstanceRequest(
            image=payload["image"],
            image_platform=payload.get("image_platform", "multi"),
            cpu_request=float(payload.get("cpu_request", 0.5)),
            memory_mb=int(payload.get("memory_mb", 256)),
            disk_mb=int(payload.get("disk_mb", 256)),
            internal_port=int(payload.get("internal_port", 80)),
            required_features=list(payload.get("required_features") or ["linux_containers"]),
        )
        workers = current_worker_states()
        network_name = str(payload.get("network_name") or _shared_network_name(payload))
        colocated_worker_id = None
        now = time.time()
        for inst in store.list_instances():
            if inst.get("network_name") != network_name:
                continue
            if _is_terminal(inst):
                continue
            expires_at = float(inst.get("expires_at") or 0)
            if expires_at and expires_at <= now:
                continue
            colocated_worker_id = inst.get("worker_id")
            break
        if colocated_worker_id:
            workers = [worker for worker in workers if worker.worker_id == colocated_worker_id]
        try:
            placement = select_worker(workers, req)
        except RuntimeError as exc:
            detail = str(exc)
            if colocated_worker_id:
                detail = f"no eligible worker for shared network {network_name} on {colocated_worker_id}: {detail}"
            return jsonify({"success": False, "error": detail}), 409

        instance_id = payload.get("idempotency_key") or "inst_" + secrets.token_hex(12)
        existing = store.get_instance(instance_id)
        if existing and not _is_terminal(existing):
            existing_expires_at = float(existing.get("expires_at") or 0)
            if existing_expires_at and existing_expires_at <= time.time():
                _stop_instance_record(str(existing["id"]), "expired", "expired")
                existing = store.get_instance(instance_id)
            else:
                return jsonify({"success": True, "instance": existing, "expires_at": existing.get("expires_at"), "idempotent": True}), 200
        ttl = int(payload.get("ttl_seconds", 3600))
        expires_at = time.time() + ttl
        instance_payload = {
            "id": instance_id,
            "ctfd_user_id": payload.get("ctfd_user_id"),
            "ctfd_team_id": payload.get("ctfd_team_id"),
            "challenge_id": payload.get("challenge_id"),
            "image": req.image,
            "image_platform": req.image_platform,
            "worker_id": placement.worker_id,
            "public_host": placement.public_ip,
            "public_port": placement.public_port,
            "url": f"http://{placement.public_ip}:{placement.public_port}/",
            "status": "allocated",
            "health": "pending",
            "role": payload.get("role") or "challenge",
            "created_at": int(time.time()),
            "resource_profile": {
                "cpu_request": req.cpu_request,
                "memory_mb": req.memory_mb,
                "disk_mb": req.disk_mb,
                "internal_port": req.internal_port,
            },
        }
        deploy_mode = os.environ.get("INSTANCE_MANAGER_DEPLOY_MODE", "allocate")
        if deploy_mode == "worker":
            worker_payload = next((row for row in store.list_workers() if row["worker_id"] == placement.worker_id), None)
            if not worker_payload or not worker_payload.get("agent_url"):
                return jsonify({"success": False, "error": "selected worker has no agent_url"}), 409
            role = str(payload.get("role") or "challenge").lower().replace("_", "-")
            container_name = f"ctfd-{role}-u{payload.get('ctfd_user_id', '0')}-c{payload.get('challenge_id', '0')}-{instance_id[-8:]}"
            labels = {
                "ctfd_target_user_id": str(payload.get("ctfd_user_id") or ""),
                "ctfd_target_user": str(payload.get("username") or ""),
                "ctfd_target_role": role,
                "ctfd_target_challenge": str(payload.get("challenge_id") or ""),
                "ctfd_target_expires": str(int(expires_at)),
            }
            labels.update({str(k): str(v) for k, v in dict(payload.get("labels") or {}).items()})
            worker_req = {
                "instance_id": instance_id,
                "image": req.image,
                "name": container_name,
                "network_name": network_name,
                "internal_port": req.internal_port,
                "public_port": placement.public_port,
                "memory_limit": f"{req.memory_mb}m",
                "cpu_quota": int(payload.get("cpu_quota") or int(req.cpu_request * 100000)),
                "pids_limit": int(payload.get("pids_limit", 256)),
                "read_only": bool(payload.get("read_only", False)),
                "shm_size": payload.get("shm_size"),
                "security_opt": payload.get("security_opt") if "security_opt" in payload else None,
                "cap_drop": payload.get("cap_drop") if "cap_drop" in payload else None,
                "restart_policy": payload.get("restart_policy", "no"),
                "environment": payload.get("environment") or {},
                "labels": labels,
                "network_aliases": payload.get("network_aliases") or [],
            }
            worker_instance_url = worker_payload["agent_url"].rstrip("/") + "/instances"
            worker_delete_url = worker_payload["agent_url"].rstrip("/") + f"/instances/{container_name}?network_name={network_name}"
            try:
                _, worker_resp = signed_worker_post(worker_instance_url, worker_req)
            except Exception as exc:
                try:
                    signed_worker_delete(worker_delete_url)
                except Exception:
                    pass
                store.event("deploy_failed", str(exc), instance_id=instance_id, worker_id=placement.worker_id)
                return jsonify({"success": False, "error": "worker deploy failed", "detail": str(exc)}), 502
            if not worker_resp.get("success"):
                try:
                    signed_worker_delete(worker_delete_url)
                except Exception:
                    pass
                store.event("deploy_failed", worker_resp.get("error", "worker deploy failed"), instance_id=instance_id, worker_id=placement.worker_id)
                return jsonify({"success": False, "error": "worker deploy failed", "detail": worker_resp}), 502
            instance_payload["container_name"] = container_name
            instance_payload["network_name"] = network_name
            instance_payload["network_aliases"] = payload.get("network_aliases") or []
            instance_payload["container_id"] = (worker_resp.get("result") or {}).get("container_id")
            instance_payload["status"] = "deployed"
        if existing:
            store.update_instance(instance_id, instance_payload, instance_payload["status"], expires_at)
        else:
            store.create_instance(instance_id, instance_payload, expires_at)
        store.event("instance_allocated", "instance allocated to worker", instance_id=instance_id, worker_id=placement.worker_id, details=instance_payload)
        return jsonify({"success": True, "instance": instance_payload, "placement": placement.__dict__, "expires_at": expires_at}), 201

    @app.get("/architecture")
    def architecture():
        ok, error = require_signature()
        if not ok:
            return error
        now = time.time()
        active_states = {worker.worker_id: worker for worker in current_worker_states()}
        instances = [
            inst for inst in store.list_instances()
            if not _is_terminal(inst)
            and not (float(inst.get("expires_at") or 0) and float(inst.get("expires_at") or 0) <= now)
        ]
        grouped_instances: dict[str, list[dict[str, Any]]] = {}
        for inst in instances:
            grouped_instances.setdefault(str(inst.get("worker_id") or "unknown"), []).append(inst)

        nodes = []
        for row in store.list_workers():
            worker_id = row.get("worker_id")
            heartbeat_age = now - float(row.get("last_heartbeat") or 0)
            payload = dict(row)
            payload["heartbeat_age_seconds"] = round(heartbeat_age, 1)
            payload["fresh"] = worker_id in active_states
            if worker_id in active_states:
                state = active_states[worker_id]
                payload.update(dict(state.__dict__))
                payload["used_ports"] = sorted(payload.get("used_ports") or [])
            payload["instances"] = sorted(grouped_instances.get(worker_id, []), key=lambda item: (str(item.get("role") or ""), str(item.get("container_name") or item.get("id") or "")))
            inventory = {"success": False, "containers": [], "images": [], "error": "agent_url missing"}
            agent_url = row.get("agent_url")
            if agent_url and payload.get("fresh"):
                try:
                    inventory = worker_get_json(agent_url.rstrip("/") + "/inventory")
                except Exception as exc:
                    inventory = {"success": False, "containers": [], "images": [], "error": str(exc)}
            payload["inventory"] = inventory
            nodes.append(payload)

        nodes.sort(key=lambda item: str(item.get("labels", {}).get("site") or item.get("worker_id") or ""))
        totals = {
            "nodes": len([n for n in nodes if n.get("fresh")]),
            "containers": sum(len(n.get("instances") or []) for n in nodes if n.get("fresh")),
            "target_containers": sum(1 for inst in instances if str(inst.get("role") or "").lower() in ("target", "challenge")),
            "kali_containers": sum(1 for inst in instances if str(inst.get("role") or "").lower() == "kali"),
            "capacity": sum(int(n.get("container_limit") or 0) for n in nodes if n.get("fresh")),
        }
        return jsonify({"success": True, "generated_at": int(now), "nodes": nodes, "instances": instances, "totals": totals})

    @app.get("/instances")
    def list_instances():
        ok, error = require_signature()
        if not ok:
            return error
        return jsonify({"success": True, "instances": store.list_instances()})

    @app.get("/instances/<instance_id>")
    @app.get("/instances/<instance_id>/status")
    def instance_status(instance_id: str):
        ok, error = require_signature()
        if not ok:
            return error
        inst = store.get_instance(instance_id)
        if not inst:
            return jsonify({"success": False, "error": "instance not found"}), 404
        if os.environ.get("INSTANCE_MANAGER_DEPLOY_MODE", "allocate") == "worker" and inst.get("container_name"):
            worker_payload = next((row for row in store.list_workers() if row["worker_id"] == inst.get("worker_id")), None)
            if worker_payload and worker_payload.get("agent_url"):
                try:
                    url = worker_payload["agent_url"].rstrip("/") + f"/instances/{inst['container_name']}/status"
                    req = urllib.request.Request(url, method="GET")
                    with urllib.request.urlopen(req, timeout=8) as resp:
                        worker_status = json.loads(resp.read().decode("utf-8"))
                    if worker_status.get("success"):
                        result = worker_status.get("result") or {}
                        inst["container_status"] = result.get("status")
                        inst["health"] = result.get("health") or inst.get("health")
                except Exception as exc:
                    inst["health"] = "worker_status_error"
                    inst["status_error"] = str(exc)
        return jsonify({"success": True, "instance": inst})

    @app.post("/instances/<instance_id>/restart")
    def restart_instance(instance_id: str):
        ok, error = require_signature()
        if not ok:
            return error
        store.event("instance_restart_requested", "restart requested", instance_id=instance_id)
        return jsonify({"success": True, "status": "restart_requested", "id": instance_id})

    @app.post("/instances/<instance_id>/stop")
    @app.delete("/instances/<instance_id>")
    def stop_instance(instance_id: str):
        ok, error = require_signature()
        if not ok:
            return error
        force = request.args.get("force", "").lower() in ("1", "true", "yes")
        result = _stop_instance_record(instance_id, "stopped", "stopped", force=force)
        status_code = int(result.pop("status_code", 200))
        return jsonify(result), status_code

    @app.post("/instances/stop-all")
    def stop_all_instances():
        ok, error = require_signature()
        if not ok:
            return error
        force = request.args.get("force", "").lower() in ("1", "true", "yes")
        stopped = 0
        for inst in store.list_instances():
            if _is_terminal(inst):
                continue
            res = _stop_instance_record(str(inst["id"]), "stopped", "stopped", force=force)
            if res.get("success"):
                stopped += 1
        store.event("instances_stopped_all", f"stopped {stopped} instance(s)")
        return jsonify({"success": True, "stopped": stopped})

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host=os.environ.get("INSTANCE_MANAGER_HOST", "0.0.0.0"), port=int(os.environ.get("INSTANCE_MANAGER_PORT", "8088")))
