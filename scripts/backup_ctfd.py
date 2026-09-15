#!/usr/bin/env python3
"""Create a timestamped backup of the CTFd database, uploads, and challenge metadata."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKUP_ROOT = ROOT / "backups"


def run(command: list[str], *, cwd: Path | None = None, capture: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        cwd=str(cwd or ROOT),
        check=True,
        text=True,
        capture_output=capture,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", help="Optional backup folder name suffix")
    args = parser.parse_args()

    stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    suffix = f"-{args.name}" if args.name else ""
    target = BACKUP_ROOT / f"ctfd-backup-{stamp}{suffix}"
    target.mkdir(parents=True, exist_ok=True)

    env_path = ROOT / ".env"
    env_values = {}
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                key, value = line.split("=", 1)
                env_values[key.strip()] = value.strip()

    mysql_password = env_values.get("MYSQL_PASSWORD", "ctfd")
    dump_cmd = [
        "docker",
        "compose",
        "exec",
        "-T",
        "db",
        "mariadb-dump",
        "-uctfd",
        f"-p{mysql_password}",
        "ctfd",
    ]
    dump = run(dump_cmd, capture=True)
    (target / "ctfd.sql").write_text(dump.stdout, encoding="utf-8")

    uploads_src = ROOT / ".data" / "CTFd" / "uploads"
    uploads_dst = target / "uploads"
    if uploads_src.exists():
        shutil.copytree(uploads_src, uploads_dst)

    challenge_json = ROOT / "challenges" / "vbank-ctf" / "ctfd_challenges.json"
    if challenge_json.exists():
        shutil.copy2(challenge_json, target / "ctfd_challenges.json")

    setup_script = ROOT / "setup_ctfd_challenges.py"
    if setup_script.exists():
        shutil.copy2(setup_script, target / "setup_ctfd_challenges.py")

    print(f"Backup created at {target}")


if __name__ == "__main__":
    main()
