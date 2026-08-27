"""Redis-backed FIFO of run ids. The worker BLPOPs the same key."""

import redis

from . import config


class JobQueue:
    def __init__(self, client=None):
        self.client = client or redis.Redis.from_url(
            config.redis_url(), socket_timeout=5, socket_connect_timeout=5
        )

    def push(self, run_id: str) -> None:
        self.client.rpush(config.JOBS_KEY, run_id)

    def remove(self, run_id: str) -> int:
        return int(self.client.lrem(config.JOBS_KEY, 0, run_id))

    def pending(self) -> list[str]:
        return [v.decode() for v in self.client.lrange(config.JOBS_KEY, 0, -1)]


_queue: JobQueue | None = None


def get_queue() -> JobQueue:
    global _queue
    if _queue is None:
        _queue = JobQueue()
    return _queue
