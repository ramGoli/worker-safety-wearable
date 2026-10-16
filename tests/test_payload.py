import json

import pytest

import payload

TS = 1760450000000000  # 2025-10-14T14:33:20Z in µs, a plausible synced clock


def test_event_id_combines_device_boot_and_seq():
    assert payload.event_id("stick-01", "a1b2c3", 42) == "stick-01-a1b2c3-42"


def test_make_event_has_required_fields_and_drops_none():
    e = payload.make_event("stick-01", "a1b2c3", 7, "heartbeat", TS, worker_name="Alex", accel_g=1.01234)
    assert e == {"event_id": "stick-01-a1b2c3-7", "device_id": "stick-01", "event_type": "heartbeat",
                 "event_ts": TS, "seq": 7, "worker_name": "Alex", "accel_g": 1.012}


def test_event_ts_is_integer_microseconds_in_json():
    e = payload.make_event("d", "b", 1, "boot", TS)
    assert isinstance(e["event_ts"], int)
    assert json.loads(json.dumps([e]))[0]["event_ts"] == TS


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_floats_are_dropped_so_json_stays_valid(bad):
    e = payload.make_event("d", "b", 1, "heartbeat", TS, accel_g=bad, peak_g=bad, noise_db=bad)
    for k in ("accel_g", "peak_g", "noise_db"):
        assert k not in e
    json.dumps(e, allow_nan=False)


def test_unknown_event_type_rejected():
    with pytest.raises(ValueError, match="unknown event_type"):
        payload.make_event("d", "b", 1, "explode", TS)


def test_unsynced_clock_rejected():
    # A stick that booted without NTP reads roughly 2000-01-01.
    with pytest.raises(ValueError, match="clock not synced"):
        payload.make_event("d", "b", 1, "fall", 5000000)


def test_unix_us_from_ns_handles_both_micropython_epochs():
    assert payload.unix_us_from_ns(5000, 1970) == 5
    assert payload.unix_us_from_ns(0, 2000) == 946684800000000


def test_zone_is_carried_when_known():
    assert payload.make_event("d", "b", 1, "heartbeat", TS, zone="Assembly line")["zone"] == "Assembly line"
    assert "zone" not in payload.make_event("d", "b", 1, "heartbeat", TS)


def test_incident_ack_is_a_known_event_type():
    assert payload.make_event("d", "b", 1, "incident_ack", TS)["event_type"] == "incident_ack"
