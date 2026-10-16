"""Pure row shaping for stream_to_lakebase.py, kept Spark-free so it can be unit tested."""

INCIDENT_TYPES = ("fall", "sos")


def _dedupe(rows):
    seen, out = set(), []
    for r in rows:
        if r["event_id"] not in seen:
            seen.add(r["event_id"])
            out.append(r)
    return out


def latest_status(rows):
    """Newest row per device by event_ts. Not by seq: seq restarts at 1 when the stick reboots."""
    best = {}
    for r in rows:
        cur = best.get(r["device_id"])
        if cur is None or r["event_ts"] > cur["event_ts"]:
            best[r["device_id"]] = r
    return [best[k] for k in sorted(best)]


def incidents(rows):
    return _dedupe([r for r in rows if r["event_type"] in INCIDENT_TYPES])


def telemetry(rows):
    return _dedupe([r for r in rows if r["event_type"] == "heartbeat"])


def resolutions(rows):
    """(device_id, event_ts) per im_ok: that device's open incidents up to that time are resolved."""
    return [(r["device_id"], r["event_ts"]) for r in rows if r["event_type"] == "im_ok"]
