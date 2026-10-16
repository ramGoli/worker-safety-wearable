import contextlib
import uuid

import pytest

from backend.repo import Repo
from config import load_config
from pg_query import connect

pytestmark = pytest.mark.live


class OneConnPool:
    def __init__(self, conn):
        self.conn = conn

    @contextlib.contextmanager
    def connection(self):
        yield self.conn


@pytest.fixture(scope="module")
def repo():
    conn = connect(load_config())
    yield Repo(OneConnPool(conn))
    conn.close()


def test_command_round_trip_and_idempotent_ack(repo):
    dev = "live-" + uuid.uuid4().hex[:6]
    (cid,) = repo.create_command(dev, "help_coming", "hang on", "pytest")
    assert [c["id"] for c in repo.pending_commands(dev)] == [cid]
    assert [c["id"] for c in repo.pending_commands(dev)] == [cid]
    assert repo.ack_command(dev, cid) and repo.ack_command(dev, cid)
    assert repo.pending_commands(dev) == []
    assert repo.ack_command(dev, 999999999) is False
    repo._all("DELETE FROM safety.device_commands WHERE device_id = %s", (dev,))


def test_incident_ack_once(repo):
    eid = "live-" + uuid.uuid4().hex[:6]
    repo._all("INSERT INTO safety.incidents (event_id, device_id, type, event_ts) VALUES (%s, 'live', 'fall', now())",
              (eid,))
    assert repo.ack_incident(eid, "pytest") is True
    assert repo.ack_incident(eid, "pytest") is False
    repo._all("DELETE FROM safety.incidents WHERE event_id = %s", (eid,))


def test_fast_lane_incident_is_confirmed_once_by_the_stream(repo):
    """The App writes the alarm first; the stream's later copy fills landed_at/confirmed_at without a duplicate."""
    import time
    import lakebase_sql as stream_sql
    eid = "live-" + uuid.uuid4().hex[:6]
    us = time.time_ns() // 1000
    fall = {"event_id": eid, "device_id": "live", "event_type": "fall", "event_ts": us, "worker_name": "Live"}
    assert repo.record_events([fall]) == 1
    assert repo.record_events([fall]) == 1  # a retried POST is harmless
    row = repo._all("SELECT landed_at, confirmed_at FROM safety.incidents WHERE event_id = %s", (eid,))
    assert row == [{"landed_at": None, "confirmed_at": None}]
    from datetime import datetime, timezone
    params = {**fall, "event_ts": datetime.fromtimestamp(us / 1e6, tz=timezone.utc), "peak_g": None, "zone": None,
              "landed_at": datetime.now(timezone.utc)}
    for _ in range(2):  # Zerobus is at-least-once: the stream may see it twice
        repo._all(stream_sql.INSERT_INCIDENT, params)
    rows = repo._all("SELECT landed_at, confirmed_at FROM safety.incidents WHERE event_id = %s", (eid,))
    assert len(rows) == 1 and rows[0]["landed_at"] is not None and rows[0]["confirmed_at"] is not None
    repo.record_events([{**fall, "event_type": "im_ok"}])
    assert repo._all("SELECT status FROM safety.incidents WHERE event_id = %s", (eid,)) == [{"status": "resolved"}]
    repo._all("DELETE FROM safety.incidents WHERE event_id = %s", (eid,))
    repo._all("DELETE FROM safety.worker_status WHERE device_id = 'live'")  # record_events also keeps status


def test_app_only_lane_keeps_worker_status_and_telemetry(repo):
    import time
    dev = "live-" + uuid.uuid4().hex[:6]
    us = time.time_ns() // 1000
    hb = {"event_id": dev + "-1", "device_id": dev, "event_type": "heartbeat", "event_ts": us, "seq": 1,
          "worker_name": "Live", "accel_g": 1.0, "battery_pct": 80}
    older = {**hb, "event_id": dev + "-0", "event_ts": us - 5000000, "battery_pct": 99}
    assert repo.record_events([hb]) == 1
    assert repo.record_events([older, hb]) == 2  # late delivery and a retry: neither moves status backwards
    status = repo._all("SELECT battery_pct FROM safety.worker_status WHERE device_id = %s", (dev,))
    assert status == [{"battery_pct": 80}]
    points = repo._all("SELECT count(*) AS n FROM safety.telemetry WHERE device_id = %s", (dev,))
    assert points == [{"n": 2}]
    for table in ("worker_status", "telemetry"):
        repo._all(f"DELETE FROM safety.{table} WHERE device_id = %s", (dev,))


def test_state_has_response_kpis_battery_signal_trends_and_resolve_time(repo):
    import time
    dev = "live-" + uuid.uuid4().hex[:6]
    us = time.time_ns() // 1000
    fall = {"event_id": dev + "-f", "device_id": dev, "event_type": "fall", "event_ts": us - 4000000}
    hb = {"event_id": dev + "-h", "device_id": dev, "event_type": "heartbeat", "event_ts": us - 3000000,
          "battery_pct": 77, "rssi": -61, "accel_g": 1.0}
    ok = {"event_id": dev + "-ok", "device_id": dev, "event_type": "im_ok", "event_ts": us}
    try:
        _check_kpis_and_trends(repo, dev, fall, hb, ok)
    finally:
        for table in ("incidents", "worker_status", "telemetry"):
            repo._all(f"DELETE FROM safety.{table} WHERE device_id = %s", (dev,))


def _check_kpis_and_trends(repo, dev, fall, hb, ok):
    repo.record_events([fall, hb, ok])
    row = repo._all("SELECT status, extract(epoch FROM resolved_at - event_ts) AS secs FROM safety.incidents "
                    "WHERE event_id = %s", (dev + "-f",))
    assert row[0]["status"] == "resolved" and abs(float(row[0]["secs"]) - 4.0) < 0.01
    s = repo.state()
    assert {"incidents_24h", "unresolved_now", "avg_ack_s", "avg_resolve_s"} <= set(s["kpis"])
    assert s["kpis"]["avg_resolve_s"] is not None
    assert s["trends"][dev][-1]["battery_pct"] == 77 and s["trends"][dev][-1]["rssi"] == -61


def test_every_worker_keeps_its_last_alert_even_after_a_big_broadcast(repo):
    devs = ["live-" + uuid.uuid4().hex[:6] for _ in range(25)]
    try:
        for d in devs:
            repo.create_command(d, "evacuate", "Evacuate now", "pytest")
        last = repo.state()["last_commands"]
        assert all(last[d]["command"] == "evacuate" for d in devs)
    finally:
        repo._all("DELETE FROM safety.device_commands WHERE device_id = ANY(%s)", (devs,))


def test_acknowledged_fall_stays_on_the_card_until_the_worker_says_ok(repo):
    import time
    dev = "live-" + uuid.uuid4().hex[:6]
    us = time.time_ns() // 1000
    fall = {"event_id": dev + "-f", "device_id": dev, "event_type": "fall", "event_ts": us, "worker_name": "Live"}
    try:
        repo.record_events([fall])
        worker = lambda: next(w for w in repo.state()["workers"] if w["device_id"] == dev)
        assert (worker()["open_incident"], worker()["incident_status"]) == ("fall", "open")
        assert worker()["incident_id"] == dev + "-f"  # the card's Acknowledge button needs it
        repo.ack_incident(dev + "-f", "supervisor@example.com")
        assert (worker()["open_incident"], worker()["incident_status"]) == ("fall", "acknowledged")
        assert repo.state()["kpis"]["unresolved_now"] >= 1
        repo.record_events([{**fall, "event_id": dev + "-ok", "event_type": "im_ok", "event_ts": us + 1000000}])
        assert worker()["open_incident"] is None
    finally:
        for table in ("incidents", "worker_status"):
            repo._all(f"DELETE FROM safety.{table} WHERE device_id = %s", (dev,))


def test_worker_ack_on_the_device_is_recorded_but_does_not_resolve(repo):
    import time
    dev = "live-" + uuid.uuid4().hex[:6]
    us = time.time_ns() // 1000
    fall = {"event_id": dev + "-f", "device_id": dev, "event_type": "fall", "event_ts": us, "worker_name": "Live"}
    try:
        repo.record_events([fall, {**fall, "event_id": dev + "-a", "event_type": "incident_ack", "event_ts": us + 2000000}])
        w = next(w for w in repo.state()["workers"] if w["device_id"] == dev)
        assert (w["open_incident"], w["incident_status"], w["worker_acked"]) == ("fall", "open", True)
        assert repo.open_incident(dev) == {"type": "fall", "worker_acked": True}
        inc = next(i for i in repo.state()["incidents"] if i["event_id"] == dev + "-f")
        assert inc["worker_ack_at"] is not None and inc["status"] == "open"
    finally:
        for table in ("incidents", "worker_status"):
            repo._all(f"DELETE FROM safety.{table} WHERE device_id = %s", (dev,))


def test_risk_scores_from_the_nightly_job_show_up_in_state(repo):
    dev = "live-" + uuid.uuid4().hex[:6]
    try:
        repo._all("""INSERT INTO safety.risk_scores (device_id, worker_name, zone, risk, top_factors, model_version,
                     holdout_auc, scored_at) VALUES (%s, 'Live', 'Workshop', 0.42, %s, '3', 0.86, now())""",
                  (dev, ["92 dB noise", "2 incidents in 7 days"]))
        r = repo.state()["risk_scores"][dev]
        assert (r["risk"], r["top_factors"], r["model_version"]) == (0.42, ["92 dB noise", "2 incidents in 7 days"], "3")
    finally:
        repo._all("DELETE FROM safety.risk_scores WHERE device_id = %s", (dev,))
