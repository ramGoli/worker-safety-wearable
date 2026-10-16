"""Events the stick posts straight to the App: only falls, SOS and "I'm OK" when the Zerobus lane is on (so the
console needn't wait for Delta and the stream), or every event when the App is the only lane.
Pure helpers, so they can be unit tested without Postgres."""
from datetime import datetime, timedelta, timezone

INCIDENT_TYPES = ("fall", "sos")
STATUS_FIELDS = ("worker_name", "accel_g", "noise_db", "battery_pct", "rssi", "free_heap", "fw_version", "zone")
TELEMETRY_FIELDS = ("accel_g", "peak_g", "noise_db", "battery_pct", "rssi")


def _ts(us):
    """Unix µs -> aware datetime, exactly (a float division would lose microseconds)."""
    return datetime.fromtimestamp(us // 1000000, tz=timezone.utc) + timedelta(microseconds=us % 1000000)


def split_alarms(events):
    """Zerobus-format rows -> (incident rows, (device_id, ts) resolutions); other event types are ignored."""
    incidents, resolutions = [], []
    for e in events:
        if e["event_type"] in INCIDENT_TYPES:
            incidents.append((e["event_id"], e["device_id"], e.get("worker_name"), e["event_type"],
                              _ts(e["event_ts"]), e.get("peak_g"), e.get("zone")))
        elif e["event_type"] == "im_ok":
            resolutions.append((e["device_id"], _ts(e["event_ts"])))
    return incidents, resolutions


def worker_acks(events):
    """(device_id, ts) per incident_ack: the worker pressed M5 on a fall/SOS (responsive, but not resolved)."""
    return [(e["device_id"], _ts(e["event_ts"])) for e in events if e["event_type"] == "incident_ack"]


def latest_status(events):
    """Newest event per device by event_ts (not seq: it restarts at 1 when the stick reboots)."""
    best = {}
    for e in events:
        cur = best.get(e["device_id"])
        if cur is None or e["event_ts"] > cur["event_ts"]:
            best[e["device_id"]] = e
    return [{"device_id": d, "event_type": e["event_type"], "event_ts": _ts(e["event_ts"]),
             **{k: e.get(k) for k in STATUS_FIELDS}} for d, e in sorted(best.items())]


def telemetry(events):
    return [{"event_id": e["event_id"], "device_id": e["device_id"], "event_ts": _ts(e["event_ts"]),
             **{k: e.get(k) for k in TELEMETRY_FIELDS}} for e in events if e["event_type"] == "heartbeat"]
