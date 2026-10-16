from datetime import datetime, timedelta, timezone

import transform as t

T0 = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)


def row(eid, dev, etype, sec, seq=1):
    return {"event_id": eid, "device_id": dev, "event_type": etype,
            "event_ts": T0 + timedelta(seconds=sec), "seq": seq, "landed_at": T0}


def test_latest_status_picks_newest_per_device():
    rows = [row("a", "d1", "heartbeat", 1), row("b", "d1", "heartbeat", 3), row("c", "d2", "heartbeat", 2)]
    assert [r["event_id"] for r in t.latest_status(rows)] == ["b", "c"]


def test_latest_status_survives_reboot_seq_reset():
    rows = [row("old", "d1", "heartbeat", 10, seq=900), row("new", "d1", "boot", 20, seq=1)]
    assert t.latest_status(rows)[0]["event_id"] == "new"


def test_incidents_dedupes_redelivered_events():
    rows = [row("f1", "d1", "fall", 1), row("f1", "d1", "fall", 1),
            row("h", "d1", "heartbeat", 2), row("s1", "d1", "sos", 3)]
    assert [r["event_id"] for r in t.incidents(rows)] == ["f1", "s1"]


def test_telemetry_is_only_heartbeats_deduped():
    rows = [row("h1", "d1", "heartbeat", 1), row("h1", "d1", "heartbeat", 1), row("f1", "d1", "fall", 2)]
    assert [r["event_id"] for r in t.telemetry(rows)] == ["h1"]


def test_resolutions_come_from_im_ok():
    rows = [row("f1", "d1", "fall", 1), row("ok", "d1", "im_ok", 5)]
    assert t.resolutions(rows) == [("d1", T0 + timedelta(seconds=5))]
