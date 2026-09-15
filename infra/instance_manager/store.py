"""Small SQLite state store for the Instance Manager."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any


SCHEMA = """
create table if not exists workers (
  worker_id text primary key,
  payload text not null,
  status text not null,
  last_heartbeat real not null
);

create table if not exists instances (
  id text primary key,
  payload text not null,
  status text not null,
  created_at real not null,
  expires_at real
);

create table if not exists instance_events (
  id integer primary key autoincrement,
  instance_id text,
  worker_id text,
  event_type text not null,
  message text not null,
  details text,
  created_at real not null
);
"""


class Store:
    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(str(self.path))
        con.row_factory = sqlite3.Row
        return con

    def _init(self) -> None:
        with self.connect() as con:
            con.executescript(SCHEMA)

    def upsert_worker(self, payload: dict[str, Any]) -> None:
        now = time.time()
        status = payload.get("status") or ("ready" if payload.get("docker_available", True) else "unavailable")
        with self.connect() as con:
            con.execute(
                """
                insert into workers(worker_id, payload, status, last_heartbeat)
                values(?, ?, ?, ?)
                on conflict(worker_id) do update set
                  payload=excluded.payload,
                  status=excluded.status,
                  last_heartbeat=excluded.last_heartbeat
                """,
                (payload["worker_id"], json.dumps(payload), status, now),
            )

    def list_workers(self) -> list[dict[str, Any]]:
        with self.connect() as con:
            rows = con.execute("select payload, status, last_heartbeat from workers").fetchall()
        workers = []
        for row in rows:
            payload = json.loads(row["payload"])
            payload["status"] = row["status"]
            payload["last_heartbeat"] = row["last_heartbeat"]
            workers.append(payload)
        return workers

    def create_instance(self, instance_id: str, payload: dict[str, Any], expires_at: float | None) -> None:
        now = time.time()
        with self.connect() as con:
            con.execute(
                "insert into instances(id, payload, status, created_at, expires_at) values(?, ?, ?, ?, ?)",
                (instance_id, json.dumps(payload), payload.get("status", "allocated"), now, expires_at),
            )

    def get_instance(self, instance_id: str) -> dict[str, Any] | None:
        with self.connect() as con:
            row = con.execute(
                "select id, payload, status, created_at, expires_at from instances where id = ?",
                (instance_id,),
            ).fetchone()
        if not row:
            return None
        payload = json.loads(row["payload"])
        payload.update(
            {
                "id": row["id"],
                "status": row["status"],
                "created_at": row["created_at"],
                "expires_at": row["expires_at"],
            }
        )
        return payload

    def update_instance(self, instance_id: str, payload: dict[str, Any], status: str | None = None, expires_at: float | None = None) -> None:
        new_status = status or payload.get("status", "unknown")
        with self.connect() as con:
            if expires_at is None:
                con.execute(
                    "update instances set payload = ?, status = ? where id = ?",
                    (json.dumps(payload), new_status, instance_id),
                )
            else:
                con.execute(
                    "update instances set payload = ?, status = ?, expires_at = ? where id = ?",
                    (json.dumps(payload), new_status, expires_at, instance_id),
                )

    def list_instances(self) -> list[dict[str, Any]]:
        with self.connect() as con:
            rows = con.execute("select id, payload, status, created_at, expires_at from instances").fetchall()
        out = []
        for row in rows:
            payload = json.loads(row["payload"])
            payload.update(
                {
                    "id": row["id"],
                    "status": row["status"],
                    "created_at": row["created_at"],
                    "expires_at": row["expires_at"],
                }
            )
            out.append(payload)
        return out

    def event(self, event_type: str, message: str, instance_id: str | None = None, worker_id: str | None = None, details: dict[str, Any] | None = None) -> None:
        with self.connect() as con:
            con.execute(
                """
                insert into instance_events(instance_id, worker_id, event_type, message, details, created_at)
                values(?, ?, ?, ?, ?, ?)
                """,
                (instance_id, worker_id, event_type, message, json.dumps(details or {}), time.time()),
            )
