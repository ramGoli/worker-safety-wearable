# One Zerobus row for worker_safety.device_events. Runs on MicroPython (device) and CPython (tools):
# no typing, dataclasses or slice deletion.
EVENT_TYPES = ("boot", "heartbeat", "fall", "sos", "im_ok", "alert_ack", "incident_ack")
# 2024-01-01T00:00:00Z in microseconds: anything earlier means the clock never synced.
MIN_PLAUSIBLE_US = 1704067200000000
_Y2K_OFFSET_S = 946684800
_INF = float("inf")


def event_id(device_id, boot_id, seq):
    return "%s-%s-%d" % (device_id, boot_id, seq)


def unix_us_from_ns(ns, epoch_year):
    """Device clock (ns since the port's epoch, 1970 or 2000) -> Unix epoch microseconds."""
    us = ns // 1000
    if epoch_year == 2000:
        us += _Y2K_OFFSET_S * 1000000
    return us


def _clean_float(x):
    if x is None:
        return None
    x = float(x)
    if x != x or x == _INF or x == -_INF:
        return None
    return round(x, 3)


def _int_or_none(x):
    return None if x is None else int(x)


def make_event(device_id, boot_id, seq, event_type, event_ts_us, worker_name=None, accel_g=None,
               peak_g=None, noise_db=None, battery_pct=None, rssi=None, free_heap=None, fw_version=None,
               zone=None):
    if event_type not in EVENT_TYPES:
        raise ValueError("unknown event_type: %s" % event_type)
    if event_ts_us < MIN_PLAUSIBLE_US:
        raise ValueError("clock not synced: event_ts_us=%d" % event_ts_us)
    e = {
        "event_id": event_id(device_id, boot_id, seq),
        "device_id": device_id,
        "event_type": event_type,
        "event_ts": int(event_ts_us),
        "seq": int(seq),
        "worker_name": worker_name,
        "accel_g": _clean_float(accel_g),
        "peak_g": _clean_float(peak_g),
        "noise_db": _clean_float(noise_db),
        "battery_pct": _int_or_none(battery_pct),
        "rssi": _int_or_none(rssi),
        "free_heap": _int_or_none(free_heap),
        "fw_version": fw_version,
        "zone": zone,
    }
    return {k: v for k, v in e.items() if v is not None}
