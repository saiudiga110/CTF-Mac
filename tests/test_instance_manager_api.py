import os
import sqlite3
import tempfile
import time
import unittest


class InstanceManagerApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        os.environ["INSTANCE_MANAGER_DISABLE_AUTH"] = "1"
        os.environ["INSTANCE_MANAGER_DB"] = os.path.join(self.tmp.name, "manager.sqlite3")
        from infra.instance_manager.app import create_app

        self.client = create_app().test_client()
        worker = {
            "worker_id": "worker-a",
            "public_ip": "10.0.0.10",
            "agent_url": "http://10.0.0.10:8090",
            "runtime_platforms": ["linux/amd64"],
            "cpu_runtime_allocated": 8,
            "ram_runtime_allocated_mb": 16000,
            "disk_free_mb": 100000,
            "container_limit": 80,
            "port_range_start": 20000,
            "port_range_end": 20002,
            "features": {"linux_containers": True},
            "docker_available": True,
        }
        self.assertEqual(self.client.post("/workers/heartbeat", json=worker).status_code, 200)

    def tearDown(self):
        self.tmp.cleanup()
        os.environ.pop("INSTANCE_MANAGER_DISABLE_AUTH", None)
        os.environ.pop("INSTANCE_MANAGER_DB", None)

    def test_allocations_consume_ports_and_idempotency_reuses_instance(self):
        payload = {
            "image": "ctf/target:v1",
            "image_platform": "linux/amd64",
            "cpu_request": 0.5,
            "memory_mb": 256,
            "disk_mb": 256,
            "challenge_id": 1,
            "ctfd_user_id": 1,
            "idempotency_key": "u1-c1",
        }
        first = self.client.post("/instances", json=payload)
        self.assertEqual(first.status_code, 201)
        self.assertEqual(first.get_json()["instance"]["public_port"], 20000)

        repeat = self.client.post("/instances", json=payload)
        self.assertEqual(repeat.status_code, 200)
        self.assertTrue(repeat.get_json()["idempotent"])
        self.assertEqual(repeat.get_json()["instance"]["public_port"], 20000)

        second_payload = dict(payload, ctfd_user_id=2, idempotency_key="u2-c1")
        second = self.client.post("/instances", json=second_payload)
        self.assertEqual(second.status_code, 201)
        self.assertEqual(second.get_json()["instance"]["public_port"], 20001)

    def test_stale_workers_are_not_scheduled(self):
        stale_worker = {
            "worker_id": "worker-stale",
            "public_ip": "10.0.0.99",
            "agent_url": "http://10.0.0.99:8090",
            "runtime_platforms": ["linux/amd64"],
            "cpu_runtime_allocated": 8,
            "ram_runtime_allocated_mb": 16000,
            "disk_free_mb": 100000,
            "container_limit": 80,
            "port_range_start": 20000,
            "port_range_end": 20002,
            "features": {"linux_containers": True},
            "docker_available": True,
        }
        self.assertEqual(self.client.post("/workers/heartbeat", json=stale_worker).status_code, 200)
        db_path = os.environ["INSTANCE_MANAGER_DB"]
        with sqlite3.connect(db_path) as con:
            con.execute(
                "update workers set last_heartbeat = ? where worker_id = ?",
                (time.time() - 3600, "worker-stale"),
            )

        payload = {
            "image": "ctf/target:v1",
            "image_platform": "linux/amd64",
            "cpu_request": 0.5,
            "memory_mb": 256,
            "disk_mb": 256,
            "challenge_id": 1,
            "ctfd_user_id": 3,
            "idempotency_key": "u3-c1",
        }
        created = self.client.post("/instances", json=payload)
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.get_json()["instance"]["worker_id"], "worker-a")


if __name__ == "__main__":
    unittest.main()
