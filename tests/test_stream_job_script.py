import sys
import threading
import types
from pathlib import Path

import cloudpickle
import pytest

STREAM = Path(__file__).resolve().parent.parent / "stream" / "stream_to_lakebase.py"


def load_job(monkeypatch):
    """Exec the job script the way Databricks spark_python_task does: no __file__, not __main__."""
    sql = types.ModuleType("pyspark.sql")
    sql.SparkSession = object
    sql.functions = types.ModuleType("pyspark.sql.functions")
    monkeypatch.setitem(sys.modules, "pyspark", types.ModuleType("pyspark"))
    monkeypatch.setitem(sys.modules, "pyspark.sql", sql)
    monkeypatch.setitem(sys.modules, "pyspark.sql.functions", sql.functions)
    monkeypatch.setattr(sys, "path", list(sys.path))
    g = {"__name__": "job_under_test"}
    exec(compile(STREAM.read_text(), str(STREAM), "exec"), g)
    return g


class FakeDriverLakebase:
    """Stands in for stream_to_lakebase.Lakebase: like the real SDK client, it holds a lock."""

    def __init__(self):
        self._lock = threading.Lock()
        cred = types.SimpleNamespace(token="tok-123")
        self.w = types.SimpleNamespace(postgres=types.SimpleNamespace(
            generate_database_credential=lambda endpoint: cred))
        self.endpoint = types.SimpleNamespace(
            name="projects/p/branches/production/endpoints/primary",
            status=types.SimpleNamespace(hosts=types.SimpleNamespace(host="ep-x.database.example.com")))
        self.database, self.user = "worker_safety", "ram@example.com"


class FlakyLakebase:
    """Connection fails `failures` times with OperationalError, then works; PRUNE can be made to fail."""

    def __init__(self, psycopg, failures, prune_fails=False):
        self.psycopg, self.failures, self.prune_fails = psycopg, failures, prune_fails
        self.executed, self.resets = [], 0

    def connection(self):
        if self.failures:
            self.failures -= 1
            raise self.psycopg.OperationalError("server closed the connection unexpectedly")
        return self

    def reset(self):
        self.resets += 1

    def transaction(self):
        return self

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def executemany(self, sql, params):
        self.executed.append(sql.split()[0])

    def execute(self, sql):
        if self.prune_fails:
            raise self.psycopg.OperationalError("prune failed")
        self.executed.append("PRUNE")


class FakeBatch:
    def __init__(self, rows):
        self.rows = rows

    def collect(self):
        return [types.SimpleNamespace(asDict=lambda r=r: dict(r)) for r in self.rows]


def fall_row():
    from datetime import datetime
    ts = datetime(2026, 10, 14, 12, 0)
    return {"event_id": "d-b-1", "device_id": "d", "event_type": "fall", "event_ts": ts, "landed_at": ts,
            "seq": 1, "worker_name": "W", "accel_g": 1.0, "peak_g": 3.0, "noise_db": None, "battery_pct": 90,
            "rssi": -50, "free_heap": 1000, "fw_version": "t"}


def test_writer_rides_out_a_lakebase_blip(monkeypatch):
    # A crash here kills the continuous run, and its replacement waits minutes for a new job cluster.
    g = load_job(monkeypatch)
    monkeypatch.setattr(g["time"], "sleep", lambda s: None)
    lb = FlakyLakebase(g["psycopg"], failures=3)
    g["make_writer"](lb)(FakeBatch([fall_row()]), 7)
    assert lb.executed == ["INSERT", "INSERT"]  # worker_status upsert + incident
    assert lb.resets == 3


def test_writer_gives_up_after_the_retry_window(monkeypatch):
    g = load_job(monkeypatch)
    clock = iter(range(0, 10000, 5))
    monkeypatch.setattr(g["time"], "time", lambda: next(clock))
    monkeypatch.setattr(g["time"], "sleep", lambda s: None)
    lb = FlakyLakebase(g["psycopg"], failures=10**6)
    with pytest.raises(g["psycopg"].OperationalError):
        g["make_writer"](lb)(FakeBatch([fall_row()]), 7)


def test_failed_prune_never_fails_the_stream(monkeypatch):
    g = load_job(monkeypatch)
    g["PRUNE_EVERY"] = 1
    lb = FlakyLakebase(g["psycopg"], failures=0, prune_fails=True)
    g["make_writer"](lb)(FakeBatch([fall_row()]), 7)
    assert lb.executed == ["INSERT", "INSERT"]


def test_job_script_runs_without_dunder_file(monkeypatch):
    g = load_job(monkeypatch)
    assert str(STREAM.parent) in sys.path
    assert callable(g["main"])


def test_serverless_writer_pickles_without_the_sdk_client(monkeypatch):
    # Spark Connect (serverless) cloudpickles the foreachBatch function and runs it remotely.
    g = load_job(monkeypatch)
    driver = FakeDriverLakebase()
    with pytest.raises(TypeError):
        cloudpickle.dumps(g["make_writer"](driver))
    remote = g["remote_lakebase"](driver)
    assert (remote.host, remote.database, remote.user, remote.token) == (
        "ep-x.database.example.com", "worker_safety", "ram@example.com", "tok-123")
    cloudpickle.loads(cloudpickle.dumps(g["make_writer"](remote)))
