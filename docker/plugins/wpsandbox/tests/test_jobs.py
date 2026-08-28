import fakeredis
import redis

from wpsandbox.config import JOBS_KEY
from wpsandbox.jobs import JobQueue


def test_push_appends_in_order():
    r = fakeredis.FakeRedis()
    q = JobQueue(r)
    q.push("a"); q.push("b")
    assert r.lrange(JOBS_KEY, 0, -1) == [b"a", b"b"]
    assert q.pending() == ["a", "b"]


def test_default_client_sets_socket_timeouts(monkeypatch):
    calls = []

    def fake_from_url(url, **kwargs):
        calls.append((url, kwargs))
        return fakeredis.FakeRedis()

    monkeypatch.setattr(redis.Redis, "from_url", staticmethod(fake_from_url))
    JobQueue()
    assert len(calls) == 1
    _, kwargs = calls[0]
    assert kwargs["socket_timeout"] == 5
    assert kwargs["socket_connect_timeout"] == 5


def test_remove_deletes_all_occurrences():
    r = fakeredis.FakeRedis()
    q = JobQueue(r)
    q.push("a"); q.push("b"); q.push("a")
    assert q.remove("a") == 2
    assert q.pending() == ["b"]
    assert q.remove("zzz") == 0
