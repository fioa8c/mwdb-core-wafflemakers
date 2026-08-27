import fakeredis

from wpsandbox.config import JOBS_KEY
from wpsandbox.jobs import JobQueue


def test_push_appends_in_order():
    r = fakeredis.FakeRedis()
    q = JobQueue(r)
    q.push("a"); q.push("b")
    assert r.lrange(JOBS_KEY, 0, -1) == [b"a", b"b"]
    assert q.pending() == ["a", "b"]


def test_remove_deletes_all_occurrences():
    r = fakeredis.FakeRedis()
    q = JobQueue(r)
    q.push("a"); q.push("b"); q.push("a")
    assert q.remove("a") == 2
    assert q.pending() == ["b"]
    assert q.remove("zzz") == 0
