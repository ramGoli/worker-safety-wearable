from datetime import datetime, timezone

from backend.device_events import latest_status, split_alarms, telemetry

TS = 1791997777000890  # µs; 2026-10-14T17:09:37.000890Z


def ev(event_type, **kw):
    return {"event_id": f"stick-01-ab-{event_type}", "device_id": "stick-01", "event_type": event_type,
            "event_ts": TS, "worker_name": "Alex", **kw}


def test_fall_and_sos_become_incidents_with_exact_timestamps():
    incidents, resolutions = split_alarms([ev("fall", peak_g=3.4), ev("sos")])
    assert incidents == [
        ("stick-01-ab-fall", "stick-01", "Alex", "fall", datetime(2026, 10, 14, 17, 9, 37, 890, tzinfo=timezone.utc), 3.4, None),
        ("stick-01-ab-sos", "stick-01", "Alex", "sos", datetime(2026, 10, 14, 17, 9, 37, 890, tzinfo=timezone.utc), None, None),
    ]
    assert resolutions == []


def test_im_ok_becomes_a_resolution():
    incidents, resolutions = split_alarms([ev("im_ok")])
    assert incidents == []
    assert resolutions == [("stick-01", datetime(2026, 10, 14, 17, 9, 37, 890, tzinfo=timezone.utc))]


def test_other_event_types_are_ignored():
    assert split_alarms([ev("heartbeat"), ev("boot"), ev("alert_ack")]) == ([], [])


def test_latest_status_keeps_the_newest_event_per_device():
    older = {**ev("heartbeat", battery_pct=90), "event_ts": TS - 3000000}
    newer = ev("heartbeat", battery_pct=89, rssi=-60, accel_g=1.01, zone="Warehouse")
    other = {**ev("boot"), "device_id": "stick-02"}
    rows = latest_status([newer, older, other])
    assert [r["device_id"] for r in rows] == ["stick-01", "stick-02"]
    assert rows[0] == {"device_id": "stick-01", "event_type": "heartbeat",
                       "event_ts": datetime(2026, 10, 14, 17, 9, 37, 890, tzinfo=timezone.utc),
                       "worker_name": "Alex", "accel_g": 1.01, "noise_db": None, "battery_pct": 89, "rssi": -60,
                       "free_heap": None, "fw_version": None, "zone": "Warehouse"}


def test_telemetry_is_only_heartbeats():
    rows = telemetry([ev("heartbeat", accel_g=1.0, battery_pct=88), ev("fall")])
    assert rows == [{"event_id": "stick-01-ab-heartbeat", "device_id": "stick-01",
                     "event_ts": datetime(2026, 10, 14, 17, 9, 37, 890, tzinfo=timezone.utc),
                     "accel_g": 1.0, "peak_g": None, "noise_db": None, "battery_pct": 88, "rssi": None}]


def test_incidents_carry_the_zone():
    incidents, _ = split_alarms([ev("fall", zone="Loading dock")])
    assert incidents[0][-1] == "Loading dock"


def test_worker_acks_come_from_incident_ack_events():
    from backend.device_events import worker_acks
    assert worker_acks([ev("incident_ack"), ev("heartbeat")]) == [
        ("stick-01", datetime(2026, 10, 14, 17, 9, 37, 890, tzinfo=timezone.utc))]
