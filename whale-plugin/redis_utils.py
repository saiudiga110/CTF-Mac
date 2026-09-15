from flask_redis import FlaskRedis
from redis.exceptions import LockError
from .db_utils import DBUtils


class RedisUtils:
    def __init__(self, app, user_id=0):
        self.redis_client = FlaskRedis(app)
        self.key = 'ctfd_whale_lock-' + str(user_id)
        self.lock = None
        self.global_port_key = "ctfd_whale-port-set"

    def init_redis_port_sets(self):
        configs = DBUtils.get_all_configs()

        self.redis_client.delete(self.global_port_key)

        containers = DBUtils.get_all_container()
        used_port_list = []
        for container in containers:
            if container.port != 0:
                used_port_list.append(container.port)

        port_min = int(configs.get("frp_direct_port_minimum", "10000"))
        port_max = int(configs.get("frp_direct_port_maximum", "10100"))

        for port in range(port_min, port_max + 1):
            if port not in used_port_list:
                self.add_available_port(port)

        print(f"[CTFd Whale] Initialized port pool: {port_min}-{port_max}")

    def add_available_port(self, port):
        self.redis_client.sadd(self.global_port_key, str(port))

    def get_available_port(self):
        port = self.redis_client.spop(self.global_port_key)
        if port is None:
            raise Exception("No available ports in the pool!")
        return int(port)

    def acquire_lock(self):
        lock = self.redis_client.lock(name=self.key, timeout=10)

        if not lock.acquire(blocking=True, blocking_timeout=2.0):
            return False

        self.lock = lock
        return True

    def release_lock(self):
        if self.lock is None:
            return False

        try:
            self.lock.release()
            return True
        except LockError:
            return False
