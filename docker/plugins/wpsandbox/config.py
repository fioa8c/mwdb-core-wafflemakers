import os

JOBS_KEY = "wpsandbox:jobs"


def redis_url() -> str:
    return os.environ.get("MWDB_WPSANDBOX_REDIS_URL") or os.environ.get(
        "MWDB_REDIS_URI", "redis://redis/"
    )


def worker_login() -> str:
    return os.environ.get("MWDB_WPSANDBOX_WORKER_LOGIN", "wpsandbox-worker")


def max_sample_bytes() -> int:
    return int(os.environ.get("MWDB_WPSANDBOX_MAX_SAMPLE_BYTES", 20 * 1024 * 1024))


def max_timeout() -> int:
    return int(os.environ.get("MWDB_WPSANDBOX_MAX_TIMEOUT", 300))


def worker_lost_after() -> int:
    return max_timeout() + 600
