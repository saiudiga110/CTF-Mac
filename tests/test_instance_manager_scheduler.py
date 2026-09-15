import unittest

from infra.instance_manager.scheduler import InstanceRequest, WorkerState, select_worker


class SchedulerTest(unittest.TestCase):
    def test_selects_less_loaded_compatible_worker(self):
        busy = WorkerState(
            worker_id="busy",
            public_ip="10.0.0.2",
            runtime_platforms=["linux/amd64"],
            cpu_runtime_allocated=8,
            ram_runtime_allocated_mb=16000,
            disk_free_mb=100000,
            container_limit=80,
            port_range_start=20000,
            port_range_end=20010,
            used_cpu=7,
            used_ram_mb=14000,
            container_count=50,
            features={"linux_containers": True},
        )
        free = WorkerState(
            worker_id="free",
            public_ip="10.0.0.3",
            runtime_platforms=["linux/amd64"],
            cpu_runtime_allocated=8,
            ram_runtime_allocated_mb=16000,
            disk_free_mb=100000,
            container_limit=80,
            port_range_start=20000,
            port_range_end=20010,
            used_cpu=2,
            used_ram_mb=4000,
            container_count=10,
            features={"linux_containers": True},
        )
        req = InstanceRequest("ctf/target:v1", "linux/amd64", 0.5, 512, 256, required_features=["linux_containers"])
        placement = select_worker([busy, free], req)
        self.assertEqual(placement.worker_id, "free")

    def test_rejects_incompatible_architecture(self):
        arm_only = WorkerState(
            worker_id="arm",
            public_ip="10.0.0.4",
            runtime_platforms=["linux/arm64"],
            cpu_runtime_allocated=8,
            ram_runtime_allocated_mb=16000,
            disk_free_mb=100000,
            container_limit=80,
            port_range_start=20000,
            port_range_end=20010,
            features={"linux_containers": True},
        )
        req = InstanceRequest("ctf/kali:v1", "linux/amd64", 1, 2048, 1024, required_features=["linux_containers"])
        with self.assertRaisesRegex(RuntimeError, "unsupported"):
            select_worker([arm_only], req)

    def test_allows_emulated_architecture_with_penalty(self):
        arm_emulated = WorkerState(
            worker_id="arm-emulated",
            public_ip="10.0.0.5",
            runtime_platforms=["linux/arm64", "linux/amd64-emulated"],
            cpu_runtime_allocated=8,
            ram_runtime_allocated_mb=16000,
            disk_free_mb=100000,
            container_limit=80,
            port_range_start=20000,
            port_range_end=20010,
            features={"linux_containers": True},
        )
        req = InstanceRequest("ctf/kali:v1", "linux/amd64", 1, 2048, 1024, required_features=["linux_containers"])
        placement = select_worker([arm_emulated], req)
        self.assertEqual(placement.worker_id, "arm-emulated")
        self.assertIn("emulated", placement.reasons[0])


if __name__ == "__main__":
    unittest.main()

