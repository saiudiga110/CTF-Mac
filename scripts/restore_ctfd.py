#!/usr/bin/env python3
"""Restore a backup created by backup_ctfd.py."""

from __future__ import annotations

import argparse
import gzip
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run(command: list[str], *, input_text: str | None = None) -> None:
    subprocess.run(
        command,
        cwd=str(ROOT),
        check=True,
        text=True,
        input=input_text,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "backup",
        help="Backup directory from backup_ctfd.py, or an automatic .sql/.sql.gz dump",
    )
    args = parser.parse_args()

    backup = Path(args.backup).resolve()
    backup_dir = backup if backup.is_dir() else backup.parent
    sql_path = backup_dir / "ctfd.sql" if backup.is_dir() else backup
    if not sql_path.exists():
        raise SystemExit(f"Missing SQL dump: {sql_path}")
    if sql_path.suffix not in (".sql", ".gz"):
        raise SystemExit("Expected a .sql or .sql.gz database dump")

    env_path = ROOT / ".env"
    env_values = {}
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                key, value = line.split("=", 1)
                env_values[key.strip()] = value.strip()

    mysql_password = env_values.get("MYSQL_PASSWORD", "ctfd")
    if sql_path.suffix == ".gz":
        with gzip.open(sql_path, "rt", encoding="utf-8") as handle:
            sql_text = handle.read()
    else:
        sql_text = sql_path.read_text(encoding="utf-8")
    run(
        ["docker", "compose", "exec", "-T", "db", "mariadb", "-uctfd", f"-p{mysql_password}", "ctfd"],
        input_text=sql_text,
    )

    uploads_src = backup_dir / "uploads"
    uploads_dst = ROOT / ".data" / "CTFd" / "uploads"
    if uploads_src.exists():
        if uploads_dst.exists():
            shutil.rmtree(uploads_dst)
        shutil.copytree(uploads_src, uploads_dst)

    print(f"Restore completed from {sql_path}")


if __name__ == "__main__":
    main()
