"""
CTFd Target Plugin - per-user VBank target instances with optional Kali sidecars.
"""

import gzip
import hashlib
import json
import logging
import os
import re
import socket
import threading
import time

import jinja2
from flask import Blueprint, Response, request

logger = logging.getLogger(__name__)

from CTFd.plugins import register_admin_plugin_menu_bar
from CTFd.plugins.flags import BaseFlag
from CTFd.utils import get_config, set_config
from CTFd.utils import user as current_user
from CTFd.utils.decorators import admins_only, authed_only
from .models import ChallengeTargetConfig


_cleanup_started = False
_cleanup_lock = threading.Lock()
_expiry_overrides: dict = {}   # key: "username:role" â†’ float unix timestamp


class TargetFlag(BaseFlag):
    id = "target_flag"
    name = "vBank Target Flag (per-user)"
    templates = {
        "create": "/plugins/ctfd-target/assets/flag-target.html",
        "update": "/plugins/ctfd-target/assets/flag-target.html",
    }

    @staticmethod
    def compare(chal_key_obj, provided):
        try:
            user = current_user.get_current_user()
            if not user:
                return False
            flag_secret = get_config("ctfd_target:flag_secret") or "ctf-vbank-2024"
            raw = f"vbank:{flag_secret}:{user.name}"
            expected = "CTF{" + hashlib.md5(raw.encode()).hexdigest()[:16] + "}"
            return expected == provided.strip()
        except Exception:
            return False


def load(app):
    plugin_dir = os.path.dirname(os.path.abspath(__file__))
    plugin_template_dir = os.path.join(plugin_dir, "templates")

    page_blueprint = Blueprint(
        "ctfd-target",
        __name__,
        template_folder="templates",
        url_prefix="/plugins/ctfd-target",
    )

    def render_plugin_template(template_name, **kwargs):
        from flask import render_template as _flask_render
        # Temporarily prepend the plugin template dir so extends/includes resolve
        original_loader = app.jinja_loader
        try:
            app.jinja_loader = jinja2.ChoiceLoader([
                jinja2.FileSystemLoader(plugin_template_dir),
                original_loader,
            ])
            return _flask_render(template_name, **kwargs)
        finally:
            app.jinja_loader = original_loader

    def _json_response(payload, status=200):
        return Response(json.dumps(payload), status=status, mimetype="application/json")

    @page_blueprint.route("/assets/<path:filename>")
    def plugin_assets(filename):
        from flask import send_from_directory

        return send_from_directory(os.path.join(plugin_dir, "assets"), filename)

    DEFAULT_TARGET_IMAGE = "vbank-ctf"
    DEFAULT_ANALYTICS_IMAGE = "vbank-analytics"
    DEFAULT_KALI_IMAGE = "kali-ctf"
    DEFAULT_TARGET_PORT = "80"
    DEFAULT_KALI_PORT = "6901"
    DEFAULT_TARGET_PORT_RANGE_START = "20000"
    DEFAULT_TARGET_PORT_RANGE_END = "20999"
    DEFAULT_KALI_PORT_RANGE_START = "21000"
    DEFAULT_KALI_PORT_RANGE_END = "21999"
    DEFAULT_MEM_LIMIT = "128m"
    DEFAULT_CPU_QUOTA = "50000"
    DEFAULT_TARGET_MODE = "global"
    DEFAULT_ENABLE_KALI = "0"
    DEFAULT_INSTANCE_LIFETIME = "3600"
    DEFAULT_KALI_IMAGE_NAME = "kali-ctf"    # Kali Linux with noVNC (SSO auth)
    DEFAULT_KALI_SSO_SECRET = "ctfd-kali-sso-secret-key-change-me"
    DEFAULT_KALI_PASSWORD   = "kali123"
    DEFAULT_MAX_CHALLENGE_INSTANCES = "3"
    DEFAULT_MAX_TOTAL_INSTANCES = "80"

    try:
        from .challenge_catalog import challenges as catalog_challenges, canonical_names as catalog_names
        CANONICAL_CHALLENGE_NAMES = catalog_names()
        CATALOG_BY_NAME = {row["name"]: row for row in catalog_challenges()}
    except Exception as _cat_exc:
        logger.warning("[ChallengeCatalog] load failed: %s", _cat_exc)
        CANONICAL_CHALLENGE_NAMES = [
            "Trust, Not Verified",
            "Someone Else's Numbers",
            "The Account No One Touches",
            "Faster Than Careful",
            "Word For Word",
            "No Handle on Your Side",
            "Who's Really Typing",
        ]
        CATALOG_BY_NAME = {}

    TARGET_PREFIX = "ctfd-target-"
    ANALYTICS_PREFIX = "ctfd-analytics-"
    KALI_PREFIX = "ctfd-kali-"
    NETWORK_PREFIX = "ctfd-net-"

    def _redis_or_none():
        try:
            import redis as _r
            client = _r.from_url(
                os.environ.get("REDIS_URL", "redis://cache:6379"),
                socket_connect_timeout=1, socket_timeout=1
            )
            client.ping()
            return client
        except Exception:
            return None

    def _get_config(key, default):
        try:
            value = get_config("ctfd_target:" + key)
            if value not in (None, ""):
                return value
            env_key = "CTFD_TARGET_" + key.upper()
            env_value = os.environ.get(env_key, "")
            return env_value if env_value not in (None, "") else default
        except Exception:
            return default

    def _flag_owner_id(user):
        """Keep target flags per-user unless CTFd explicitly uses team scoring."""
        if get_config("user_mode") == "teams" and getattr(user, "team_id", None):
            return str(user.team_id)
        return str(user.id)

    _LAB_DOMAIN = _get_config("lab_domain", os.environ.get("LAB_DOMAIN", "lab"))

    def _bool_config(key, default=False):
        return str(_get_config(key, "1" if default else "0")).lower() in ("1", "true", "yes", "on")


    def _instance_manager_enabled():
        mode = str(_get_config("orchestrator", "") or "").strip().lower()
        url = str(_get_config("instance_manager_url", "") or "").strip()
        return mode in ("instance_manager", "manager", "remote") and bool(url)

    def _instance_manager_id(user_id, challenge_id, role="challenge"):
        return f"ctfd-{role}-u{int(user_id)}-c{int(challenge_id)}"

    def _parse_mem_mb(value, default_mb=256):
        raw = str(value or "").strip().lower()
        if not raw:
            return default_mb
        try:
            if raw.endswith("g"):
                return int(float(raw[:-1]) * 1024)
            if raw.endswith("gb"):
                return int(float(raw[:-2]) * 1024)
            if raw.endswith("m"):
                return int(float(raw[:-1]))
            if raw.endswith("mb"):
                return int(float(raw[:-2]))
            return int(float(raw))
        except Exception:
            return default_mb

    def _instance_manager_request(method, path, payload=None):
        import hmac as _hmac
        import urllib.error as _urlerr
        import urllib.request as _urlreq

        base = str(_get_config("instance_manager_url", "") or "").strip().rstrip("/")
        if not base:
            raise RuntimeError("Instance Manager URL is not configured.")
        secret = (
            str(_get_config("instance_manager_secret", "") or "").strip()
            or os.environ.get("INSTANCE_MANAGER_SECRET", "")
        )
        if not secret:
            raise RuntimeError("Instance Manager secret is not configured.")
        body = json.dumps(payload or {}, sort_keys=True).encode("utf-8") if payload is not None else b""
        ts = str(int(time.time()))
        sig = _hmac.new(secret.encode("utf-8"), ts.encode("utf-8") + b"." + body, hashlib.sha256).hexdigest()
        req = _urlreq.Request(
            base + path,
            data=body if method in ("POST", "PUT", "DELETE") else None,
            method=method,
            headers={
                "Content-Type": "application/json",
                "X-CTF-Timestamp": ts,
                "X-CTF-Signature": sig,
            },
        )
        try:
            with _urlreq.urlopen(req, timeout=int(_get_config("instance_manager_timeout", "20"))) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                return json.loads(raw) if raw else {}
        except _urlerr.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            try:
                data = json.loads(detail)
                raise RuntimeError(data.get("error") or data.get("msg") or detail)
            except json.JSONDecodeError:
                raise RuntimeError(detail or str(exc))

    def _manager_instance_payload(user, challenge_id, cfg):
        mem_mb = _parse_mem_mb((cfg.mem_limit or "").strip() or _get_config("mem_limit", DEFAULT_MEM_LIMIT))
        cpu_quota = int((cfg.cpu_quota or "").strip() or _get_config("cpu_quota", DEFAULT_CPU_QUOTA))
        image_platform = str(_get_config("image_platform", "multi") or "multi").strip()
        return {
            "ctfd_user_id": int(user.id),
            "ctfd_team_id": getattr(user, "team_id", None),
            "username": user.name,
            "challenge_id": int(challenge_id),
            "image": (cfg.image_name or "").strip() or _get_config("target_image", DEFAULT_TARGET_IMAGE),
            "image_platform": image_platform,
            "cpu_request": max(0.05, float(_get_config("target_cpu_request", "0.25") or "0.25")),
            "cpu_quota": cpu_quota,
            "memory_mb": mem_mb,
            "disk_mb": int(_get_config("instance_disk_mb", "256") or "256"),
            "internal_port": int(cfg.internal_port or 80),
            "ttl_seconds": _instance_lifetime(),
            "idempotency_key": _instance_manager_id(user.id, challenge_id),
            "required_features": ["linux_containers"],
            "network_aliases": ["vbank-app"],
            "environment": {
                "FLAG_SECRET": _get_config("flag_secret", "") or os.environ.get("FLAG_SECRET", "ctf-vbank-2024"),
                "TEAM_ID": _flag_owner_id(user),
                "CHALLENGE_KEY": (getattr(cfg, "challenge_key", None) or "").strip(),
                "TARGET_PROFILE": (getattr(cfg, "target_profile", None) or "").strip(),
            },
        }

    def _manager_start_challenge(user, challenge_id, cfg):
        data = _instance_manager_request("POST", "/instances", _manager_instance_payload(user, challenge_id, cfg))
        inst = data.get("instance") or {}
        return {
            "success": True,
            "status": inst.get("status") or "allocated",
            "username": user.name,
            "challenge_id": int(challenge_id),
            "url": inst.get("url"),
            "expires_at": int(float(data.get("expires_at") or 0)) or None,
            "network": inst.get("network_name") or inst.get("worker_id"),
            "worker": inst.get("worker_id"),
            "instance_id": inst.get("id"),
            "msg": "Challenge instance launched.",
        }

    def _manager_challenge_status(user, challenge_id):
        instance_id = _instance_manager_id(user.id, challenge_id)
        try:
            data = _instance_manager_request("GET", f"/instances/{instance_id}/status")
        except Exception:
            return {"success": True, "status": "not_found", "url": None, "challenge_id": int(challenge_id)}
        inst = data.get("instance") or {}
        return {
            "success": True,
            "status": inst.get("container_status") or inst.get("status") or "unknown",
            "health": inst.get("health"),
            "url": inst.get("url"),
            "expires_at": int(float(inst.get("expires_at") or 0)) or None,
            "network": inst.get("network_name") or inst.get("worker_id"),
            "worker": inst.get("worker_id"),
            "instance_id": inst.get("id"),
            "challenge_id": int(challenge_id),
        }

    def _manager_stop_challenge(user, challenge_id):
        instance_id = _instance_manager_id(user.id, challenge_id)
        data = _instance_manager_request("DELETE", f"/instances/{instance_id}", {})
        return {
            "success": bool(data.get("success", True)),
            "msg": "Challenge instance stopped.",
            "challenge_id": int(challenge_id),
            "instance_id": instance_id,
        }

    def _manager_global_id(user_id):
        return f"ctfd-global-u{int(user_id)}"

    def _manager_global_payload(user):
        mem_mb = _parse_mem_mb(_get_config("mem_limit", DEFAULT_MEM_LIMIT))
        cpu_quota = int(_get_config("cpu_quota", DEFAULT_CPU_QUOTA))
        image_platform = str(_get_config("image_platform", "multi") or "multi").strip()
        return {
            "ctfd_user_id": int(user.id),
            "ctfd_team_id": getattr(user, "team_id", None),
            "username": user.name,
            "role": "target",
            "challenge_id": 0,
            "image": _get_config("target_image", DEFAULT_TARGET_IMAGE),
            "image_platform": image_platform,
            "cpu_request": max(0.05, float(_get_config("target_cpu_request", "0.25") or "0.25")),
            "cpu_quota": cpu_quota,
            "memory_mb": mem_mb,
            "disk_mb": int(_get_config("instance_disk_mb", "256") or "256"),
            "internal_port": int(_get_config("target_port", DEFAULT_TARGET_PORT)),
            "ttl_seconds": _instance_lifetime(),
            "idempotency_key": _manager_global_id(user.id),
            "required_features": ["linux_containers"],
            "network_aliases": ["vbank-app"],
            "environment": {
                "FLAG_SECRET": _get_config("flag_secret", "") or os.environ.get("FLAG_SECRET", "ctf-vbank-2024"),
                "TEAM_ID": _flag_owner_id(user),
                "TARGET_PROFILE": "global",
            },
        }

    def _manager_start_global(user):
        data = _instance_manager_request("POST", "/instances", _manager_global_payload(user))
        inst = data.get("instance") or {}
        status = inst.get("container_status") or inst.get("status") or "running"
        if status == "deployed":
            status = "running"
        return {
            "success": True,
            "status": status,
            "username": user.name,
            "url": inst.get("url"),
            "expires_at": int(float(data.get("expires_at") or inst.get("expires_at") or 0)) or None,
            "network": inst.get("network_name") or inst.get("worker_id"),
            "worker": inst.get("worker_id"),
            "instance_id": inst.get("id"),
            "msg": "vBank target launched.",
        }

    def _manager_global_status(user):
        instance_id = _manager_global_id(user.id)
        try:
            data = _instance_manager_request("GET", f"/instances/{instance_id}/status")
        except Exception:
            return {"success": True, "status": "not_found", "url": None, "username": user.name}
        inst = data.get("instance") or {}
        status = inst.get("container_status") or inst.get("status") or "unknown"
        if status == "deployed":
            status = "running"
        return {
            "success": True,
            "status": status,
            "health": inst.get("health"),
            "url": inst.get("url"),
            "expires_at": int(float(inst.get("expires_at") or 0)) or None,
            "network": inst.get("network_name") or inst.get("worker_id"),
            "worker": inst.get("worker_id"),
            "instance_id": inst.get("id"),
            "username": user.name,
        }

    def _manager_stop_global(user):
        instance_id = _manager_global_id(user.id)
        data = _instance_manager_request("DELETE", f"/instances/{instance_id}", {})
        return {
            "success": bool(data.get("success", True)),
            "msg": "vBank target stopped.",
            "instance_id": instance_id,
        }
    def _manager_kali_id(user_id):
        return _instance_manager_id(user_id, 0, "kali")

    def _manager_kali_payload(user):
        mem_mb = _parse_mem_mb(_get_config("kali_mem_limit", "2048m"), 2048)
        cpu_quota = int(_get_config("kali_cpu_quota", "200000") or "200000")
        image_platform = str(_get_config("kali_image_platform", _get_config("image_platform", "multi")) or "multi").strip()
        return {
            "role": "kali",
            "ctfd_user_id": int(user.id),
            "ctfd_team_id": getattr(user, "team_id", None),
            "username": user.name,
            "challenge_id": 0,
            "image": _get_config("kali_image", DEFAULT_KALI_IMAGE_NAME),
            "image_platform": image_platform,
            "cpu_request": max(0.1, float(_get_config("kali_cpu_request", "0.5") or "0.5")),
            "cpu_quota": cpu_quota,
            "memory_mb": mem_mb,
            "disk_mb": int(_get_config("kali_disk_mb", "2048") or "2048"),
            "internal_port": int(_get_config("kali_port", DEFAULT_KALI_PORT)),
            "ttl_seconds": min(_instance_lifetime(), KALI_MAX_LIFETIME_SECONDS),
            "idempotency_key": _manager_kali_id(user.id),
            "required_features": ["linux_containers"],
            "pids_limit": int(_get_config("kali_pids_limit", "1024") or "1024"),
            "shm_size": _get_config("kali_shm_size", "512m"),
            "cap_drop": [],
            "security_opt": ["no-new-privileges:true"],
            "environment": {
                "KALI_USER": user.name,
                "VNC_PASSWORD": _kali_vnc_password(user.name),
                "TARGET_URL": "http://vbank-app",
                "TARGET_HOST": "vbank-app",
                "VBANK_URL": "http://vbank-app",
            },
            "labels": {
                "ctfd_kali_created": str(int(time.time())),
            },
        }

    def _manager_start_kali(user):
        data = _instance_manager_request("POST", "/instances", _manager_kali_payload(user))
        inst = data.get("instance") or {}
        status = inst.get("container_status") or inst.get("status") or "running"
        if status == "deployed":
            status = "running"
        return {
            "success": True,
            "status": status,
            "username": user.name,
            "role": "kali",
            "url": inst.get("url"),
            "expires_at": int(float(data.get("expires_at") or inst.get("expires_at") or 0)) or None,
            "created_at": int(float(inst.get("created_at") or time.time())),
            "max_lifetime": KALI_MAX_LIFETIME_SECONDS,
            "network": inst.get("network_name") or inst.get("worker_id"),
            "worker": inst.get("worker_id"),
            "instance_id": inst.get("id"),
            "msg": "Linux Pwn Machine launched.",
        }

    def _manager_kali_status(user):
        instance_id = _manager_kali_id(user.id)
        try:
            data = _instance_manager_request("GET", f"/instances/{instance_id}/status")
        except Exception:
            return {"success": True, "status": "not_found", "url": None, "username": user.name, "role": "kali"}
        inst = data.get("instance") or {}
        status = inst.get("container_status") or inst.get("status") or "unknown"
        if status == "deployed":
            status = "running"
        return {
            "success": True,
            "status": status,
            "health": inst.get("health"),
            "url": inst.get("url"),
            "expires_at": int(float(inst.get("expires_at") or 0)) or None,
            "created_at": int(float(inst.get("created_at") or time.time())),
            "max_lifetime": KALI_MAX_LIFETIME_SECONDS,
            "network": inst.get("network_name") or inst.get("worker_id"),
            "worker": inst.get("worker_id"),
            "instance_id": inst.get("id"),
            "username": user.name,
            "role": "kali",
        }

    def _manager_stop_kali(user):
        instance_id = _manager_kali_id(user.id)
        data = _instance_manager_request("DELETE", f"/instances/{instance_id}", {})
        return {
            "success": bool(data.get("success", True)),
            "msg": "Linux Pwn Machine stopped.",
            "instance_id": instance_id,
        }

    def _ctfd_bool(key, default=False):
        val = get_config(key)
        if val is None:
            return default
        if isinstance(val, bool):
            return val
        return str(val).strip().lower() in ("1", "true", "yes", "on")

    def _ctfd_set_bool(key, enabled):
        set_config(key, "true" if enabled else "false")

    def _get_docker_client():
        import docker as docker_lib

        return docker_lib.DockerClient(base_url=_get_config("docker_api_url", "unix:///var/run/docker.sock"))

    def _kali_vnc_password(username):
        """Derive a stable, per-user 8-char VNC password from the shared SSO secret."""
        import hmac as _hmac
        import hashlib as _hashlib
        secret = _get_config("kali_sso_secret", DEFAULT_KALI_SSO_SECRET)
        h = _hmac.new(secret.encode(), username.encode(), _hashlib.sha256).hexdigest()
        return h[:8]   # VNC passwords are capped at 8 characters

    def _safe_name(username):
        return "".join(c if c.isalnum() else "-" for c in username.lower())

    def _target_name(username):
        return TARGET_PREFIX + _safe_name(username)

    def _analytics_name(username):
        return ANALYTICS_PREFIX + _safe_name(username)

    def _kali_name(username):
        return KALI_PREFIX + _safe_name(username)

    def _network_name(username):
        return NETWORK_PREFIX + _safe_name(username)

    def _chal_target_name(username, challenge_id):
        return f"ctfd-chal-{_safe_name(username)}-c{int(challenge_id)}"

    def _chal_analytics_name(username, challenge_id):
        return f"ctfd-analytics-{_safe_name(username)}-c{int(challenge_id)}"

    def _is_per_challenge_mode():
        return _get_config("target_mode", DEFAULT_TARGET_MODE) == "per_challenge"

    def _max_challenge_instances():
        try:
            value = int(_get_config("max_challenge_instances", DEFAULT_MAX_CHALLENGE_INSTANCES))
            return max(1, value)
        except (TypeError, ValueError):
            return int(DEFAULT_MAX_CHALLENGE_INSTANCES)

    def _max_total_instances():
        try:
            return max(1, int(_get_config("max_total_instances", DEFAULT_MAX_TOTAL_INSTANCES) or DEFAULT_MAX_TOTAL_INSTANCES))
        except (TypeError, ValueError):
            return int(DEFAULT_MAX_TOTAL_INSTANCES)

    def _running_instance_count():
        try:
            client = _get_docker_client()
            return len(client.containers.list(filters={"label": "ctfd_target_user", "status": "running"}))
        except Exception:
            return 0

    def _disk_free_gb():
        path = "/var/backups" if os.path.isdir("/var/backups") else "/"
        try:
            st = os.statvfs(path)
            return round((st.f_bavail * st.f_frsize) / float(1024 ** 3), 1)
        except Exception:
            return None

    def _secrets_ok():
        weak = {
            "change_this_to_a_random_secret",
            "change_this_to_a_random_string",
            "ctf-vbank-2024",
            "ctfd-kali-sso-secret-key-change-me",
            "",
        }
        flag = (os.environ.get("FLAG_SECRET") or _get_config("flag_secret", "") or "").strip()
        key = (os.environ.get("SECRET_KEY") or os.environ.get("CTFD_SECRET_KEY") or "").strip()
        return bool(flag) and flag not in weak and bool(key) and key not in weak

    def _launch_guard_key(user, resource="target"):
        user_id = str(getattr(user, "id", user))
        return f"ctfd_target:launch:{resource}:{user_id}"

    def _guard_launch(user=None, resource="target"):
        if _bool_config("restore_lock", False):
            return "A database restore is running. Try again in a minute."
        if _ctfd_bool("paused"):
            return "The CTF is paused. Targets cannot be launched."
        if _bool_config("launches_locked", False):
            return "New targets are locked by an administrator."
        try:
            end_raw = get_config("end")
            end_ts = int(float(end_raw)) if end_raw else 0
        except (TypeError, ValueError):
            end_ts = 0
        if end_ts and int(time.time()) >= end_ts:
            return "The event has ended. Targets cannot be launched."
        cap = _max_total_instances()
        running = _running_instance_count()
        if running >= cap:
            return "The host is at capacity (%s instances). Try again shortly." % cap
        disk = _disk_free_gb()
        if disk is not None and disk < 2:
            return "Host disk is low. New targets are paused until space is freed."
        if user is not None:
            try:
                redis_client = _redis_or_none()
                if redis_client is not None:
                    key = _launch_guard_key(user, resource)
                    # Short concurrency lock (2s) scoped strictly to this resource (target vs kali vs challenge)
                    if not redis_client.set(key, "1", nx=True, ex=2):
                        for _ in range(3):
                            time.sleep(0.4)
                            if redis_client.get(key) is None:
                                break
                        if _instance_manager_enabled():
                            return None
                        return "Another launch is already in progress. Please wait a moment."
            except Exception:
                pass
        return None

    def _release_launch_guard(user=None, resource="target"):
        if user is None:
            return
        try:
            redis_client = _redis_or_none()
            if redis_client is not None:
                user_id = str(getattr(user, "id", user))
                redis_client.delete(_launch_guard_key(user, resource))
                redis_client.delete(f"ctfd_target:launch:{user_id}")
        except Exception:
            pass

    def _request_challenge_id():
        data = request.get_json(silent=True) or {}
        raw = data.get("challenge_id") or request.args.get("challenge_id") or request.form.get("challenge_id")
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    def _get_challenge_target_config(challenge_id):
        return ChallengeTargetConfig.query.filter_by(challenge_id=int(challenge_id)).first()

    def _env_host_ip():
        return (os.environ.get("HOST_IP") or "").strip()

    # Once per CTFd process: rewrite stale DB host_ip from another machine's .data
    _host_ip_synced = {"done": False}

    def _sync_host_ip_from_env():
        """Align DB host_ip with HOST_IP from start.sh/start.ps1 (once per boot).

        Pulled .data volumes often contain another machine's LAN IP. Start scripts
        rewrite HOST_IP for this host â€” sync that into CTFd config on first use so
        target URLs and network setup work without manual re-entry.
        """
        env_ip = _env_host_ip()
        if _host_ip_synced["done"]:
            return env_ip
        _host_ip_synced["done"] = True
        if not env_ip or env_ip in ("127.0.0.1", "localhost"):
            return env_ip
        try:
            cfg_ip = get_config("ctfd_target:host_ip") or ""
            if cfg_ip != env_ip:
                set_config("ctfd_target:host_ip", env_ip)
                logger.info("[HostIP] Synced ctfd_target:host_ip %r -> %r (from HOST_IP env)", cfg_ip, env_ip)
        except Exception as exc:
            logger.warning("[HostIP] Could not sync host_ip from env: %s", exc)
        return env_ip

    def _get_host_ip():
        # Sync once so a restored .data DB does not keep another machine's IP
        env_ip = _sync_host_ip_from_env()
        # 1. Plugin settings (now synced, or admin-overridden after boot)
        cfg_ip = _get_config("host_ip", "")
        if cfg_ip and cfg_ip not in ("127.0.0.1", "localhost", ""):
            return cfg_ip
        # 2. HOST_IP env (written by start.sh / start.ps1 for THIS machine)
        if env_ip and env_ip not in ("127.0.0.1", "localhost", ""):
            return env_ip
        # 3. Dynamic fallback: resolve the Docker host gateway from inside the container
        try:
            import subprocess as _sp
            out = _sp.run(
                ["ip", "route", "show", "default"],
                capture_output=True, text=True, timeout=2
            ).stdout
            for _line in out.splitlines():
                parts = _line.split()
                if "default" in parts and "via" in parts:
                    gw = parts[parts.index("via") + 1]
                    if gw and not gw.startswith("127."):
                        return gw
        except Exception:
            pass
        # 4. Last resort
        return env_ip or "localhost"

    def _instance_lifetime():
        try:
            return int(_get_config("instance_lifetime", DEFAULT_INSTANCE_LIFETIME))
        except ValueError:
            return int(DEFAULT_INSTANCE_LIFETIME)

    def _make_target_url(port):
        return f"http://{_get_host_ip()}:{port}/"

    def _make_kali_url(port):
        # Parrot OS uses plain noVNC (HTTP), not KasmVNC HTTPS
        return f"http://{_get_host_ip()}:{port}/"

    def _target_subdomain(username):
        return f"{_safe_name(username)}.{_LAB_DOMAIN}"

    def _kali_subdomain(username):
        return f"kali-{_safe_name(username)}.{_LAB_DOMAIN}"

    def _connect_to_proxy_net(client, container):
        """Connect container to proxy-net so nginx-proxy can route to it."""
        try:
            net = client.networks.get("proxy-net")
            net.connect(container)
            logger.info(f"[ProxyNet] {container.name} connected to proxy-net")
            return True
        except Exception as exc:
            logger.warning(f"[ProxyNet] connect failed: {exc}")
            return False

    def _ensure_network_aliases(network, container, aliases):
        """Ensure Docker DNS resolves stable service names on the per-user network."""
        wanted = set(aliases)
        try:
            network.reload()
            container.reload()
            network_info = container.attrs.get("NetworkSettings", {}).get("Networks", {}).get(network.name, {})
            current = set(network_info.get("Aliases") or [])
            if wanted.issubset(current):
                return True

            if network_info:
                network.disconnect(container, force=True)
            network.connect(container, aliases=sorted(wanted))
            logger.info(f"[NetworkDNS] {container.name} aliases set: {', '.join(sorted(wanted))}")
            return True
        except Exception as exc:
            logger.warning(f"[NetworkDNS] alias update failed for {container.name}: {exc}")
            return False

    def _find_container(client, name):
        try:
            return client.containers.get(name)
        except Exception:
            return None

    def _find_network(client, name):
        try:
            return client.networks.get(name)
        except Exception:
            return None

    def _ensure_network(client, username):
        network_name = _network_name(username)
        network = _find_network(client, network_name)
        if network:
            return network
        return client.networks.create(
            network_name,
            driver="bridge",
            internal=False,
            labels={
                "ctfd_target_user": username,
                "ctfd_target_managed": "true",
            },
        )

    def _remove_network_if_unused(client, username):
        network = _find_network(client, _network_name(username))
        if not network:
            return
        try:
            network.reload()
            containers = network.attrs.get("Containers") or {}
            if not containers:
                network.remove()
        except Exception:
            pass

    def _extract_host_port(container, internal_port):
        try:
            container.reload()
            bindings = container.attrs["NetworkSettings"]["Ports"].get(f"{internal_port}/tcp")
            if bindings:
                return int(bindings[0]["HostPort"])
        except Exception:
            return None
        return None

    def _port_range(start_key, end_key, default_start, default_end):
        try:
            start = int(_get_config(start_key, default_start))
            end = int(_get_config(end_key, default_end))
            if start < 1 or end > 65535 or start > end:
                raise ValueError()
            return start, end
        except ValueError:
            return int(default_start), int(default_end)

    def _used_host_ports(client):
        used = set()
        try:
            for container in client.containers.list(all=True):
                ports = container.attrs.get("NetworkSettings", {}).get("Ports", {}) or {}
                for bindings in ports.values():
                    for binding in bindings or []:
                        try:
                            used.add(int(binding.get("HostPort")))
                        except Exception:
                            pass
        except Exception:
            pass
        return used

    def _allocate_host_port(client, role, username, extra=""):
        if role == "target":
            start, end = _port_range(
                "target_port_range_start",
                "target_port_range_end",
                DEFAULT_TARGET_PORT_RANGE_START,
                DEFAULT_TARGET_PORT_RANGE_END,
            )
        else:
            start, end = _port_range(
                "kali_port_range_start",
                "kali_port_range_end",
                DEFAULT_KALI_PORT_RANGE_START,
                DEFAULT_KALI_PORT_RANGE_END,
            )

        used = _used_host_ports(client)
        size = end - start + 1
        seed_key = f"{role}:{username}" if not extra else f"{role}:{username}:{extra}"
        seed = int(hashlib.sha256(seed_key.encode()).hexdigest()[:8], 16)
        for offset in range(size):
            port = start + ((seed + offset) % size)
            if port not in used:
                return port
        return None

    def _container_url(role, container):
        if role in ("target", "challenge"):
            if role == "challenge":
                internal = container.labels.get("ctfd_target_port") or _get_config("target_port", DEFAULT_TARGET_PORT)
            else:
                internal = _get_config("target_port", DEFAULT_TARGET_PORT)
            port = _extract_host_port(container, internal)
            return _make_target_url(port) if port else None
        if role == "kali":
            port = _extract_host_port(container, _get_config("kali_port", DEFAULT_KALI_PORT))
            return _make_kali_url(port) if port else None
        return None

    def _safe_remove_container(container):
        if not container:
            return
        try:
            container.remove(force=True)
        except Exception:
            pass

    def _cleanup_user_stack(client, username):
        for name in (_target_name(username), _analytics_name(username), _kali_name(username)):
            _safe_remove_container(_find_container(client, name))
        _remove_network_if_unused(client, username)

    def _ensure_analytics_sidecar(client, username, expires_at, team_id="0"):
        network = _ensure_network(client, username)
        analytics = _find_container(client, _analytics_name(username))
        if analytics and analytics.status == "running":
            _ensure_network_aliases(network, analytics, ["vbank-analytics", _analytics_name(username)])
            return analytics
        if analytics:
            _safe_remove_container(analytics)

        flag_secret = _get_config("flag_secret", "") or os.environ.get("FLAG_SECRET", "vbank_ctf_hmac_2024_change_me")
        analytics = client.containers.run(
            _get_config("analytics_image", DEFAULT_ANALYTICS_IMAGE),
            name=_analytics_name(username),
            hostname="vbank-analytics",
            detach=True,
            network=network.name,
            labels={
                "ctfd_target_user": username,
                "ctfd_target_role": "analytics",
                "ctfd_target_expires": str(expires_at),
            },
            environment={
                "FLAG_SECRET": flag_secret,
                "TEAM_ID": str(team_id),
            },
        )
        _ensure_network_aliases(network, analytics, ["vbank-analytics", _analytics_name(username)])
        return analytics

    def _start_target_stack(username, team_id="0", user_id=0):
        client = _get_docker_client()
        expires_at = int(time.time()) + _instance_lifetime()
        network = _ensure_network(client, username)

        target = _find_container(client, _target_name(username))
        if target:
            try:
                target.reload()
                if target.status == "running":
                    _ensure_network_aliases(network, target, ["vbank-app", _target_name(username)])
                    # Ensure it's not paused (macOS Docker Desktop optimization)
                    if target.status == "paused":
                        try:
                            target.unpause()
                        except Exception:
                            pass
                    return target, expires_at
            except Exception:
                pass
            # If we get here, remove the old container
            _safe_remove_container(target)

        _ensure_analytics_sidecar(client, username, expires_at, team_id=team_id)

        flag_secret = _get_config("flag_secret", "") or os.environ.get("FLAG_SECRET", "vbank_ctf_hmac_2024_change_me")
        target_port = _get_config("target_port", DEFAULT_TARGET_PORT)
        host_port = _allocate_host_port(client, "target", username)

        try:
            target = client.containers.run(
                _get_config("target_image", DEFAULT_TARGET_IMAGE),
                name=_target_name(username),
                hostname="vbank-app",
                detach=True,
                network=network.name,
                ports={f"{target_port}/tcp": ("0.0.0.0", host_port) if host_port else None},
                mem_limit=_get_config("mem_limit", DEFAULT_MEM_LIMIT),
                cpu_period=100000,
                cpu_quota=int(_get_config("cpu_quota", DEFAULT_CPU_QUOTA)),
                restart_policy={"Name": "no"},
                environment={
                    "FLAG_SECRET": flag_secret,
                    "TEAM_ID": str(team_id),
                    "ANALYTICS_BASE_URL": f"http://{_analytics_name(username)}:3000",
                },
                labels={
                    "ctfd_target_user":    username,
                    "ctfd_target_role":    "target",
                    "ctfd_target_expires": str(expires_at),
                },
                healthcheck=_healthcheck_spec(target_port, "/"),
            )
            _ensure_network_aliases(network, target, ["vbank-app", _target_name(username)])
            # Give container time to start and bind port (critical for macOS Docker Desktop)
            time.sleep(1)
            
            # Verify port was bound
            max_retries = 5
            for attempt in range(max_retries):
                try:
                    target.reload()
                    if _extract_host_port(target, target_port):
                        logger.info(f"[TargetStart] {username}: container started successfully")
                        return target, expires_at
                except Exception:
                    pass
                if attempt < max_retries - 1:
                    time.sleep(0.5)
            
            logger.warning(f"[TargetStart] {username}: port binding took longer than expected")
            return target, expires_at
            
        except Exception as e:
            logger.error(f"[TargetStart] {username}: Failed to start container: {e}")
            raise

    def _healthcheck_spec(port=80, path="/"):
        port = int(port or 80)
        path = (path or "/").strip() or "/"
        if not path.startswith("/"):
            path = "/" + path
        url = f"http://127.0.0.1:{port}{path}"
        return {
            "test": [
                "CMD-SHELL",
                "python -c \"import urllib.request; urllib.request.urlopen(%r, timeout=2).read(1)\"" % url,
            ],
            "interval": 3_000_000_000,
            "timeout": 2_000_000_000,
            "retries": 3,
            "start_period": 5_000_000_000,
        }

    def _count_user_challenge_instances(client, username):
        count = 0
        try:
            for container in client.containers.list(filters={"label": f"ctfd_target_user={username}"}):
                if container.labels.get("ctfd_target_role") == "challenge":
                    count += 1
        except Exception:
            pass
        return count

    def _ensure_challenge_analytics(client, username, challenge_id, expires_at, team_id="0"):
        network = _ensure_network(client, username)
        name = _chal_analytics_name(username, challenge_id)
        analytics = _find_container(client, name)
        if analytics and analytics.status == "running":
            _ensure_network_aliases(network, analytics, [name])
            return analytics
        if analytics:
            _safe_remove_container(analytics)

        flag_secret = _get_config("flag_secret", "") or os.environ.get("FLAG_SECRET", "vbank_ctf_hmac_2024_change_me")
        analytics = client.containers.run(
            _get_config("analytics_image", DEFAULT_ANALYTICS_IMAGE),
            name=name,
            hostname=name,
            detach=True,
            network=network.name,
            labels={
                "ctfd_target_user": username,
                "ctfd_target_role": "challenge_analytics",
                "ctfd_target_challenge": str(challenge_id),
                "ctfd_target_expires": str(expires_at),
            },
            environment={
                "FLAG_SECRET": flag_secret,
                "TEAM_ID": str(team_id),
            },
        )
        _ensure_network_aliases(network, analytics, [name])
        return analytics

    def _start_challenge_stack(username, challenge_id, cfg, team_id="0"):
        client = _get_docker_client()
        expires_at = int(time.time()) + _instance_lifetime()
        network = _ensure_network(client, username)
        name = _chal_target_name(username, challenge_id)
        analytics_name = _chal_analytics_name(username, challenge_id)

        target = _find_container(client, name)
        if target:
            try:
                target.reload()
                if target.status == "running":
                    if target.status == "paused":
                        try:
                            target.unpause()
                        except Exception:
                            pass
                    return target, int(target.labels.get("ctfd_target_expires") or expires_at)
            except Exception:
                pass
            _safe_remove_container(target)

        running = _count_user_challenge_instances(client, username)
        cap = _max_challenge_instances()
        if running >= cap:
            raise RuntimeError(f"Instance limit reached ({cap}). Stop another challenge target first.")

        if cfg.needs_analytics:
            _ensure_challenge_analytics(client, username, challenge_id, expires_at, team_id=team_id)

        flag_secret = _get_config("flag_secret", "") or os.environ.get("FLAG_SECRET", "vbank_ctf_hmac_2024_change_me")
        internal_port = str(int(cfg.internal_port or 80))
        host_port = _allocate_host_port(client, "target", username, extra=str(challenge_id))
        mem_limit = (cfg.mem_limit or "").strip() or _get_config("mem_limit", DEFAULT_MEM_LIMIT)
        cpu_quota = (cfg.cpu_quota or "").strip() or _get_config("cpu_quota", DEFAULT_CPU_QUOTA)
        image = (cfg.image_name or "").strip() or _get_config("target_image", DEFAULT_TARGET_IMAGE)
        env = {
            "FLAG_SECRET": flag_secret,
            "TEAM_ID": str(team_id),
            "CHALLENGE_KEY": (getattr(cfg, "challenge_key", None) or "").strip(),
            "TARGET_PROFILE": (getattr(cfg, "target_profile", None) or "").strip(),
        }
        if cfg.needs_analytics:
            env["ANALYTICS_BASE_URL"] = f"http://{analytics_name}:3000"

        target = client.containers.run(
            image,
            name=name,
            hostname=f"vbank-c{challenge_id}",
            detach=True,
            network=network.name,
            ports={f"{internal_port}/tcp": ("0.0.0.0", host_port) if host_port else None},
            mem_limit=mem_limit,
            cpu_period=100000,
            cpu_quota=int(cpu_quota),
            restart_policy={"Name": "no"},
            environment=env,
            labels={
                "ctfd_target_user": username,
                "ctfd_target_role": "challenge",
                "ctfd_target_challenge": str(challenge_id),
                "ctfd_target_port": internal_port,
                "ctfd_target_expires": str(expires_at),
                "ctfd_target_key": (getattr(cfg, "challenge_key", None) or ""),
                "ctfd_target_profile": (getattr(cfg, "target_profile", None) or ""),
            },
            healthcheck=_healthcheck_spec(internal_port, getattr(cfg, "health_path", None) or "/"),
        )
        _ensure_network_aliases(network, target, [f"vbank-c{challenge_id}", name])
        time.sleep(1)
        for attempt in range(5):
            try:
                target.reload()
                if _extract_host_port(target, internal_port):
                    logger.info("[ChalStart] %s c%s started", username, challenge_id)
                    return target, expires_at
            except Exception:
                pass
            if attempt < 4:
                time.sleep(0.5)
        return target, expires_at

    def _stop_challenge_stack(username, challenge_id):
        client = _get_docker_client()
        _safe_remove_container(_find_container(client, _chal_target_name(username, challenge_id)))
        _safe_remove_container(_find_container(client, _chal_analytics_name(username, challenge_id)))
        _expiry_overrides.pop(f"{username}:challenge:{challenge_id}", None)
        _expiry_overrides.pop(f"{username}:challenge_analytics:{challenge_id}", None)
        _remove_network_if_unused(client, username)

    def _challenge_container_status(username, challenge_id):
        client = _get_docker_client()
        container = _find_container(client, _chal_target_name(username, challenge_id))
        if not container:
            return {
                "success": True,
                "status": "not_found",
                "url": None,
                "username": username,
                "challenge_id": challenge_id,
            }
        try:
            container.reload()
        except Exception:
            return {
                "success": True,
                "status": "docker_error",
                "url": None,
                "username": username,
                "challenge_id": challenge_id,
            }
        if container.status == "paused":
            try:
                container.unpause()
                container.reload()
            except Exception:
                pass
        role_key = f"challenge:{challenge_id}"
        label_exp = container.labels.get("ctfd_target_expires")
        eff_exp = _effective_expiry(username, role_key, label_exp)
        if eff_exp and time.time() > eff_exp:
            _expiry_overrides.pop(f"{username}:{role_key}", None)
            _stop_challenge_stack(username, challenge_id)
            return {
                "success": True,
                "status": "not_found",
                "url": None,
                "username": username,
                "challenge_id": challenge_id,
            }
        return {
            "success": True,
            "status": container.status,
            "username": username,
            "role": "challenge",
            "challenge_id": challenge_id,
            "expires_at": int(eff_exp) if eff_exp else None,
            "url": _container_url("challenge", container),
            "network": _network_name(username),
        }

    def _start_kali_instance(username, team_id="0"):
        client = _get_docker_client()
        expires_at = int(time.time()) + _instance_lifetime()
        network = _ensure_network(client, username)
        _ensure_analytics_sidecar(client, username, expires_at, team_id=team_id)

        kali = _find_container(client, _kali_name(username))
        if kali:
            try:
                kali.reload()
                if kali.status == "running":
                    # Ensure it's not paused
                    if kali.status == "paused":
                        try:
                            kali.unpause()
                        except Exception:
                            pass
                    return kali, expires_at
            except Exception:
                pass
            # If we get here, remove the old container
            _safe_remove_container(kali)

        try:
            kali_port = _get_config('kali_port', DEFAULT_KALI_PORT)
            host_port = _allocate_host_port(client, "kali", username)
            created_at = int(time.time())
            kali = client.containers.run(
                _get_config("kali_image", DEFAULT_KALI_IMAGE_NAME),
                name=_kali_name(username),
                hostname="kali-pwn",
                detach=True,
                network=network.name,
                ports={f"{kali_port}/tcp": ("0.0.0.0", host_port) if host_port else None},
                shm_size="512m",
                environment={
                    "KALI_USER":    username,
                    "VNC_PASSWORD": _kali_vnc_password(username),
                    "TARGET_URL":   "http://vbank-app",
                    "TARGET_HOST":  "vbank-app",
                    "VBANK_URL":    "http://vbank-app",
                },
                labels={
                    "ctfd_target_user":    username,
                    "ctfd_target_role":    "kali",
                    "ctfd_target_expires": str(expires_at),
                    "ctfd_kali_created":   str(created_at),
                },
            )
            # Give container time to start and bind port
            time.sleep(1)
            
            # Connect to proxy network
            _connect_to_proxy_net(client, kali)
            
            # Verify port was bound
            for attempt in range(5):
                try:
                    kali.reload()
                    if _extract_host_port(kali, kali_port):
                        logger.info(f"[KaliStart] {username}: container started successfully")
                        return kali, expires_at
                except Exception:
                    pass
                if attempt < 4:
                    time.sleep(0.5)
            
            logger.warning(f"[KaliStart] {username}: port binding took longer than expected")
            return kali, expires_at
            
        except Exception as e:
            logger.error(f"[KaliStart] {username}: Failed to start container: {e}")
            raise

    def _container_status(username, role):
        client = _get_docker_client()
        name = {
            "target": _target_name(username),
            "analytics": _analytics_name(username),
            "kali": _kali_name(username),
        }[role]
        container = _find_container(client, name)
        if not container:
            return {"success": True, "status": "not_found", "url": None, "username": username}

        try:
            container.reload()
        except Exception:
            return {"success": True, "status": "docker_error", "url": None, "username": username}

        # Docker Desktop resource-saver pauses idle containers â€” unpause transparently
        if container.status == "paused":
            try:
                container.unpause()
                container.reload()
            except Exception:
                pass

        label_exp = container.labels.get("ctfd_target_expires")
        role_for_exp = {"target": "target", "kali": "kali", "analytics": "analytics"}.get(role, role)
        eff_exp = _effective_expiry(username, role_for_exp, label_exp)
        if eff_exp and time.time() > eff_exp:
            _expiry_overrides.pop(f"{username}:{role_for_exp}", None)
            _safe_remove_container(container)
            _remove_network_if_unused(client, username)
            return {"success": True, "status": "not_found", "url": None, "username": username}
        expires_at = str(int(eff_exp)) if eff_exp else label_exp

        response = {
            "success": True,
            "status": container.status,
            "username": username,
            "role": role,
            "expires_at": int(float(expires_at)) if expires_at else None,
            "url": _container_url(role, container),
            "network": _network_name(username),
        }
        return response

    def _collect_container_snapshot(container):
        role = container.labels.get("ctfd_target_role", "unknown")
        username = container.labels.get("ctfd_target_user", "unknown")
        challenge_id = container.labels.get("ctfd_target_challenge") or ""
        expires_at = container.labels.get("ctfd_target_expires")
        ports = container.attrs.get("NetworkSettings", {}).get("Ports", {})
        host_port = None
        for bindings in ports.values():
            if bindings:
                host_port = bindings[0].get("HostPort")
                break
        cpu_percent = None
        mem_usage = None
        # Skip docker stats by default â€” they block ~2s per container.

        started_at = container.attrs.get("State", {}).get("StartedAt", "")
        uptime = "-"
        if started_at:
            try:
                created_ts = started_at.split(".")[0].replace("T", " ")
                uptime = created_ts
            except Exception:
                pass

        return {
            "name": container.name,
            "username": username,
            "role": role,
            "challenge_id": challenge_id,
            "status": container.status,
            "port": host_port,
            "network": next(iter((container.attrs.get("NetworkSettings", {}).get("Networks") or {}).keys()), "-"),
            "expires_at": expires_at,
            "cpu_percent": cpu_percent,
            "mem_usage": mem_usage,
            "uptime": uptime,
            "url": _container_url("challenge" if role == "challenge" else role, container),
        }

    def _effective_expiry(username, role, label_expiry):
        """Return the latest expiry from label or in-memory override (or Redis)."""
        key = f"{username}:{role}"
        override = _expiry_overrides.get(key)
        label_ts = float(label_expiry) if label_expiry else 0.0
        redis_ts = 0.0
        try:
            r = _redis_or_none()
            if r:
                val = r.get(f"ctfd:ext:{username}:{role}")
                if val:
                    redis_ts = float(val)
        except Exception:
            pass
        return max(label_ts, override or 0.0, redis_ts)

    def _auto_restart_container(client, container, role, username):
        """Restart an exited managed container that hasn't expired yet."""
        try:
            expires_at = container.labels.get("ctfd_target_expires")
            if expires_at and time.time() > float(expires_at):
                return  # expired â€” cleanup loop will remove it
            # Docker stores the network by ID; if it was deleted a new same-named
            # network won't match. Remove the stale container so relaunch creates fresh.
            if not _find_network(client, _network_name(username)):
                logger.warning(f"[AutoRestart] Network missing for {username}, removing stale {role} container")
                _safe_remove_container(container)
                return
            container.start()
            logger.info(f"[AutoRestart] Restarted {role} container for {username}")
            # Notify the player via Redis (read by ctf-live-plugin events endpoint)
            try:
                r = _redis_or_none()
                if r:
                    import json as _json
                    r.setex(
                        f"ctfd:restart_alert:{username}",
                        300,
                        _json.dumps({"role": role, "timestamp": time.time()}),
                    )
            except Exception:
                pass
        except Exception as exc:
            logger.warning(f"[AutoRestart] Failed to restart {role}/{username}: {exc}")
            _safe_remove_container(container)

    def _cleanup_loop():
        while True:
            time.sleep(30)   # check every 30 s (was 60)
            try:
                client = _get_docker_client()
                containers = client.containers.list(all=True, filters={"label": "ctfd_target_user"})
                users = set()
                for container in containers:
                    username = container.labels.get("ctfd_target_user", "")
                    role     = container.labels.get("ctfd_target_role", "")
                    chal_id  = container.labels.get("ctfd_target_challenge")
                    users.add(username)

                    expiry_role = f"{role}:{chal_id}" if chal_id and role in ("challenge", "challenge_analytics") else role
                    label_exp = container.labels.get("ctfd_target_expires")
                    eff_exp = _effective_expiry(username, expiry_role, label_exp)
                    if eff_exp and time.time() > eff_exp:
                        _expiry_overrides.pop(f"{username}:{expiry_role}", None)
                        _safe_remove_container(container)
                        continue

                    # â”€â”€ Unpause containers paused by Docker Desktop resource saver â”€â”€
                    if container.status == "paused" and username and role in ("target", "kali", "challenge"):
                        try:
                            container.unpause()
                        except Exception:
                            pass

                    # â”€â”€ Auto-restart: revive exited (crashed) containers â”€â”€â”€â”€â”€â”€
                    if container.status in ("exited", "dead") and username and role in ("target", "kali", "challenge"):
                        _auto_restart_container(client, container, role, username)

                for username in users:
                    if username:
                        target    = _find_container(client, _target_name(username))
                        kali      = _find_container(client, _kali_name(username))
                        analytics = _find_container(client, _analytics_name(username))
                        if not target and not kali and analytics:
                            _safe_remove_container(analytics)
                        _remove_network_if_unused(client, username)
                threading.Thread(target=_auto_backup_tick, daemon=True, name="auto-backup").start()
            except Exception:
                pass

    with _cleanup_lock:
        global _cleanup_started
        if not _cleanup_started:
            threading.Thread(target=_cleanup_loop, daemon=True).start()
            _cleanup_started = True

    @page_blueprint.route("/target/mode", methods=["GET"])
    def target_mode():
        return json.dumps({"mode": _get_config("target_mode", DEFAULT_TARGET_MODE)})

    @page_blueprint.route("/target/start", methods=["POST"])
    @authed_only
    def start_target():
        if _get_config("target_mode", DEFAULT_TARGET_MODE) != "global":
            return json.dumps({"success": False, "msg": "Global target is disabled. Use per-challenge instances."})

        user = current_user.get_current_user()
        if not user or not user.name:
            return json.dumps({"success": False, "msg": "Not authenticated."})
        blocked = _guard_launch(user, resource="target")
        if blocked:
            return json.dumps({"success": False, "msg": blocked})

        team_id = _flag_owner_id(user)
        try:
            if _instance_manager_enabled():
                return json.dumps(_manager_start_global(user))
            target, expires_at = _start_target_stack(user.name, team_id=team_id, user_id=user.id)
            target_url = _container_url("target", target)
            return json.dumps({
                "success": True,
                "status": "running",
                "username": user.name,
                "url": target_url,
                "expires_at": expires_at,
                "network": _network_name(user.name),
                "msg": "Target machine launched.",
            })
        except Exception as exc:
            import traceback

            traceback.print_exc()
            return json.dumps({"success": False, "msg": f"Error: {exc}"})
        finally:
            _release_launch_guard(user, resource="target")

    @page_blueprint.route("/target/ip", methods=["GET"])
    @authed_only
    def get_team_ip_api():
        user = current_user.get_current_user()
        if not user:
            return json.dumps({"success": False})
        subdomain = _target_subdomain(user.name)
        return json.dumps({
            "success": True,
            "url":     f"http://{subdomain}/",
            "subdomain": subdomain,
        })

    @page_blueprint.route("/target/status", methods=["GET"])
    @authed_only
    def target_status():
        user = current_user.get_current_user()
        if not user or not user.name:
            return json.dumps({"success": False, "msg": "Not authenticated."})
        if _get_config("target_mode", DEFAULT_TARGET_MODE) == "global" and _instance_manager_enabled():
            return json.dumps(_manager_global_status(user))
        return json.dumps(_container_status(user.name, "target"))

    @page_blueprint.route("/target/stop", methods=["DELETE"])
    @authed_only
    def stop_target():
        user = current_user.get_current_user()
        if not user or not user.name:
            return json.dumps({"success": False, "msg": "Not authenticated."})
        try:
            if _get_config("target_mode", DEFAULT_TARGET_MODE) == "global" and _instance_manager_enabled():
                return json.dumps(_manager_stop_global(user))
            client = _get_docker_client()
            _safe_remove_container(_find_container(client, _target_name(user.name)))
            analytics = _find_container(client, _analytics_name(user.name))
            kali = _find_container(client, _kali_name(user.name))
            if not kali and analytics:
                _safe_remove_container(analytics)
            _remove_network_if_unused(client, user.name)
            return json.dumps({"success": True, "msg": "Target machine stopped."})
        except Exception as exc:
            return json.dumps({"success": False, "msg": str(exc)})
        finally:
            _release_launch_guard(user, resource="target")

    @page_blueprint.route("/challenge-target/configs", methods=["GET"])
    @authed_only
    def challenge_target_configs():
        if not _is_per_challenge_mode():
            return json.dumps({"success": True, "mode": "global", "ids": [], "configs": {}})
        rows = ChallengeTargetConfig.query.filter_by(enabled=True).all()
        configs = {str(row.challenge_id): row.to_dict() for row in rows}
        return json.dumps({
            "success": True,
            "mode": "per_challenge",
            "ids": [row.challenge_id for row in rows],
            "configs": configs,
            "max_instances": _max_challenge_instances(),
        })

    @page_blueprint.route("/challenge-target/start", methods=["POST"])
    @authed_only
    def start_challenge_target():
        if not _is_per_challenge_mode():
            return json.dumps({"success": False, "msg": "Per-challenge targets are disabled. Use the global Launch Target banner."})
        user = current_user.get_current_user()
        if not user or not user.name:
            return json.dumps({"success": False, "msg": "Not authenticated."})
        challenge_id = _request_challenge_id()
        if not challenge_id:
            return json.dumps({"success": False, "msg": "challenge_id is required."})
        resource = f"chal_{challenge_id}"
        blocked = _guard_launch(user, resource=resource)
        if blocked:
            return json.dumps({"success": False, "msg": blocked})
        cfg = _get_challenge_target_config(challenge_id)
        if not cfg or not cfg.enabled:
            _release_launch_guard(user, resource=resource)
            return json.dumps({"success": False, "msg": "This challenge has no target instance configured."})
        try:
            if _instance_manager_enabled():
                return json.dumps(_manager_start_challenge(user, challenge_id, cfg))
            target, expires_at = _start_challenge_stack(
                user.name, challenge_id, cfg, team_id=_flag_owner_id(user)
            )
            return json.dumps({
                "success": True,
                "status": "running",
                "username": user.name,
                "challenge_id": challenge_id,
                "url": _container_url("challenge", target),
                "expires_at": expires_at,
                "network": _network_name(user.name),
                "msg": "Challenge instance launched.",
            })
        except Exception as exc:
            logger.exception("[ChalStart] failed")
            return json.dumps({"success": False, "msg": str(exc)})
        finally:
            _release_launch_guard(user, resource=resource)

    @page_blueprint.route("/challenge-target/status", methods=["GET"])
    @authed_only
    def challenge_target_status():
        if not _is_per_challenge_mode():
            return json.dumps({"success": True, "status": "not_found", "mode": "global"})
        user = current_user.get_current_user()
        if not user or not user.name:
            return json.dumps({"success": False, "msg": "Not authenticated."})
        challenge_id = _request_challenge_id()
        if not challenge_id:
            return json.dumps({"success": False, "msg": "challenge_id is required."})
        if _instance_manager_enabled():
            return json.dumps(_manager_challenge_status(user, challenge_id))
        return json.dumps(_challenge_container_status(user.name, challenge_id))

    @page_blueprint.route("/challenge-target/stop", methods=["DELETE"])
    @authed_only
    def stop_challenge_target():
        if not _is_per_challenge_mode():
            return json.dumps({"success": False, "msg": "Per-challenge targets are disabled."})
        user = current_user.get_current_user()
        if not user or not user.name:
            return json.dumps({"success": False, "msg": "Not authenticated."})
        challenge_id = _request_challenge_id()
        if not challenge_id:
            return json.dumps({"success": False, "msg": "challenge_id is required."})
        resource = f"chal_{challenge_id}"
        try:
            if _instance_manager_enabled():
                return json.dumps(_manager_stop_challenge(user, challenge_id))
            _stop_challenge_stack(user.name, challenge_id)
            return json.dumps({"success": True, "msg": "Challenge instance stopped.", "challenge_id": challenge_id})
        except Exception as exc:
            return json.dumps({"success": False, "msg": str(exc)})
        finally:
            _release_launch_guard(user, resource=resource)

    @page_blueprint.route("/challenge-target/extend", methods=["POST"])
    @authed_only
    def extend_challenge_target():
        if not _is_per_challenge_mode():
            return json.dumps({"success": False, "msg": "Per-challenge targets are disabled."})
        user = current_user.get_current_user()
        if not user or not user.name:
            return json.dumps({"success": False, "msg": "Not authenticated."})
        challenge_id = _request_challenge_id()
        if not challenge_id:
            return json.dumps({"success": False, "msg": "challenge_id is required."})
        try:
            client = _get_docker_client()
            extension = _instance_lifetime()
            extended = []
            pairs = [
                ("challenge", _chal_target_name(user.name, challenge_id)),
                ("challenge_analytics", _chal_analytics_name(user.name, challenge_id)),
            ]
            for role, name in pairs:
                c = _find_container(client, name)
                if not c:
                    continue
                c.reload()
                expiry_role = f"{role}:{challenge_id}"
                old_exp = _effective_expiry(user.name, expiry_role, c.labels.get("ctfd_target_expires"))
                new_exp = max(old_exp or time.time(), time.time()) + extension
                _expiry_overrides[f"{user.name}:{expiry_role}"] = new_exp
                try:
                    r = _redis_or_none()
                    if r:
                        r.setex(f"ctfd:ext:{user.name}:{expiry_role}", int(extension * 2), str(new_exp))
                except Exception:
                    pass
                extended.append(role)
            if not extended:
                return json.dumps({"success": False, "msg": "No instance running for this challenge."})
            return json.dumps({
                "success": True,
                "msg": f"Extended by {extension // 60} minutes.",
                "extension_seconds": extension,
                "challenge_id": challenge_id,
            })
        except Exception as exc:
            return json.dumps({"success": False, "msg": str(exc)})

    @page_blueprint.route("/target/extend", methods=["POST"])
    @authed_only
    def extend_target():
        """Extend the lifetime of the current user's target + kali containers by instance_lifetime seconds."""
        user = current_user.get_current_user()
        if not user or not user.name:
            return json.dumps({"success": False, "msg": "Not authenticated."})
        try:
            client = _get_docker_client()
            extension = _instance_lifetime()
            extended = []
            for role, name_fn in [("target", _target_name), ("kali", _kali_name), ("analytics", _analytics_name)]:
                c = _find_container(client, name_fn(user.name))
                if not c:
                    continue
                c.reload()
                old_exp = _effective_expiry(user.name, role, c.labels.get("ctfd_target_expires"))
                new_exp = max(old_exp, time.time()) + extension
                # Store in in-memory dict (always works) and Redis (if available)
                _expiry_overrides[f"{user.name}:{role}"] = new_exp
                try:
                    r = _redis_or_none()
                    if r:
                        r.setex(f"ctfd:ext:{user.name}:{role}", int(extension * 2), str(new_exp))
                except Exception:
                    pass
                extended.append(role)
            return json.dumps({
                "success": True,
                "msg": f"Extended {', '.join(extended)} by {extension // 60} minutes.",
                "extension_seconds": extension,
            })
        except Exception as exc:
            return json.dumps({"success": False, "msg": str(exc)})

    KALI_MAX_LIFETIME_SECONDS = 70 * 60  # 70-minute hard cap from first launch

    @page_blueprint.route("/kali/extend", methods=["POST"])
    @authed_only
    def extend_kali():
        """Extend the Kali container by instance_lifetime seconds, capped at 70 min total from launch."""
        user = current_user.get_current_user()
        if not user or not user.name:
            return json.dumps({"success": False, "msg": "Not authenticated."})
        try:
            client = _get_docker_client()
            c = _find_container(client, _kali_name(user.name))
            if not c:
                return json.dumps({"success": False, "msg": "No Kali machine running."})
            c.reload()
            created_at = float(c.labels.get("ctfd_kali_created") or c.labels.get("ctfd_target_expires") or time.time())
            elapsed = time.time() - created_at
            if elapsed >= KALI_MAX_LIFETIME_SECONDS:
                return json.dumps({
                    "success": False,
                    "msg": "70-minute session limit reached â€” Kali machine cannot be extended further.",
                    "limit_reached": True,
                })
            extension = _instance_lifetime()
            extended = []
            for role, name_fn in [("kali", _kali_name), ("analytics", _analytics_name)]:
                cont = _find_container(client, name_fn(user.name))
                if not cont:
                    continue
                cont.reload()
                old_exp = _effective_expiry(user.name, role, cont.labels.get("ctfd_target_expires"))
                new_exp = max(old_exp, time.time()) + extension
                _expiry_overrides[f"{user.name}:{role}"] = new_exp
                try:
                    r = _redis_or_none()
                    if r:
                        r.setex(f"ctfd:ext:{user.name}:{role}", int(extension * 2), str(new_exp))
                except Exception:
                    pass
                extended.append(role)
            return json.dumps({
                "success": True,
                "msg": f"Kali machine extended by {extension // 60} minutes.",
                "extension_seconds": extension,
            })
        except Exception as exc:
            return json.dumps({"success": False, "msg": str(exc)})

    @page_blueprint.route("/kali/reset", methods=["POST"])
    @authed_only
    def reset_kali():
        """Auto-extend: reset Pwn Machine to 60 min from now, but only if under the 70-min session cap."""
        user = current_user.get_current_user()
        if not user or not user.name:
            return json.dumps({"success": False, "msg": "Not authenticated."})
        try:
            client = _get_docker_client()
            c = _find_container(client, _kali_name(user.name))
            if not c:
                return json.dumps({"success": False, "msg": "No Pwn Machine running."})
            c.reload()
            created_at = float(c.labels.get("ctfd_kali_created") or time.time())
            elapsed = time.time() - created_at
            if elapsed >= KALI_MAX_LIFETIME_SECONDS:
                return json.dumps({
                    "success": False,
                    "msg": "70-minute session limit reached.",
                    "limit_reached": True,
                })
            new_exp = time.time() + 3600
            _expiry_overrides[f"{user.name}:kali"] = new_exp
            try:
                r = _redis_or_none()
                if r:
                    r.setex(f"ctfd:ext:{user.name}:kali", 7200, str(new_exp))
            except Exception:
                pass
            return json.dumps({"success": True, "msg": "Pwn Machine time set to 60 minutes."})
        except Exception as exc:
            return json.dumps({"success": False, "msg": str(exc)})

    @page_blueprint.route("/kali/start", methods=["POST"])
    @authed_only
    def start_kali():
        if not _bool_config("enable_kali", True):
            return json.dumps({"success": False, "msg": "Linux Pwn Machine is disabled by the admin."})

        user = current_user.get_current_user()
        if not user or not user.name:
            return json.dumps({"success": False, "msg": "Not authenticated."})
        blocked = _guard_launch(user, resource="kali")
        if blocked:
            return json.dumps({"success": False, "msg": blocked})

        team_id = _flag_owner_id(user)
        try:
            if _instance_manager_enabled():
                return json.dumps(_manager_start_kali(user))

            kali, expires_at = _start_kali_instance(user.name, team_id=team_id)

            kali_url = _container_url("kali", kali)
            return json.dumps({
                "success":    True,
                "status":     "running",
                "username":   user.name,
                "url":        kali_url,
                "expires_at": expires_at,
                "network":    _network_name(user.name),
                "msg":        "Linux Pwn Machine launched.",
            })
        except Exception as exc:
            import traceback

            traceback.print_exc()
            return json.dumps({"success": False, "msg": f"Error: {exc}"})
        finally:
            _release_launch_guard(user, resource="kali")

    @page_blueprint.route("/kali/status", methods=["GET"])
    @authed_only
    def kali_status():
        user = current_user.get_current_user()
        if not user or not user.name:
            return json.dumps({"success": False, "msg": "Not authenticated."})
        if _instance_manager_enabled():
            return json.dumps(_manager_kali_status(user))

        status = _container_status(user.name, "kali")
        # Attach session age so the UI can enforce the 70-min cap display
        try:
            client = _get_docker_client()
            c = _find_container(client, _kali_name(user.name))
            if c:
                c.reload()
                created_at = c.labels.get("ctfd_kali_created")
                if created_at:
                    status["created_at"] = int(float(created_at))
                    status["max_lifetime"] = KALI_MAX_LIFETIME_SECONDS
        except Exception:
            pass
        return json.dumps(status)

    @page_blueprint.route("/kali/sso-url", methods=["GET"])
    @authed_only
    def kali_sso_url():
        """
        Return the Kali URL pre-populated with the per-user VNC password so the
        browser can open it without the user typing anything.
        """
        user = current_user.get_current_user()
        if not user or not user.name:
            return json.dumps({"success": False, "msg": "Not authenticated."})

        status = _manager_kali_status(user) if _instance_manager_enabled() else _container_status(user.name, "kali")
        if status.get("status") != "running":
            return json.dumps({"success": False, "msg": "Linux Pwn Machine is not running."})

        kali_base_url = status.get("url", "")
        if not kali_base_url:
            return json.dumps({"success": False, "msg": "Linux Pwn Machine URL unavailable."})

        # Append ?autoauth= so noVNC auto-authenticates without the user typing the password
        vnc_pass = _kali_vnc_password(user.name)
        sso_url = kali_base_url.rstrip("/") + f"/?autoauth={vnc_pass}"

        return json.dumps({
            "success":  True,
            "url":      sso_url,
            "username": user.name,
        })

    @page_blueprint.route("/kali/stop", methods=["DELETE"])
    @authed_only
    def stop_kali():
        user = current_user.get_current_user()
        if not user or not user.name:
            return json.dumps({"success": False, "msg": "Not authenticated."})
        try:
            if _instance_manager_enabled():
                return json.dumps(_manager_stop_kali(user))

            client = _get_docker_client()
            _safe_remove_container(_find_container(client, _kali_name(user.name)))
            analytics = _find_container(client, _analytics_name(user.name))
            target = _find_container(client, _target_name(user.name))
            if not target and analytics:
                _safe_remove_container(analytics)
            _remove_network_if_unused(client, user.name)
            return json.dumps({"success": True, "msg": "Linux Pwn Machine stopped."})
        except Exception as exc:
            return json.dumps({"success": False, "msg": str(exc)})
        finally:
            _release_launch_guard(user, resource="kali")

    @page_blueprint.route("/admin/settings", methods=["GET"])
    @admins_only
    def admin_settings():
        settings = {
            "target_image": _get_config("target_image", DEFAULT_TARGET_IMAGE),
            "analytics_image": _get_config("analytics_image", DEFAULT_ANALYTICS_IMAGE),
            "kali_image": _get_config("kali_image", DEFAULT_KALI_IMAGE),
            "target_port": _get_config("target_port", DEFAULT_TARGET_PORT),
            "kali_port": _get_config("kali_port", DEFAULT_KALI_PORT),
            "target_port_range_start": _get_config("target_port_range_start", DEFAULT_TARGET_PORT_RANGE_START),
            "target_port_range_end": _get_config("target_port_range_end", DEFAULT_TARGET_PORT_RANGE_END),
            "kali_port_range_start": _get_config("kali_port_range_start", DEFAULT_KALI_PORT_RANGE_START),
            "kali_port_range_end": _get_config("kali_port_range_end", DEFAULT_KALI_PORT_RANGE_END),
            "host_ip": _get_host_ip(),
            "mem_limit": _get_config("mem_limit", DEFAULT_MEM_LIMIT),
            "cpu_quota": _get_config("cpu_quota", DEFAULT_CPU_QUOTA),
            "docker_api_url": _get_config("docker_api_url", "unix:///var/run/docker.sock"),
            "flag_secret": _get_config("flag_secret", ""),
            "target_mode": _get_config("target_mode", DEFAULT_TARGET_MODE),
            "enable_kali": _get_config("enable_kali", DEFAULT_ENABLE_KALI),
            "instance_lifetime": _get_config("instance_lifetime", DEFAULT_INSTANCE_LIFETIME),
            "kali_password": _get_config("kali_password", DEFAULT_KALI_PASSWORD),
            "lab_domain": _get_config("lab_domain", os.environ.get("LAB_DOMAIN", "lab")),
            "max_challenge_instances": _get_config("max_challenge_instances", DEFAULT_MAX_CHALLENGE_INSTANCES),
            "max_total_instances": _get_config("max_total_instances", DEFAULT_MAX_TOTAL_INSTANCES),
        }
        return render_plugin_template("target_settings.html", settings=settings, nav="settings")

    @page_blueprint.route("/admin/settings", methods=["POST"])
    @admins_only
    def admin_settings_save():
        saved_keys = [
            "target_image",
            "analytics_image",
            "kali_image",
            "target_port",
            "kali_port",
            "target_port_range_start",
            "target_port_range_end",
            "kali_port_range_start",
            "kali_port_range_end",
            "host_ip",
            "mem_limit",
            "cpu_quota",
            "docker_api_url",
            "flag_secret",
            "target_mode",
            "enable_kali",
            "instance_lifetime",
            "kali_password",
            "lab_domain",
            "max_challenge_instances",
            "max_total_instances",
        ]
        try:
            for key in saved_keys:
                if key in request.form:
                    set_config("ctfd_target:" + key, request.form.get(key, "").strip())
            return _json_response({"success": True, "msg": "Settings saved."})
        except Exception as exc:
            logger.exception("[Settings] save failed: %s", exc)
            return _json_response({"success": False, "msg": str(exc)}, status=500)

    @page_blueprint.route("/admin/challenge-targets", methods=["GET"])
    @admins_only
    def admin_challenge_targets_list():
        from CTFd.models import Challenges
        configs = {row.challenge_id: row.to_dict() for row in ChallengeTargetConfig.query.all()}
        challenges = []
        for chal in Challenges.query.order_by(Challenges.id.asc()).all():
            cfg = configs.get(chal.id) or {
                "challenge_id": chal.id,
                "enabled": False,
                "image_name": _get_config("target_image", DEFAULT_TARGET_IMAGE),
                "internal_port": int(_get_config("target_port", DEFAULT_TARGET_PORT) or 80),
                "needs_analytics": False,
                "mem_limit": "",
                "cpu_quota": "",
                "challenge_key": (CATALOG_BY_NAME.get(chal.name) or {}).get("flag_key", ""),
                "target_profile": (CATALOG_BY_NAME.get(chal.name) or {}).get("profile", ""),
                "health_path": (CATALOG_BY_NAME.get(chal.name) or {}).get("health_path", "/"),
            }
            cfg["name"] = chal.name
            cfg["category"] = chal.category
            cfg["value"] = chal.value
            challenges.append(cfg)
        return _json_response({"success": True, "challenges": challenges})

    @page_blueprint.route("/admin/challenge-targets", methods=["POST"])
    @admins_only
    def admin_challenge_targets_save():
        from CTFd.models import db, Challenges
        data = request.get_json(silent=True) or {}
        try:
            challenge_id = int(data.get("challenge_id"))
        except (TypeError, ValueError):
            return _json_response({"success": False, "msg": "challenge_id is required."}, status=400)
        if not Challenges.query.filter_by(id=challenge_id).first():
            return _json_response({"success": False, "msg": "Challenge not found."}, status=404)
        row = ChallengeTargetConfig.query.filter_by(challenge_id=challenge_id).first()
        if not row:
            row = ChallengeTargetConfig(challenge_id=challenge_id)
            db.session.add(row)
        row.enabled = bool(data.get("enabled", True))
        row.image_name = (data.get("image_name") or DEFAULT_TARGET_IMAGE).strip()
        try:
            row.internal_port = int(data.get("internal_port") or 80)
        except (TypeError, ValueError):
            row.internal_port = 80
        row.needs_analytics = bool(data.get("needs_analytics", False))
        row.mem_limit = (data.get("mem_limit") or "").strip()
        row.cpu_quota = str(data.get("cpu_quota") or "").strip()
        if "challenge_key" in data:
            row.challenge_key = (data.get("challenge_key") or "").strip()
        if "target_profile" in data:
            row.target_profile = (data.get("target_profile") or "").strip()
        if "health_path" in data:
            row.health_path = (data.get("health_path") or "/").strip() or "/"
        db.session.commit()
        return _json_response({"success": True, "config": row.to_dict()})

    @page_blueprint.route("/admin/dashboard", methods=["GET"])
    @admins_only
    def admin_dashboard():
        return render_plugin_template(
            "target_dashboard.html",
            containers=_list_managed_containers(),
            nav="instances",
        )

    def _list_managed_containers():
        containers = []
        try:
            client = _get_docker_client()
            managed = client.containers.list(all=True, filters={"label": "ctfd_target_user"})
            containers = [_collect_container_snapshot(container) for container in managed]
            containers.sort(key=lambda item: (item["username"], item["role"], item["name"]))
        except Exception:
            containers = []
        return containers

    _BACKUP_DIR = "/var/backups"
    _BACKUP_NAME_RE = re.compile(r"^ctfd-[A-Za-z0-9._-]+\.(sql|sql\.gz)$")

    def _audit(action, detail=""):
        try:
            user = current_user.get_current_user()
            name = user.name if user else "system"
        except Exception:
            name = "system"
        entry = {
            "ts": int(time.time()),
            "user": name,
            "action": action,
            "detail": str(detail or "")[:240],
        }
        try:
            os.makedirs(_BACKUP_DIR, exist_ok=True)
            with open(os.path.join(_BACKUP_DIR, "ops-audit.jsonl"), "a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry) + "\n")
        except Exception:
            logger.warning("[Ops] audit write failed")

    def _mysql_password():
        password = os.environ.get("MYSQL_PASSWORD") or ""
        if password:
            return password
        db_url = os.environ.get("DATABASE_URL") or ""
        try:
            return db_url.split("://", 1)[1].split("@", 1)[0].split(":", 1)[1]
        except Exception:
            return "ctfd"

    def _find_db_container(client=None):
        client = client or _get_docker_client()
        for container in client.containers.list():
            labels = container.labels or {}
            if labels.get("com.docker.compose.service") == "db":
                return container
        return None

    def _safe_backup_name(name):
        name = os.path.basename(name or "")
        if not _BACKUP_NAME_RE.match(name):
            return None
        path = os.path.join(_BACKUP_DIR, name)
        if not os.path.isfile(path):
            return None
        return name, path

    def _backup_kind(name):
        if "-auto-" in name:
            return "auto"
        if "-prerestore-" in name:
            return "safety"
        return "manual"

    def _list_backup_files(limit=16):
        files = []
        try:
            names = os.listdir(_BACKUP_DIR)
        except FileNotFoundError:
            return []
        for name in names:
            if not _BACKUP_NAME_RE.match(name):
                continue
            path = os.path.join(_BACKUP_DIR, name)
            try:
                files.append({
                    "file": name,
                    "bytes": os.path.getsize(path),
                    "mtime": int(os.path.getmtime(path)),
                    "kind": _backup_kind(name),
                })
            except OSError:
                pass
        files.sort(key=lambda item: item["mtime"], reverse=True)
        return files[:limit]

    def _prune_auto_backups(keep=16):
        autos = [row for row in _list_backup_files(limit=80) if row["kind"] == "auto"]
        for row in autos[keep:]:
            try:
                os.remove(os.path.join(_BACKUP_DIR, row["file"]))
            except OSError:
                pass

    def _create_ops_backup(reason="manual"):
        client = _get_docker_client()
        db_container = _find_db_container(client)
        if not db_container:
            return {"success": False, "msg": "Database container not found."}
        password = _mysql_password()
        result = db_container.exec_run(
            [
                "mariadb-dump",
                "-uctfd",
                "-p" + password,
                "--single-transaction",
                "--quick",
                "--routines",
                "ctfd",
            ],
            demux=True,
        )
        stdout, stderr = result.output if isinstance(result.output, tuple) else (result.output, b"")
        if result.exit_code != 0:
            err = (stderr or stdout or b"").decode("utf-8", errors="replace")[:300]
            return {"success": False, "msg": "Dump failed: " + err}
        if not stdout or len(stdout) < 1024 or b"CREATE TABLE" not in stdout:
            return {"success": False, "msg": "Dump was empty or incomplete."}
        stamp = time.strftime("%Y%m%d-%H%M%S")
        safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in (reason or "manual"))[:24] or "manual"
        os.makedirs(_BACKUP_DIR, exist_ok=True)
        name = "ctfd-" + safe + "-" + stamp + ".sql"
        path = os.path.join(_BACKUP_DIR, name)
        with open(path, "wb") as handle:
            handle.write(stdout or b"")
        set_config("ctfd_target:last_backup_ts", str(int(time.time())))
        set_config("ctfd_target:last_backup_file", name)
        return {"success": True, "file": name, "bytes": len(stdout or b"")}

    def _pipe_sql_to_db(container, sql_bytes):
        api = container.client.api
        exec_id = api.exec_create(
            container.id,
            ["mariadb", "-uctfd", "-p" + _mysql_password(), "ctfd"],
            stdin=True,
            stdout=True,
            stderr=True,
            tty=False,
        )
        sock = api.exec_start(exec_id, socket=True)
        raw = getattr(sock, "_sock", sock)
        try:
            raw.sendall(sql_bytes)
            try:
                raw.shutdown(socket.SHUT_WR)
            except Exception:
                pass
            while True:
                chunk = raw.recv(8192)
                if not chunk:
                    break
        finally:
            try:
                raw.close()
            except Exception:
                pass
        info = api.exec_inspect(exec_id)
        return int(info.get("ExitCode") or 0)

    def _read_backup_sql(path):
        with open(path, "rb") as handle:
            raw = handle.read()
        if path.endswith(".gz"):
            return gzip.decompress(raw)
        return raw

    def _auto_backup_enabled():
        prod = os.environ.get("PRODUCTION", "0") in ("1", "true", "True")
        return _bool_config("auto_backup", prod)

    def _config_ts(key):
        val = get_config(key)
        if not val:
            return 0
        try:
            return int(float(val))
        except (TypeError, ValueError):
            return 0

    def _schedule_tick():
        if not _bool_config("auto_schedule", False):
            return
        now = int(time.time())
        start = _config_ts("start")
        end = _config_ts("end")
        if start and now >= start:
            started = str(_get_config("auto_started_ts", "") or "")
            if started != str(start) and _ctfd_bool("paused"):
                _ctfd_set_bool("paused", False)
                set_config("ctfd_target:auto_started_ts", str(start))
                _audit("auto-start", str(start))
        if end and now >= end:
            ended = str(_get_config("auto_ended_ts", "") or "")
            if ended != str(end):
                _ctfd_set_bool("paused", True)
                _ctfd_set_bool("prevent_registration", True)
                set_config("freeze", str(now))
                set_config("ctfd_target:auto_ended_ts", str(end))
                try:
                    _create_ops_backup("auto-end")
                except Exception:
                    pass
                _audit("auto-end", str(end))

    _images_cache = {"ts": 0, "data": {}}

    def _images_ready():
        now = time.time()
        if now - _images_cache["ts"] < 60 and _images_cache["data"]:
            return _images_cache["data"]
        needed = ("vbank-ctf", "vbank-analytics")
        found = {name: False for name in needed}
        try:
            client = _get_docker_client()
            tags = set()
            for image in client.images.list():
                for tag in image.tags or []:
                    tags.add(tag)
                    tags.add(tag.split(":")[0])
            for name in needed:
                found[name] = name in tags or (name + ":latest") in tags
        except Exception:
            pass
        _images_cache["ts"] = now
        _images_cache["data"] = found
        return found

    def _auto_backup_tick():
        try:
            with app.app_context():
                _schedule_tick()
                if not _auto_backup_enabled():
                    return
                try:
                    interval = int(_get_config("auto_backup_seconds", "3600") or 3600)
                except (TypeError, ValueError):
                    interval = 3600
                last = 0.0
                try:
                    last = float(_get_config("last_backup_ts", "0") or 0)
                except (TypeError, ValueError):
                    last = 0.0
                if time.time() - last < max(300, interval):
                    return
                redis_client = _redis_or_none()
                if redis_client is not None:
                    if not redis_client.set("ctfd_target:auto_backup_lock", "1", nx=True, ex=180):
                        return
                result = _create_ops_backup("auto")
                if result.get("success"):
                    _audit("auto-backup", result.get("file", ""))
                    _prune_auto_backups()
                else:
                    logger.warning("[Ops] auto-backup failed: %s", result.get("msg"))
        except Exception as exc:
            logger.warning("[Ops] auto-backup tick failed: %s", exc)

    def _is_frozen():
        val = get_config("freeze")
        if not val:
            return False
        try:
            return int(val) > 0
        except (TypeError, ValueError):
            return str(val).strip().lower() in ("1", "true", "yes", "on")

    def _event_counts():
        users_count = 0
        challenges_total = 0
        challenges_visible = 0
        try:
            from CTFd.models import Users, Challenges
            users_count = (
                Users.query.filter_by(banned=False, hidden=False)
                .filter(Users.type != "admin")
                .count()
            )
            challenges_total = Challenges.query.count()
            challenges_visible = Challenges.query.filter_by(state="visible").count()
        except Exception:
            pass
        return users_count, challenges_total, challenges_visible

    def _join_parts():
        host_ip = _get_host_ip()
        if host_ip in ("localhost", "127.0.0.1", ""):
            return "", ""
        return host_ip, "http://" + host_ip + "/register"

    def _qr_svg(text, scale=6, border=3):
        if not text:
            return ""
        try:
            from .qr import svg_for_text
            return svg_for_text(text, scale=scale, border=border)
        except Exception as exc:
            logger.warning("[QR] encode failed: %s", exc)
            return ""

    def _schedule_label(ts):
        if not ts:
            return ""
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))

    def _event_phase(now=None):
        now = int(now or time.time())
        start = _config_ts("start")
        end = _config_ts("end")
        if _ctfd_bool("paused"):
            return "paused"
        if start and now < start:
            return "countdown"
        if end and now >= end:
            return "ended"
        return "live"

    def _board_token():
        return (_get_config("board_token", "") or "").strip()

    def _board_authorized():
        token = _board_token()
        if not token:
            return True
        got = (request.args.get("k") or request.args.get("token") or "").strip()
        return got == token

    def _board_url():
        host_ip, _join = _join_parts()
        token = _board_token()
        path = "/plugins/ctfd-target/board"
        if token:
            path += "?k=" + token
        if host_ip:
            return "http://" + host_ip + path
        return path

    def _safe_player_name(name):
        name = (name or "").strip()
        if not name or len(name) > 64:
            return ""
        if not re.match(r"^[A-Za-z0-9._-]+$", name):
            return ""
        return name

    def _public_standings(limit=10):
        rows = []
        try:
            from CTFd.utils.scores import get_standings
            standings = get_standings()
            for index, item in enumerate(standings[:limit], start=1):
                name = getattr(item, "name", None) or (item.get("name") if isinstance(item, dict) else "")
                score = getattr(item, "score", None)
                if score is None and isinstance(item, dict):
                    score = item.get("score", 0)
                rows.append({"rank": index, "name": name, "score": int(score or 0)})
        except Exception as exc:
            logger.warning("[Board] standings failed: %s", exc)
        return rows

    def _board_payload():
        now = int(time.time())
        users_count, _total, _visible = _event_counts()
        solves = 0
        solves_15m = 0
        try:
            from datetime import datetime, timedelta
            from CTFd.models import Solves
            solves = Solves.query.count()
            cutoff = datetime.utcnow() - timedelta(minutes=15)
            solves_15m = Solves.query.filter(Solves.date >= cutoff).count()
        except Exception:
            pass
        return {
            "success": True,
            "name": get_config("ctf_name") or "Lloyds CTF",
            "phase": _event_phase(now),
            "paused": _ctfd_bool("paused"),
            "frozen": _is_frozen(),
            "start": _config_ts("start"),
            "end": _config_ts("end"),
            "now": now,
            "message": (_get_config("maintenance_message", "") or "").strip()[:280],
            "players": users_count,
            "solves": solves,
            "solves_15m": solves_15m,
            "standings": _public_standings(10),
        }

    def _event_pulse():
        from datetime import datetime, timedelta
        from CTFd.models import Challenges, Solves, Fails, Users
        cutoff = datetime.utcnow() - timedelta(minutes=15)
        challenges = Challenges.query.order_by(Challenges.id.asc()).all()
        solve_rows = Solves.query.with_entities(
            Solves.challenge_id, Solves.user_id, Solves.date
        ).order_by(Solves.date.asc()).all()
        fail_rows = Fails.query.with_entities(Fails.challenge_id, Fails.date).all()
        names = {}
        try:
            for user in Users.query.with_entities(Users.id, Users.name).all():
                names[user.id] = user.name
        except Exception:
            pass
        first = {}
        solve_counts = {}
        recent_solves = 0
        for challenge_id, user_id, date in solve_rows:
            solve_counts[challenge_id] = solve_counts.get(challenge_id, 0) + 1
            if challenge_id not in first:
                first[challenge_id] = names.get(user_id, "")
            if date and date >= cutoff:
                recent_solves += 1
        fail_counts = {}
        recent_fails = 0
        for challenge_id, date in fail_rows:
            fail_counts[challenge_id] = fail_counts.get(challenge_id, 0) + 1
            if date and date >= cutoff:
                recent_fails += 1
        items = []
        unsolved = []
        for chal in challenges:
            if chal.state != "visible":
                continue
            solves = solve_counts.get(chal.id, 0)
            fails = fail_counts.get(chal.id, 0)
            row = {
                "id": chal.id,
                "name": chal.name,
                "solves": solves,
                "fails": fails,
                "first_blood": first.get(chal.id) or "",
            }
            items.append(row)
            if solves == 0:
                unsolved.append(chal.name)
        items.sort(key=lambda row: (row["solves"], -row["fails"], row["name"]))
        return {
            "success": True,
            "challenges": items,
            "unsolved": unsolved,
            "solves_15m": recent_solves,
            "fails_15m": recent_fails,
        }

    def _http_up(url, timeout=1.5):
        import urllib.error
        import urllib.request
        try:
            req = urllib.request.Request(url, method="GET", headers={"User-Agent": "ctfd-ops-probe"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return 100 <= int(getattr(resp, "status", 200) or 200) < 600
        except urllib.error.HTTPError:
            return True
        except Exception:
            return False

    def _probe_instances(limit=24):
        rows = []
        targets = [
            item for item in _list_managed_containers()
            if item.get("status") == "running" and item.get("role") in ("target", "challenge") and item.get("url")
        ]
        from concurrent.futures import ThreadPoolExecutor, as_completed
        sample = targets[:limit]
        if not sample:
            return {"success": True, "checked": 0, "up": 0, "down": 0, "instances": []}
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(_http_up, item["url"]): item for item in sample}
            for future in as_completed(futures):
                item = futures[future]
                try:
                    ok = bool(future.result())
                except Exception:
                    ok = False
                rows.append({
                    "username": item.get("username"),
                    "role": item.get("role"),
                    "url": item.get("url"),
                    "up": ok,
                })
        rows.sort(key=lambda row: (not row["up"], row.get("username") or ""))
        up = sum(1 for row in rows if row["up"])
        return {
            "success": True,
            "checked": len(rows),
            "up": up,
            "down": len(rows) - up,
            "instances": rows,
        }

    def _player_roster():
        from collections import defaultdict
        from CTFd.models import Users, Solves
        counts = defaultdict(int)
        try:
            for user_id, in Solves.query.with_entities(Solves.user_id).all():
                counts[user_id] += 1
        except Exception:
            pass
        players = []
        for user in Users.query.order_by(Users.id.asc()).all():
            if getattr(user, "type", "") == "admin":
                continue
            if user.banned or user.hidden:
                continue
            players.append({
                "id": user.id,
                "name": user.name,
                "email": getattr(user, "email", "") or "",
                "solves": counts.get(user.id, 0),
            })
        return players

    def _lookup_player(query):
        query = (query or "").strip()[:64]
        if not query:
            return None
        from CTFd.models import Users, Solves
        user = Users.query.filter(Users.name == query).first()
        if user is None:
            user = Users.query.filter(Users.name.ilike("%" + query.replace("%", "") + "%")).first()
        if user is None:
            return None
        containers = [
            item for item in _list_managed_containers()
            if item.get("username") == user.name
        ]
        solves = 0
        try:
            solves = Solves.query.filter_by(user_id=user.id).count()
        except Exception:
            pass
        return {
            "id": user.id,
            "name": user.name,
            "email": getattr(user, "email", "") or "",
            "banned": bool(user.banned),
            "hidden": bool(user.hidden),
            "solves": solves,
            "containers": containers,
        }

    def _stop_username_stack(username):
        username = _safe_player_name(username)
        if not username:
            return 0
        client = _get_docker_client()
        removed = 0
        for container in client.containers.list(all=True, filters={"label": "ctfd_target_user=" + username}):
            try:
                container.remove(force=True)
                removed += 1
            except Exception:
                pass
        _remove_network_if_unused(client, username)
        return removed

    def _extend_all_instances():
        extension = _instance_lifetime()
        client = _get_docker_client()
        updated = 0
        for container in client.containers.list(filters={"label": "ctfd_target_user", "status": "running"}):
            username = container.labels.get("ctfd_target_user") or ""
            role = container.labels.get("ctfd_target_role") or "target"
            challenge_id = container.labels.get("ctfd_target_challenge") or ""
            expiry_role = ("%s:%s" % (role, challenge_id)) if challenge_id else role
            old_exp = _effective_expiry(username, expiry_role, container.labels.get("ctfd_target_expires"))
            new_exp = max(old_exp or time.time(), time.time()) + extension
            _expiry_overrides["%s:%s" % (username, expiry_role)] = new_exp
            try:
                redis_client = _redis_or_none()
                if redis_client is not None:
                    redis_client.setex(
                        "ctfd:ext:%s:%s" % (username, expiry_role),
                        int(extension * 2),
                        str(new_exp),
                    )
            except Exception:
                pass
            updated += 1
        return updated, extension

    def _preflight():
        overview_checks = []
        host_ip, join_url = _join_parts()
        dns_ok = False
        proxy_ok = False
        try:
            client = _get_docker_client()
            for item in client.containers.list():
                name = item.name or ""
                svc = (item.labels or {}).get("com.docker.compose.service") or ""
                if ("dnsmasq" in name or svc == "dnsmasq") and item.status == "running":
                    dns_ok = True
                if svc == "proxy" and item.status == "running":
                    proxy_ok = True
        except Exception:
            pass
        db_ok = False
        try:
            from CTFd.models import Users
            Users.query.limit(1).count()
            db_ok = True
        except Exception:
            db_ok = False
        cache_ok = False
        try:
            redis_client = _redis_or_none()
            cache_ok = bool(redis_client and redis_client.ping())
        except Exception:
            cache_ok = False
        disk_gb = _disk_free_gb()
        images = _images_ready()
        users_count, challenges_total, challenges_visible = _event_counts()
        checks = [
            {"id": "host_ip", "ok": bool(host_ip), "label": "Host IP set"},
            {"id": "join", "ok": bool(join_url), "label": "Join URL ready"},
            {"id": "dns", "ok": dns_ok, "label": "DNS running"},
            {"id": "proxy", "ok": proxy_ok, "label": "Proxy running"},
            {"id": "db", "ok": db_ok, "label": "Database query"},
            {"id": "cache", "ok": cache_ok, "label": "Redis ping"},
            {"id": "images", "ok": all(images.values()) if images else False, "label": "Images present"},
            {"id": "challenges", "ok": challenges_visible > 0, "label": "Challenges visible"},
            {"id": "disk", "ok": disk_gb is None or disk_gb >= 5, "label": "Disk space"},
            {"id": "secrets", "ok": _secrets_ok(), "label": "Secrets not defaults"},
        ]
        probe = _probe_instances(limit=8)
        if probe.get("checked"):
            checks.append({
                "id": "targets",
                "ok": probe.get("down", 0) == 0,
                "label": "Targets responding (%s/%s)" % (probe.get("up", 0), probe.get("checked", 0)),
            })
        fails = [item["label"] for item in checks if not item["ok"]]
        return {
            "success": len(fails) == 0,
            "ready": len(fails) == 0,
            "checks": checks,
            "fails": fails,
            "join_url": join_url,
            "users": users_count,
            "challenges_visible": challenges_visible,
            "challenges_total": challenges_total,
            "probe": probe,
            "overview_checks": overview_checks,
        }

    @page_blueprint.route("/admin/overview", methods=["GET"])
    @admins_only
    def admin_overview():
        containers = _list_managed_containers()
        running = [c for c in containers if c.get("status") == "running"]
        players = {c.get("username") for c in running if c.get("username")}
        dns_running = False
        try:
            client = _get_docker_client()
            for item in client.containers.list():
                if "dnsmasq" in (item.name or "") and item.status == "running":
                    dns_running = True
                    break
        except Exception:
            pass
        host_ip = _get_host_ip()
        if host_ip in ("localhost",):
            host_ip = ""
        services = {}
        try:
            client = _get_docker_client()
            wanted = ("ctfd", "db", "cache", "proxy", "dnsmasq")
            for item in client.containers.list(all=True):
                svc = (item.labels or {}).get("com.docker.compose.service")
                if svc in wanted:
                    services[svc] = item.status
        except Exception:
            pass
        paused = _ctfd_bool("paused")
        prevent_registration = _ctfd_bool("prevent_registration")
        users_count, challenges_total, challenges_visible = _event_counts()
        last_backup_file = _get_config("last_backup_file", "") or ""
        backups = _list_backup_files(1)
        if not last_backup_file and backups:
            last_backup_file = backups[0]["file"]
        images = _images_ready()
        start_ts = _config_ts("start")
        end_ts = _config_ts("end")
        join_url = ("http://" + host_ip + "/register") if host_ip else ""
        disk_gb = _disk_free_gb()
        running_count = len(running)
        cap = _max_total_instances()
        secrets_ok = _secrets_ok()
        launches_locked = _bool_config("launches_locked", False)
        production = os.environ.get("PRODUCTION", "0") in ("1", "true", "True")
        checklist = [
            {"id": "host_ip", "ok": bool(host_ip), "label": "Host IP set"},
            {"id": "dns", "ok": dns_running, "label": "DNS running"},
            {"id": "db", "ok": services.get("db") == "running", "label": "Database up"},
            {"id": "cache", "ok": services.get("cache") == "running", "label": "Cache up"},
            {"id": "images", "ok": all(images.values()), "label": "Challenge images ready"},
            {"id": "secrets", "ok": secrets_ok, "label": "Secrets not defaults"},
            {"id": "disk", "ok": disk_gb is None or disk_gb >= 5, "label": "Disk space"},
            {"id": "paused", "ok": not paused, "label": "CTF not paused"},
            {"id": "registration", "ok": not prevent_registration, "label": "Registration open"},
            {"id": "challenges", "ok": challenges_visible > 0, "label": "Challenges visible"},
            {"id": "backup", "ok": bool(last_backup_file), "label": "Backup exists"},
            {"id": "capacity", "ok": running_count < cap, "label": "Instance headroom"},
        ]
        return _json_response({
            "success": True,
            "instances": running_count,
            "instance_cap": cap,
            "players": len(players),
            "users": users_count,
            "challenges_total": challenges_total,
            "challenges_visible": challenges_visible,
            "host_ip": host_ip,
            "join_url": join_url,
            "dns_running": dns_running,
            "mode": _get_config("target_mode", DEFAULT_TARGET_MODE),
            "paused": paused,
            "prevent_registration": prevent_registration,
            "frozen": _is_frozen(),
            "start": start_ts,
            "end": end_ts,
            "auto_schedule": _bool_config("auto_schedule", False),
            "notes": (_get_config("run_notes", "") or "")[:2000],
            "maintenance_message": (_get_config("maintenance_message", "") or "").strip(),
            "auto_backup": _auto_backup_enabled(),
            "last_backup": last_backup_file,
            "last_backup_ts": _get_config("last_backup_ts", "") or "",
            "production": production,
            "workers": os.environ.get("WORKERS", "1"),
            "secrets_ok": secrets_ok,
            "disk_gb": disk_gb,
            "launches_locked": launches_locked,
            "board_url": _board_url(),
            "board_private": bool(_board_token()),
            "join_password": bool((get_config("ctf_password") or "").strip()),
            "services": services,
            "images": images,
            "checklist": checklist,
            "ready": all(item["ok"] for item in checklist),
        })

    @page_blueprint.route("/admin/instances.json", methods=["GET"])
    @admins_only
    def admin_instances_json():
        return _json_response({"success": True, "containers": _list_managed_containers()})

    def _manager_architecture_snapshot():
        if not _instance_manager_enabled():
            return {
                "success": False,
                "msg": "Instance Manager is not enabled.",
                "nodes": [],
                "instances": [],
                "totals": {"nodes": 0, "containers": 0, "target_containers": 0, "kali_containers": 0, "capacity": 0},
            }
        return _instance_manager_request("GET", "/architecture")

    @page_blueprint.route("/admin/architecture", methods=["GET"])
    @admins_only
    def admin_architecture():
        try:
            snapshot = _manager_architecture_snapshot()
        except Exception as exc:
            snapshot = {
                "success": False,
                "msg": str(exc),
                "nodes": [],
                "instances": [],
                "totals": {"nodes": 0, "containers": 0, "target_containers": 0, "kali_containers": 0, "capacity": 0},
            }
        return render_plugin_template("architecture.html", snapshot=snapshot, nav="architecture")

    @page_blueprint.route("/admin/architecture.json", methods=["GET"])
    @admins_only
    def admin_architecture_json():
        try:
            return _json_response(_manager_architecture_snapshot())
        except Exception as exc:
            return _json_response({"success": False, "msg": str(exc), "nodes": [], "instances": [], "totals": {}}, status=502)

    @page_blueprint.route("/admin/ops", methods=["GET"])
    @admins_only
    def admin_ops():
        return render_plugin_template("ops.html", nav="ops")

    @page_blueprint.route("/admin/ops/pause", methods=["POST"])
    @admins_only
    def admin_ops_pause():
        data = request.get_json(silent=True) or {}
        paused = bool(data.get("paused"))
        _ctfd_set_bool("paused", paused)
        _audit("pause" if paused else "resume")
        return _json_response({"success": True, "paused": paused})

    @page_blueprint.route("/admin/ops/registration", methods=["POST"])
    @admins_only
    def admin_ops_registration():
        data = request.get_json(silent=True) or {}
        prevent = bool(data.get("prevent_registration"))
        _ctfd_set_bool("prevent_registration", prevent)
        _audit("registration-close" if prevent else "registration-open")
        return _json_response({"success": True, "prevent_registration": prevent})

    @page_blueprint.route("/admin/ops/stop-all", methods=["POST"])
    @admins_only
    def admin_ops_stop_all():
        removed = 0
        users = set()
        try:
            if _instance_manager_enabled():
                try:
                    resp = _instance_manager_request("POST", "/instances/stop-all")
                    removed += int(resp.get("stopped", 0))
                except Exception as exc:
                    logger.warning("[AdminOps] instance-manager stop-all error: %s", exc)
            client = _get_docker_client()
            for container in client.containers.list(all=True, filters={"label": "ctfd_target_user"}):
                users.add(container.labels.get("ctfd_target_user") or "")
                try:
                    container.remove(force=True)
                    removed += 1
                except Exception:
                    pass
            for username in users:
                if username:
                    _remove_network_if_unused(client, username)
        except Exception as exc:
            return _json_response({"success": False, "msg": str(exc)}, status=500)
        _audit("stop-all", str(removed))
        return _json_response({"success": True, "stopped": removed})

    @page_blueprint.route("/admin/ops/backup", methods=["POST"])
    @admins_only
    def admin_ops_backup():
        try:
            result = _create_ops_backup("manual")
            if result.get("success"):
                _audit("backup", result.get("file", ""))
            return _json_response(result, status=200 if result.get("success") else 500)
        except Exception as exc:
            logger.exception("[Ops] backup failed")
            return _json_response({"success": False, "msg": str(exc)}, status=500)

    @page_blueprint.route("/admin/ops/backups", methods=["GET"])
    @admins_only
    def admin_ops_backups():
        return _json_response({
            "success": True,
            "backups": _list_backup_files(),
            "auto_backup": _auto_backup_enabled(),
        })

    @page_blueprint.route("/admin/ops/backups/<filename>", methods=["GET"])
    @admins_only
    def admin_ops_backup_download(filename):
        from flask import send_from_directory
        parsed = _safe_backup_name(filename)
        if not parsed:
            return _json_response({"success": False, "msg": "Backup not found."}, status=404)
        name, _path = parsed
        return send_from_directory(_BACKUP_DIR, name, as_attachment=True)

    @page_blueprint.route("/admin/ops/restore", methods=["POST"])
    @admins_only
    def admin_ops_restore():
        data = request.get_json(silent=True) or {}
        if str(data.get("confirm") or "").strip().upper() != "RESTORE":
            return _json_response({"success": False, "msg": "Type RESTORE to confirm."}, status=400)
        parsed = _safe_backup_name(data.get("file"))
        if not parsed:
            return _json_response({"success": False, "msg": "Backup not found."}, status=404)
        name, path = parsed
        try:
            set_config("ctfd_target:restore_lock", "1")
            safety = _create_ops_backup("prerestore")
            db_container = _find_db_container()
            if not db_container:
                return _json_response({"success": False, "msg": "Database container not found."}, status=500)
            sql_bytes = _read_backup_sql(path)
            if not sql_bytes:
                return _json_response({"success": False, "msg": "Backup file is empty."}, status=400)
            code = _pipe_sql_to_db(db_container, sql_bytes)
            if code != 0:
                return _json_response({"success": False, "msg": "Restore failed (exit %s)." % code}, status=500)
            _audit("restore", name)
            return _json_response({
                "success": True,
                "file": name,
                "safety": safety.get("file"),
            })
        except Exception as exc:
            logger.exception("[Ops] restore failed")
            return _json_response({"success": False, "msg": str(exc)}, status=500)
        finally:
            set_config("ctfd_target:restore_lock", "0")

    @page_blueprint.route("/admin/ops/auto-backup", methods=["POST"])
    @admins_only
    def admin_ops_auto_backup():
        data = request.get_json(silent=True) or {}
        enabled = bool(data.get("enabled"))
        set_config("ctfd_target:auto_backup", "1" if enabled else "0")
        _audit("auto-backup", "on" if enabled else "off")
        return _json_response({"success": True, "auto_backup": enabled})

    @page_blueprint.route("/admin/ops/maintenance", methods=["POST"])
    @admins_only
    def admin_ops_maintenance():
        data = request.get_json(silent=True) or {}
        message = str(data.get("message") or "").strip()[:280]
        set_config("ctfd_target:maintenance_message", message)
        _audit("maintenance", message or "(cleared)")
        return _json_response({"success": True, "message": message})

    @page_blueprint.route("/admin/ops/freeze", methods=["POST"])
    @admins_only
    def admin_ops_freeze():
        data = request.get_json(silent=True) or {}
        frozen = bool(data.get("frozen"))
        set_config("freeze", str(int(time.time())) if frozen else "")
        _audit("freeze" if frozen else "unfreeze")
        return _json_response({"success": True, "frozen": frozen})

    @page_blueprint.route("/admin/ops/go-live", methods=["POST"])
    @admins_only
    def admin_ops_go_live():
        backup = _create_ops_backup("go-live")
        _ctfd_set_bool("paused", False)
        set_config("freeze", "")
        set_config("ctfd_target:launches_locked", "0")
        set_config("ctfd_target:restore_lock", "0")
        _audit("go-live", backup.get("file", ""))
        return _json_response({
            "success": True,
            "paused": False,
            "frozen": False,
            "backup": backup.get("file") if backup.get("success") else None,
        })

    @page_blueprint.route("/admin/ops/lock-launches", methods=["POST"])
    @admins_only
    def admin_ops_lock_launches():
        data = request.get_json(silent=True) or {}
        locked = bool(data.get("locked"))
        set_config("ctfd_target:launches_locked", "1" if locked else "0")
        _audit("lock-launches" if locked else "unlock-launches")
        return _json_response({"success": True, "launches_locked": locked})

    @page_blueprint.route("/admin/ops/end-event", methods=["POST"])
    @admins_only
    def admin_ops_end_event():
        data = request.get_json(silent=True) or {}
        _ctfd_set_bool("paused", True)
        _ctfd_set_bool("prevent_registration", True)
        set_config("freeze", str(int(time.time())))
        set_config("ctfd_target:launches_locked", "1")
        backup = _create_ops_backup("event-end")
        stopped = 0
        if data.get("stop_instances"):
            try:
                client = _get_docker_client()
                users = set()
                for container in client.containers.list(all=True, filters={"label": "ctfd_target_user"}):
                    users.add(container.labels.get("ctfd_target_user") or "")
                    try:
                        container.remove(force=True)
                        stopped += 1
                    except Exception:
                        pass
                for username in users:
                    if username:
                        _remove_network_if_unused(client, username)
            except Exception as exc:
                logger.warning("[Ops] end-event stop failed: %s", exc)
        _audit("end-event", "stopped=%s backup=%s" % (stopped, backup.get("file", "")))
        return _json_response({
            "success": True,
            "backup": backup.get("file"),
            "stopped": stopped,
        })

    @page_blueprint.route("/admin/ops/schedule", methods=["POST"])
    @admins_only
    def admin_ops_schedule():
        data = request.get_json(silent=True) or {}
        start = data.get("start") or 0
        end = data.get("end") or 0
        try:
            start = int(start or 0)
            end = int(end or 0)
        except (TypeError, ValueError):
            return _json_response({"success": False, "msg": "Invalid schedule."}, status=400)
        if start and end and end <= start:
            return _json_response({"success": False, "msg": "End must be after start."}, status=400)
        set_config("start", str(start) if start else "")
        set_config("end", str(end) if end else "")
        if "auto_schedule" in data:
            set_config("ctfd_target:auto_schedule", "1" if data.get("auto_schedule") else "0")
        _audit("schedule", "start=%s end=%s" % (start or "-", end or "-"))
        return _json_response({"success": True, "start": start, "end": end})

    @page_blueprint.route("/admin/ops/notes", methods=["POST"])
    @admins_only
    def admin_ops_notes():
        data = request.get_json(silent=True) or {}
        notes = str(data.get("notes") or "")[:2000]
        set_config("ctfd_target:run_notes", notes)
        _audit("notes", "updated")
        return _json_response({"success": True})

    @page_blueprint.route("/admin/ops/logs.txt", methods=["GET"])
    @admins_only
    def admin_ops_logs():
        raw = b"No CTFd container logs found.\n"
        try:
            client = _get_docker_client()
            for container in client.containers.list(all=True):
                if (container.labels or {}).get("com.docker.compose.service") == "ctfd":
                    raw = container.logs(tail=400, timestamps=True) or raw
                    break
        except Exception as exc:
            raw = ("Could not read logs: %s\n" % exc).encode("utf-8")
        return Response(
            raw,
            mimetype="text/plain; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=ctfd-logs.txt"},
        )

    @page_blueprint.route("/admin/ops/briefing", methods=["GET"])
    @admins_only
    def admin_ops_briefing():
        host_ip, join_url = _join_parts()
        start_ts = _config_ts("start")
        end_ts = _config_ts("end")
        return render_plugin_template(
            "briefing.html",
            ctf_name=get_config("ctf_name") or "Lloyds CTF",
            host_ip=host_ip,
            join_url=join_url,
            start_label=_schedule_label(start_ts),
            end_label=_schedule_label(end_ts),
            notes=(_get_config("run_notes", "") or "").strip(),
            qr_svg=_qr_svg(join_url),
        )

    @page_blueprint.route("/admin/ops/briefing.txt", methods=["GET"])
    @admins_only
    def admin_ops_briefing_txt():
        host_ip, join_url = _join_parts()
        start_ts = _config_ts("start")
        end_ts = _config_ts("end")
        lines = [
            get_config("ctf_name") or "Lloyds CTF",
            "Join: " + (join_url or "(set host IP)"),
            "Host: " + (host_ip or "(not set)"),
        ]
        if start_ts:
            lines.append("Start: " + _schedule_label(start_ts))
        if end_ts:
            lines.append("End: " + _schedule_label(end_ts))
        notes = (_get_config("run_notes", "") or "").strip()
        if notes:
            lines.extend(["", "Notes:", notes])
        body = "\n".join(lines) + "\n"
        return Response(
            body,
            mimetype="text/plain; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=player-briefing.txt"},
        )

    @page_blueprint.route("/admin/ops/pack", methods=["GET"])
    @admins_only
    def admin_ops_pack():
        host_ip, join_url = _join_parts()
        return render_plugin_template(
            "pack.html",
            ctf_name=get_config("ctf_name") or "Lloyds CTF",
            join_url=join_url,
            start_label=_schedule_label(_config_ts("start")),
            end_label=_schedule_label(_config_ts("end")),
            notes=(_get_config("run_notes", "") or "").strip(),
            join_password=(get_config("ctf_password") or "").strip(),
            players=_player_roster(),
            qr_svg=_qr_svg(join_url),
        )

    @page_blueprint.route("/board", methods=["GET"])
    def public_board():
        if not _board_authorized():
            return Response("Board token required.", status=403, mimetype="text/plain")
        return render_plugin_template(
            "board.html",
            ctf_name=get_config("ctf_name") or "Lloyds CTF",
        )

    @page_blueprint.route("/board.json", methods=["GET"])
    def public_board_json():
        if not _board_authorized():
            return _json_response({"success": False, "msg": "Board token required."}, status=403)
        return _json_response(_board_payload())

    @page_blueprint.route("/admin/ops/pulse", methods=["GET"])
    @admins_only
    def admin_ops_pulse():
        try:
            return _json_response(_event_pulse())
        except Exception as exc:
            logger.exception("[Ops] pulse failed")
            return _json_response({"success": False, "msg": str(exc)}, status=500)

    @page_blueprint.route("/admin/ops/probe", methods=["POST"])
    @admins_only
    def admin_ops_probe():
        try:
            result = _probe_instances()
            _audit("probe", "%s up / %s down" % (result.get("up"), result.get("down")))
            return _json_response(result)
        except Exception as exc:
            logger.exception("[Ops] probe failed")
            return _json_response({"success": False, "msg": str(exc)}, status=500)

    @page_blueprint.route("/admin/ops/preflight", methods=["POST"])
    @admins_only
    def admin_ops_preflight():
        try:
            result = _preflight()
            _audit("preflight", "ready" if result.get("ready") else ",".join(result.get("fails") or []))
            return _json_response(result)
        except Exception as exc:
            logger.exception("[Ops] preflight failed")
            return _json_response({"success": False, "msg": str(exc)}, status=500)

    @page_blueprint.route("/admin/ops/player", methods=["GET"])
    @admins_only
    def admin_ops_player():
        found = _lookup_player(request.args.get("q") or "")
        if not found:
            return _json_response({"success": False, "msg": "Player not found."}, status=404)
        return _json_response({"success": True, "player": found})

    @page_blueprint.route("/admin/ops/stop-user", methods=["POST"])
    @admins_only
    def admin_ops_stop_user():
        data = request.get_json(silent=True) or {}
        username = _safe_player_name(data.get("username") or data.get("name"))
        if not username:
            return _json_response({"success": False, "msg": "Enter a player username."}, status=400)
        try:
            stopped = _stop_username_stack(username)
        except Exception as exc:
            return _json_response({"success": False, "msg": str(exc)}, status=500)
        _audit("stop-user", "%s:%s" % (username, stopped))
        return _json_response({"success": True, "username": username, "stopped": stopped})

    @page_blueprint.route("/admin/ops/extend-all", methods=["POST"])
    @admins_only
    def admin_ops_extend_all():
        try:
            updated, extension = _extend_all_instances()
        except Exception as exc:
            return _json_response({"success": False, "msg": str(exc)}, status=500)
        _audit("extend-all", str(updated))
        return _json_response({
            "success": True,
            "updated": updated,
            "extension_seconds": extension,
        })

    @page_blueprint.route("/admin/ops/join-password", methods=["POST"])
    @admins_only
    def admin_ops_join_password():
        data = request.get_json(silent=True) or {}
        password = str(data.get("password") or "").strip()[:64]
        set_config("ctf_password", password)
        _audit("join-password", "on" if password else "off")
        return _json_response({"success": True, "enabled": bool(password)})

    @page_blueprint.route("/admin/ops/board-token", methods=["POST"])
    @admins_only
    def admin_ops_board_token():
        import secrets
        data = request.get_json(silent=True) or {}
        if data.get("enabled"):
            token = secrets.token_urlsafe(10)
            set_config("ctfd_target:board_token", token)
            _audit("board-token", "private")
        else:
            token = ""
            set_config("ctfd_target:board_token", "")
            _audit("board-token", "public")
        return _json_response({
            "success": True,
            "private": bool(token),
            "board_url": _board_url(),
        })

    @page_blueprint.route("/admin/ops/backups/delete", methods=["POST"])
    @admins_only
    def admin_ops_backup_delete():
        data = request.get_json(silent=True) or {}
        parsed = _safe_backup_name(data.get("file"))
        if not parsed:
            return _json_response({"success": False, "msg": "Backup not found."}, status=404)
        name, path = parsed
        try:
            os.remove(path)
        except OSError as exc:
            return _json_response({"success": False, "msg": str(exc)}, status=500)
        _audit("backup-delete", name)
        return _json_response({"success": True, "file": name})

    @page_blueprint.route("/admin/ops/warm-images", methods=["POST"])
    @admins_only
    def admin_ops_warm_images():
        names = ["vbank-ctf", "vbank-analytics", _get_config("kali_image", DEFAULT_KALI_IMAGE_NAME)]
        ready, missing = [], []
        try:
            client = _get_docker_client()
            for name in names:
                if not name:
                    continue
                try:
                    client.images.get(name)
                    ready.append(name)
                except Exception:
                    missing.append(name)
                    continue
                try:
                    container = client.containers.create(image=name, labels={"ctfd_warmup": "1"})
                    container.remove(force=True)
                except Exception as exc:
                    logger.warning("[Ops] warmup %s: %s", name, exc)
            _images_cache["ts"] = 0
            _audit("warm-images", ",".join(ready) or "none")
            ok = len(missing) == 0
            return _json_response({
                "success": ok,
                "ready": ready,
                "missing": missing,
                "msg": ("Missing images: " + ", ".join(missing)) if missing else "Images ready.",
            })
        except Exception as exc:
            return _json_response({"success": False, "msg": str(exc)}, status=500)

    @page_blueprint.route("/admin/ops/challenges", methods=["POST"])
    @admins_only
    def admin_ops_challenges():
        data = request.get_json(silent=True) or {}
        state = str(data.get("state") or "").strip().lower()
        if state not in ("visible", "hidden"):
            return _json_response({"success": False, "msg": "state must be visible or hidden."}, status=400)
        try:
            from CTFd.models import Challenges, db
            updated = 0
            for challenge in Challenges.query.all():
                if challenge.state != state:
                    challenge.state = state
                    updated += 1
            db.session.commit()
            _audit("challenges-" + state, str(updated))
            return _json_response({"success": True, "state": state, "updated": updated})
        except Exception as exc:
            return _json_response({"success": False, "msg": str(exc)}, status=500)

    @page_blueprint.route("/admin/ops/dns-test", methods=["POST"])
    @admins_only
    def admin_ops_dns_test():
        host_ip = _get_host_ip()
        dns_ok = False
        proxy_ok = False
        try:
            client = _get_docker_client()
            for item in client.containers.list():
                name = item.name or ""
                svc = (item.labels or {}).get("com.docker.compose.service") or ""
                if ("dnsmasq" in name or svc == "dnsmasq") and item.status == "running":
                    dns_ok = True
                if svc == "proxy" and item.status == "running":
                    proxy_ok = True
        except Exception:
            pass
        tcp_ok = False
        try:
            sock = socket.create_connection(("proxy", 80), timeout=2)
            sock.close()
            tcp_ok = True
        except Exception:
            tcp_ok = False
        return _json_response({
            "success": True,
            "host_ip": host_ip if host_ip not in ("localhost",) else "",
            "dns": dns_ok,
            "proxy": proxy_ok,
            "proxy_tcp": tcp_ok,
        })

    @page_blueprint.route("/admin/ops/restart-dns", methods=["POST"])
    @admins_only
    def admin_ops_restart_dns():
        host_ip = _get_host_ip()
        if not host_ip or host_ip in ("localhost", "127.0.0.1"):
            return _json_response({"success": False, "msg": "Set a LAN host IP first."}, status=400)
        lab_domain = _get_config("lab_domain", os.environ.get("LAB_DOMAIN", "lab"))
        _restart_dnsmasq_async(host_ip, lab_domain)
        _audit("restart-dns", host_ip)
        return _json_response({"success": True, "msg": "DNS restart started."})

    @page_blueprint.route("/admin/ops/audit", methods=["GET"])
    @admins_only
    def admin_ops_audit():
        path = os.path.join(_BACKUP_DIR, "ops-audit.jsonl")
        rows = []
        try:
            with open(path, "r", encoding="utf-8") as handle:
                lines = handle.readlines()[-24:]
            for line in reversed(lines):
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass
        except FileNotFoundError:
            rows = []
        return _json_response({"success": True, "events": rows})

    @page_blueprint.route("/admin/ops/audit.jsonl", methods=["GET"])
    @admins_only
    def admin_ops_audit_download():
        from flask import send_from_directory
        path = os.path.join(_BACKUP_DIR, "ops-audit.jsonl")
        if not os.path.isfile(path):
            return Response("", mimetype="application/x-ndjson", headers={
                "Content-Disposition": "attachment; filename=ops-audit.jsonl",
            })
        return send_from_directory(_BACKUP_DIR, "ops-audit.jsonl", as_attachment=True)

    @page_blueprint.route("/admin/ops/players.csv", methods=["GET"])
    @admins_only
    def admin_ops_players_csv():
        import csv
        import io
        from collections import defaultdict
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["id", "name", "email", "score", "solves", "banned", "hidden"])
        try:
            from CTFd.models import Users, Solves
            from CTFd.utils.scores import get_standings
            scores = {}
            try:
                for item in get_standings(admin=True):
                    name = getattr(item, "name", None) or (item.get("name") if isinstance(item, dict) else "")
                    score = getattr(item, "score", None)
                    if score is None and isinstance(item, dict):
                        score = item.get("score", 0)
                    if name:
                        scores[name] = score
            except Exception:
                scores = {}
            solve_counts = defaultdict(int)
            for user_id, in Solves.query.with_entities(Solves.user_id).all():
                solve_counts[user_id] += 1
            for user in Users.query.order_by(Users.id.asc()).all():
                if getattr(user, "type", "") == "admin":
                    continue
                writer.writerow([
                    user.id,
                    user.name,
                    getattr(user, "email", "") or "",
                    scores.get(user.name, 0),
                    solve_counts.get(user.id, 0),
                    int(bool(user.banned)),
                    int(bool(user.hidden)),
                ])
        except Exception as exc:
            logger.warning("[Ops] players export failed: %s", exc)
        return Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=players.csv"},
        )

    @page_blueprint.route("/health", methods=["GET"])
    def public_health():
        disk = _disk_free_gb()
        paused = _ctfd_bool("paused")
        ok = (disk is None or disk >= 2) and not _bool_config("restore_lock", False)
        return _json_response({
            "ok": ok,
            "paused": paused,
            "production": os.environ.get("PRODUCTION", "0") in ("1", "true", "True"),
        })

    @page_blueprint.route("/banner", methods=["GET"])
    def public_banner():
        msg = (_get_config("maintenance_message", "") or "").strip()[:280]
        start = _config_ts("start")
        end = _config_ts("end")
        now = int(time.time())
        phase = "live"
        if _ctfd_bool("paused"):
            phase = "paused"
        elif start and now < start:
            phase = "countdown"
        elif end and now >= end:
            phase = "ended"
        return _json_response({
            "success": True,
            "message": msg,
            "paused": _ctfd_bool("paused"),
            "start": start,
            "end": end,
            "now": now,
            "phase": phase,
        })

    @page_blueprint.route("/admin/ops/scoreboard.csv", methods=["GET"])
    @admins_only
    def admin_ops_scoreboard_csv():
        import csv
        import io
        rows = []
        try:
            from CTFd.utils.scores import get_standings
            standings = get_standings()
            for item in standings:
                name = getattr(item, "name", None) or (item.get("name") if isinstance(item, dict) else "")
                score = getattr(item, "score", None)
                if score is None and isinstance(item, dict):
                    score = item.get("score", 0)
                account_id = getattr(item, "account_id", None) or (item.get("account_id") if isinstance(item, dict) else "")
                rows.append((account_id, name, score))
        except Exception as exc:
            logger.warning("[Ops] scoreboard export fallback: %s", exc)
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["id", "name", "score"])
        writer.writerows(rows)
        return Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=scoreboard.csv"},
        )

    @page_blueprint.route("/admin/hub", methods=["GET"])
    @admins_only
    def admin_hub():
        return render_plugin_template("hub.html", nav="hub")

    # â”€â”€ Network & DNS Setup â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def _restart_dnsmasq_with_ip(host_ip, lab_domain="lab"):
        """Stop the running dnsmasq container and launch a fresh one with the new IP."""
        client = _get_docker_client()
        dns_container = None
        for c in client.containers.list(all=True):
            if "dnsmasq" in c.name:
                dns_container = c
                break
        if not dns_container:
            raise RuntimeError("dnsmasq container not found")

        image         = dns_container.attrs["Config"]["Image"]
        container_name = dns_container.name
        networks      = list(dns_container.attrs["NetworkSettings"]["Networks"].keys())
        cap_add       = (dns_container.attrs.get("HostConfig") or {}).get("CapAdd") or ["NET_ADMIN"]
        labels        = dns_container.labels or {}

        # Match docker-compose: listen on all interfaces. Binding to a specific
        # host_ip fails on a new machine when that IP is not present locally.
        new_cmd = [
            "--no-daemon",
            f"--address=/.{lab_domain}/{host_ip}",
            "--server=8.8.8.8",
            "--server=1.1.1.1",
            "--no-resolv",
            "--listen-address=0.0.0.0",
            "--log-facility=-",
            "--log-queries",
        ]

        try:
            dns_container.stop(timeout=5)
        except Exception:
            pass
        try:
            dns_container.remove()
        except Exception:
            pass

        new_container = client.containers.run(
            image,
            command=new_cmd,
            name=container_name,
            detach=True,
            ports={"53/udp": 53, "53/tcp": 53},
            cap_add=cap_add,
            network=networks[0] if networks else "bridge",
            restart_policy={"Name": "always"},
            labels=labels,
        )
        return new_container

    _dnsmasq_restart_lock = threading.Lock()

    def _restart_dnsmasq_async(host_ip, lab_domain):
        def _run():
            if not _dnsmasq_restart_lock.acquire(blocking=False):
                logger.info("[Network] dnsmasq restart already in progress")
                return
            try:
                _restart_dnsmasq_with_ip(host_ip, lab_domain)
                logger.info("[Network] dnsmasq restarted: *.%s -> %s", lab_domain, host_ip)
            except Exception as exc:
                logger.error("[Network] dnsmasq restart failed: %s", exc)
            finally:
                _dnsmasq_restart_lock.release()

        threading.Thread(target=_run, daemon=True, name="dnsmasq-restart").start()

    @page_blueprint.route("/admin/network", methods=["GET"])
    @admins_only
    def admin_network():
        host_ip      = _get_host_ip()
        ctf_hostname = _get_config("ctf_hostname", os.environ.get("CTF_HOSTNAME", "lloydsctf.lab"))
        lab_domain   = _get_config("lab_domain", os.environ.get("LAB_DOMAIN", "lab"))
        return render_plugin_template(
            "network_setup.html",
            host_ip=host_ip if host_ip not in ("localhost",) else "",
            ctf_hostname=ctf_hostname,
            lab_domain=lab_domain,
            nav="network",
        )

    @page_blueprint.route("/admin/network/save", methods=["POST"])
    @admins_only
    def admin_network_save():
        payload = request.get_json(silent=True) or {}
        host_ip = (request.form.get("host_ip") or payload.get("host_ip") or "").strip()
        ctf_hostname = (request.form.get("ctf_hostname") or payload.get("ctf_hostname") or "lloydsctf.lab").strip()
        lab_domain = ctf_hostname.split(".")[-1] if "." in ctf_hostname else "lab"

        if not host_ip:
            return _json_response({"success": False, "msg": "Host IP is required."}, status=400)

        import re as _re
        if not _re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", host_ip):
            return _json_response({"success": False, "msg": "Enter a valid IPv4 address, e.g. 192.168.1.10."}, status=400)

        try:
            set_config("ctfd_target:host_ip", host_ip)
            set_config("ctfd_target:ctf_hostname", ctf_hostname)
            set_config("ctfd_target:lab_domain", lab_domain)
        except Exception as exc:
            logger.exception("[Network] failed to persist settings: %s", exc)
            return _json_response({"success": False, "msg": "Could not save settings: %s" % exc}, status=500)

        _restart_dnsmasq_async(host_ip, lab_domain)
        return _json_response({
            "success": True,
            "msg": "Saved. DNS is updating in the background.",
            "host_ip": host_ip,
            "ctf_hostname": ctf_hostname,
        })

    @page_blueprint.route("/admin/network/detect-ips", methods=["GET"])
    @admins_only
    def admin_network_detect_ips():
        """Return a list of candidate host IPs for the admin to choose from."""
        ips = []
        seen = set()

        def _add(ip, label, source):
            if ip and ip not in seen and not ip.startswith("127.") and ip != "0.0.0.0":
                ips.append({"ip": ip, "label": label, "source": source})
                seen.add(ip)

        # Prefer this machine's detected LAN IP (start scripts) over stale DB values
        _add(_env_host_ip(), "Detected LAN IP (HOST_IP)", "env")
        _add(_get_host_ip(), "Effective host IP", "effective")
        _add(_get_config("host_ip", ""), "Saved in settings", "config")

        # Docker host gateway (often wrong for LAN players â€” kept as fallback)
        try:
            import subprocess as _sp
            out = _sp.run(["ip", "route", "show", "default"],
                          capture_output=True, text=True, timeout=2).stdout
            for _line in out.splitlines():
                parts = _line.split()
                if "via" in parts:
                    _add(parts[parts.index("via") + 1], "Docker host gateway", "route")
        except Exception:
            pass

        try:
            import socket as _sock
            _s = _sock.socket(_sock.AF_INET, _sock.SOCK_DGRAM)
            _s.connect(("8.8.8.8", 80))
            _add(_s.getsockname()[0], "Outbound IP", "socket")
            _s.close()
        except Exception:
            pass

        return _json_response({"success": True, "ips": ips})

    @page_blueprint.route("/admin/network/dns-status", methods=["GET"])
    @admins_only
    def admin_network_dns_status():
        """Live health check: is the dnsmasq container running and resolving?"""
        try:
            client = _get_docker_client()
            dns_container = None
            for c in client.containers.list(all=True):
                if "dnsmasq" in c.name:
                    dns_container = c
                    break

            if not dns_container:
                return _json_response({"running": False, "status": "not_found", "name": None})

            dns_container.reload()
            host_ip      = _get_host_ip()
            if host_ip in ("localhost",):
                host_ip = ""
            ctf_hostname = _get_config("ctf_hostname", os.environ.get("CTF_HOSTNAME", "lloydsctf.lab"))

            # Try a DNS query from inside the container to verify resolution
            resolves = False
            resolved_ip = ""
            if dns_container.status == "running" and host_ip:
                try:
                    result = dns_container.exec_run(
                        ["nslookup", ctf_hostname, "127.0.0.1"],
                        timeout=3
                    )
                    out = (result.output or b"").decode(errors="replace")
                    resolves = host_ip in out
                    if resolves:
                        resolved_ip = host_ip
                except Exception:
                    pass

            return _json_response({
                "running": dns_container.status == "running",
                "status": dns_container.status,
                "name": dns_container.name,
                "host_ip": host_ip,
                "ctf_hostname": ctf_hostname,
                "resolves": resolves,
                "resolved_ip": resolved_ip,
            })
        except Exception as exc:
            return _json_response({"running": False, "status": "error", "msg": str(exc)})


    @page_blueprint.route("/ca.crt", methods=["GET"])
    def serve_ca_certificate():
        """Serve the CA certificate for players to install."""
        from flask import send_file
        ca_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "..", "nginx", "certs", "ca.crt")
        if os.path.isfile(ca_path):
            return send_file(ca_path, as_attachment=True, download_name="lloyds-ctf-ca.crt", mimetype="application/pkcs8")
        return json.dumps({"success": False, "msg": "CA certificate not found"}), 404

    @page_blueprint.route("/certificate-guide", methods=["GET"])
    def certificate_guide():
        """Display certificate installation guide."""
        host = request.host.split(":")[0]
        guide = f"""
        <html>
        <head>
            <title>Lloyds CTF - Certificate Installation</title>
            <style>
                body {{ font-family: monospace; margin: 20px; background: #1a1a1a; color: #00ff41; }}
                .container {{ max-width: 800px; margin: auto; }}
                h1 {{ color: #00ffff; }}
                .command {{ background: #0a0a0a; padding: 10px; margin: 10px 0; border-left: 4px solid #00ff41; }}
                .windows {{ border-left-color: #4472c4; }}
                .mac {{ border-left-color: #ff9500; }}
                .linux {{ border-left-color: #fcc624; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>Lloyds CTF - Certificate Installation Guide</h1>
                <p>To access https://ctfd.lab, you must install the CA certificate.</p>
                
                <h2>Option 1: Download & Install (Easiest)</h2>
                <a href="/plugins/ctfd-target/ca.crt" download>Download CA Certificate</a>
                
                <h2>Windows</h2>
                <ol>
                    <li>Download the certificate above</li>
                    <li>Right-click ca.crt â†’ "Install Certificate"</li>
                    <li>Select "Local Machine" â†’ Click "Next"</li>
                    <li>Select "Place all certificates in the following store"</li>
                    <li>Browse â†’ Select "Trusted Root Certification Authorities"</li>
                    <li>Click "Next" then "Finish"</li>
                    <li>Click "Yes" on the security warning</li>
                </ol>
                
                <h2>macOS</h2>
                <div class="command mac">
                    sudo security add-trusted-cert -d -r trustRoot \\\n
                    -k /Library/Keychains/System.keychain ca.crt
                </div>
                
                <h2>Linux (Ubuntu/Debian)</h2>
                <div class="command linux">
                    sudo cp ca.crt /usr/local/share/ca-certificates/lloyds-ctf.crt<br>
                    sudo update-ca-certificates
                </div>
                
                <h2>Linux (Fedora/RHEL)</h2>
                <div class="command linux">
                    sudo cp ca.crt /etc/pki/ca-trust/source/anchors/<br>
                    sudo update-ca-trust
                </div>
                
                <h2>Chrome/Chromium</h2>
                <ol>
                    <li>Settings â†’ Privacy and Security â†’ Manage Certificates</li>
                    <li>Authorities tab â†’ Import</li>
                    <li>Select ca.crt and click "Open"</li>
                    <li>Check all boxes and click "OK"</li>
                </ol>
            </div>
        </body>
        </html>
        """
        from flask import Response
        return Response(guide, mimetype="text/html")

    _CSS_TAG = '<link rel="stylesheet" href="/plugins/ctfd-target/assets/cyber-theme.css?v=38">'
    _JS_TAG  = '<script src="/plugins/ctfd-target/assets/target-inject.js?v=39"></script>'
    _HEAD_INJECT = _CSS_TAG + "\n</head>"
    _BODY_INJECT = _JS_TAG  + "\n</body>"
    _CSS_MARKER  = b"cyber-theme.css?v=38"   # fast bytes probe

    @app.after_request
    def inject_target_assets(response):
        # Only touch text/html; skip JSON, static files, etc.
        ct = response.content_type or ""
        if "text/html" not in ct:
            return response
        path = request.path or ""
        if (
            path.startswith("/plugins/ctfd-target/board")
            or path.endswith("/briefing")
            or path.endswith("/pack")
        ):
            return response
        # Fast-path: peek at the raw bytes without full decode.
        # If our marker is already present, skip entirely.
        raw = response.get_data()
        if _CSS_MARKER in raw:
            return response
        # Only process if it looks like a full page (has </head>)
        if b"</head>" not in raw:
            return response
        try:
            data = raw.decode("utf-8", errors="replace")
            data = data.replace("</head>", _HEAD_INJECT, 1)
            data = data.replace("</body>", _BODY_INJECT, 1)
            response.set_data(data.encode("utf-8"))
        except Exception:
            pass
        return response

    register_admin_plugin_menu_bar(
        title="Dashboards",
        route="/plugins/ctfd-target/admin/hub",
    )

    from CTFd.plugins.flags import FLAG_CLASSES

    FLAG_CLASSES["target_flag"] = TargetFlag

    app.register_blueprint(page_blueprint)

    def _ensure_challenge_target_columns():
        from CTFd.models import db
        from sqlalchemy import text
        alters = [
            "ALTER TABLE ctfd_challenge_target_config ADD COLUMN challenge_key VARCHAR(64) DEFAULT ''",
            "ALTER TABLE ctfd_challenge_target_config ADD COLUMN target_profile VARCHAR(64) DEFAULT ''",
            "ALTER TABLE ctfd_challenge_target_config ADD COLUMN health_path VARCHAR(255) DEFAULT '/'",
        ]
        for sql in alters:
            try:
                db.session.execute(text(sql))
                db.session.commit()
            except Exception:
                db.session.rollback()

    def _seed_challenge_target_defaults():
        from CTFd.models import Challenges, db
        try:
            db.create_all()
        except Exception as exc:
            logger.warning("[ChallengeTarget] create_all failed: %s", exc)
        _ensure_challenge_target_columns()
        created = 0
        updated = 0
        for chal in Challenges.query.filter(Challenges.name.in_(CANONICAL_CHALLENGE_NAMES)).all():
            meta = CATALOG_BY_NAME.get(chal.name) or {}
            existing = ChallengeTargetConfig.query.filter_by(challenge_id=chal.id).first()
            image = meta.get("image") or _get_config("target_image", DEFAULT_TARGET_IMAGE)
            port = int(meta.get("internal_port") or _get_config("target_port", DEFAULT_TARGET_PORT) or 80)
            needs_analytics = bool(meta.get("needs_analytics", False))
            mem_limit = meta.get("mem_limit") or ""
            challenge_key = meta.get("flag_key") or ""
            target_profile = meta.get("profile") or ""
            health_path = meta.get("health_path") or "/"
            if existing:
                first_backfill = not (existing.challenge_key or "").strip()
                changed = False
                if first_backfill and challenge_key:
                    existing.challenge_key = challenge_key
                    existing.target_profile = target_profile
                    existing.health_path = health_path
                    existing.image_name = image
                    existing.internal_port = port
                    existing.needs_analytics = needs_analytics
                    existing.mem_limit = mem_limit
                    changed = True
                else:
                    if not (existing.target_profile or "").strip() and target_profile:
                        existing.target_profile = target_profile
                        changed = True
                    if not (existing.health_path or "").strip() and health_path:
                        existing.health_path = health_path
                        changed = True
                if changed:
                    updated += 1
                continue
            db.session.add(ChallengeTargetConfig(
                challenge_id=chal.id,
                enabled=True,
                image_name=image,
                internal_port=port,
                needs_analytics=needs_analytics,
                mem_limit=mem_limit,
                challenge_key=challenge_key,
                target_profile=target_profile,
                health_path=health_path,
            ))
            created += 1
        if created or updated:
            db.session.commit()
            logger.info("[ChallengeTarget] Seeded %s default configs, updated %s", created, updated)

    try:
        with app.app_context():
            _seed_challenge_target_defaults()
    except Exception as exc:
        logger.warning("[ChallengeTarget] seed failed: %s", exc)



