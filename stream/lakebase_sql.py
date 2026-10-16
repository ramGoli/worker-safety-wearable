"""Lakebase SQL used by stream_to_lakebase.py (kept separate so tests can import it without Spark)."""
UPSERT_STATUS = """
INSERT INTO safety.worker_status AS s (device_id, worker_name, last_event_type, last_event_ts, accel_g, noise_db,
    battery_pct, rssi, free_heap, fw_version, zone, landed_at, lakebase_at)
VALUES (%(device_id)s, %(worker_name)s, %(event_type)s, %(event_ts)s, %(accel_g)s, %(noise_db)s,
    %(battery_pct)s, %(rssi)s, %(free_heap)s, %(fw_version)s, %(zone)s, %(landed_at)s, now())
ON CONFLICT (device_id) DO UPDATE SET worker_name = excluded.worker_name, last_event_type = excluded.last_event_type,
    last_event_ts = excluded.last_event_ts, accel_g = excluded.accel_g, noise_db = excluded.noise_db,
    battery_pct = excluded.battery_pct, rssi = excluded.rssi, free_heap = excluded.free_heap,
    fw_version = excluded.fw_version, zone = coalesce(excluded.zone, s.zone), landed_at = excluded.landed_at,
    lakebase_at = now()
WHERE excluded.last_event_ts >= s.last_event_ts
"""
# The alarm fast lane usually inserted this incident already: confirm it once, never duplicate it.
INSERT_INCIDENT = """
INSERT INTO safety.incidents (event_id, device_id, worker_name, type, event_ts, peak_g, zone, landed_at,
    lakebase_at, confirmed_at)
VALUES (%(event_id)s, %(device_id)s, %(worker_name)s, %(event_type)s, %(event_ts)s, %(peak_g)s, %(zone)s,
    %(landed_at)s, now(), now())
ON CONFLICT (event_id) DO UPDATE SET landed_at = excluded.landed_at, confirmed_at = now()
WHERE incidents.landed_at IS NULL
"""
INSERT_TELEMETRY = """
INSERT INTO safety.telemetry (event_id, device_id, event_ts, accel_g, peak_g, noise_db, battery_pct, rssi)
VALUES (%(event_id)s, %(device_id)s, %(event_ts)s, %(accel_g)s, %(peak_g)s, %(noise_db)s, %(battery_pct)s, %(rssi)s)
ON CONFLICT (event_id) DO NOTHING
"""
RESOLVE = """
UPDATE safety.incidents i SET status = 'resolved', resolved_at = r.ts
  FROM (SELECT %s::text AS device_id, %s::timestamptz AS ts) r
 WHERE i.device_id = r.device_id AND i.status <> 'resolved' AND i.event_ts <= r.ts
"""
PRUNE = "DELETE FROM safety.telemetry WHERE event_ts < now() - interval '30 minutes'"
