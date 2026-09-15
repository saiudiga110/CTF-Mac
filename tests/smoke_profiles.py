"""Optional Docker smoke: build 10 profile images and launch one container each.

Run with:  python tests/smoke_profiles.py
Skip automatically if docker is unavailable.
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CATALOG = os.path.join(ROOT, "challenge-catalog.json")


def _run(cmd, **kwargs):
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, **kwargs)


def docker_available():
    try:
        r = _run(["docker", "info"], timeout=20)
        return r.returncode == 0
    except Exception:
        return False


def main():
    if not docker_available():
        print("SKIP: docker is not available")
        return 0

    cfg = _run(["docker", "compose", "config"], timeout=60)
    if cfg.returncode != 0:
        print(cfg.stderr)
        return 1
    print("compose config: ok")

    with open(CATALOG, encoding="utf-8") as fh:
        catalog = json.load(fh)
    images = []
    for row in catalog["challenges"]:
        if row["image"] not in images:
            images.append(row["image"])
    if len(images) != 10:
        print(f"expected 10 images, got {images}")
        return 1

    services = [tag.split(":")[0] for tag in images]
    build = _run(["docker", "compose", "build"] + services, timeout=3600)
    if build.returncode != 0:
        print(build.stderr[-4000:])
        return 1
    print("built:", ", ".join(images))

    failed = []
    for tag in images:
        name = f"smoke-{tag.replace(':', '-')}"
        _run(["docker", "rm", "-f", name], timeout=30)
        run = _run([
            "docker", "run", "-d", "--name", name, "-P",
            "-e", "FLAG_SECRET=vbank_ctf_default_2024",
            "-e", "TEAM_ID=0",
            tag,
        ], timeout=60)
        if run.returncode != 0:
            failed.append((tag, run.stderr.strip()))
            continue
        time.sleep(3)
        inspect = _run(["docker", "inspect", "-f", "{{.State.Status}}", name], timeout=20)
        status = (inspect.stdout or "").strip()
        _run(["docker", "rm", "-f", name], timeout=30)
        if status not in ("running", "created"):
            failed.append((tag, f"status={status}"))
        else:
            print(f"smoke {tag}: {status}")

    if failed:
        for tag, err in failed:
            print(f"FAIL {tag}: {err}")
        return 1
    print("all profile smokes passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
