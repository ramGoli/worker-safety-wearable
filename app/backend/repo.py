"""All Lakebase SQL used by the App."""
from psycopg.rows import dict_row

from backend.device_events import latest_status, split_alarms, telemetry, worker_acks
from backend.exposure import CRITERION_DB, EXCHANGE_DB, THRESHOLD_DB, noise_dose_pct

ONLINE_SECONDS = 20  # heartbeat every 3 s plus stream lag
HEARTBEAT_S = 3  # each telemetry row stands for this many seconds of noise exposure


class Repo:
    def __init__(self, pool):
        self.pool = pool

    def _all(self, sql, params=()):
        with self.pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            return cur.fetchall() if cur.description else []

    def state(self, telemetry_minutes=5, trend_minutes=30, series_devices=None):
        workers = self._all("""
            SELECT s.*, now() - s.lakebase_at < make_interval(secs => %s) AS online,
                   i.type AS open_incident, i.status AS incident_status, i.worker_ack_at IS NOT NULL AS worker_acked,
                   i.event_id AS incident_id
              FROM safety.worker_status s
              LEFT JOIN LATERAL (SELECT event_id, type, status, worker_ack_at FROM safety.incidents i  -- until I'm OK
                                  WHERE i.device_id = s.device_id AND i.status <> 'resolved'
                                  ORDER BY i.event_ts DESC LIMIT 1) i ON true
             ORDER BY s.device_id""", (ONLINE_SECONDS,))
        incidents = self._all("""
            SELECT * FROM safety.incidents
             WHERE status <> 'resolved' OR event_ts > now() - interval '1 hour'
             ORDER BY event_ts DESC LIMIT 50""")
        points = self._all("""
            SELECT device_id, event_ts, accel_g, noise_db FROM safety.telemetry
             WHERE event_ts > now() - make_interval(mins => %s) AND (%s::text[] IS NULL OR device_id = ANY(%s))
             ORDER BY event_ts""", (telemetry_minutes, series_devices, series_devices))
        # Enough rows that a site-wide broadcast (one row per worker) arrives whole; the console groups it
        commands = self._all("SELECT * FROM safety.device_commands ORDER BY created_at DESC LIMIT 200")
        risk = self._all("SELECT * FROM safety.risk_scores ORDER BY risk DESC")
        last_commands = self._all("""
            SELECT DISTINCT ON (device_id) * FROM safety.device_commands ORDER BY device_id, created_at DESC""")
        buckets = self._all("""
            SELECT device_id, date_bin('30 seconds', event_ts, timestamptz 'epoch') AS t,
                   round(avg(battery_pct))::int AS battery_pct, round(avg(rssi))::int AS rssi,
                   round(avg(noise_db)::numeric, 1)::float AS noise_db
              FROM safety.telemetry WHERE event_ts > now() - make_interval(mins => %s)
               AND (%s::text[] IS NULL OR device_id = ANY(%s))
             GROUP BY 1, 2 ORDER BY 2""", (trend_minutes, series_devices, series_devices))
        kpis = self._all("""
            SELECT count(*) FILTER (WHERE event_ts > now() - interval '24 hours') AS incidents_24h,
                   count(*) FILTER (WHERE status <> 'resolved') AS unresolved_now,
                   extract(epoch FROM avg(ack_at - event_ts)
                       FILTER (WHERE ack_at IS NOT NULL AND event_ts > now() - interval '24 hours'))::float AS avg_ack_s,
                   extract(epoch FROM avg(resolved_at - event_ts)
                       FILTER (WHERE resolved_at IS NOT NULL AND event_ts > now() - interval '24 hours'))::float
                       AS avg_resolve_s
              FROM safety.incidents""")[0]
        doses = self._all("""
            SELECT device_id, sum(power(2, (noise_db - %s) / %s::float)) AS weight FROM safety.telemetry
             WHERE noise_db >= %s AND event_ts > now() - interval '8 hours' GROUP BY 1""",
                          (CRITERION_DB, EXCHANGE_DB, THRESHOLD_DB))
        series, trends = {}, {}
        for p in points:
            series.setdefault(p["device_id"], []).append(p)
        for b in buckets:
            trends.setdefault(b["device_id"], []).append(b)
        return {"workers": workers, "incidents": incidents, "telemetry": series, "trends": trends,
                "commands": commands, "last_commands": {c["device_id"]: c for c in last_commands}, "kpis": kpis,
                "risk_scores": {r["device_id"]: r for r in risk},
                "noise_dose": {d["device_id"]: round(noise_dose_pct(d["weight"], HEARTBEAT_S), 1) for d in doses}}

    def record_events(self, events):
        """Events posted by the stick. Incidents and telemetry are keyed by event_id, so a retried POST, or the
        Zerobus copy arriving later through the stream, can't duplicate them."""
        incidents, resolutions = split_alarms(events)
        status, points, acks = latest_status(events), telemetry(events), worker_acks(events)
        with self.pool.connection() as conn, conn.transaction(), conn.cursor() as cur:
            if status:
                cur.executemany("""
                    INSERT INTO safety.worker_status AS s (device_id, worker_name, last_event_type, last_event_ts,
                        accel_g, noise_db, battery_pct, rssi, free_heap, fw_version, zone, lakebase_at)
                    VALUES (%(device_id)s, %(worker_name)s, %(event_type)s, %(event_ts)s, %(accel_g)s,
                        %(noise_db)s, %(battery_pct)s, %(rssi)s, %(free_heap)s, %(fw_version)s, %(zone)s, now())
                    ON CONFLICT (device_id) DO UPDATE SET worker_name = excluded.worker_name,
                        last_event_type = excluded.last_event_type, last_event_ts = excluded.last_event_ts,
                        accel_g = excluded.accel_g, noise_db = excluded.noise_db,
                        battery_pct = excluded.battery_pct, rssi = excluded.rssi, free_heap = excluded.free_heap,
                        fw_version = excluded.fw_version, zone = coalesce(excluded.zone, s.zone), lakebase_at = now()
                    WHERE excluded.last_event_ts >= s.last_event_ts""", status)
            if points:
                cur.executemany("""
                    INSERT INTO safety.telemetry (event_id, device_id, event_ts, accel_g, peak_g, noise_db,
                        battery_pct, rssi)
                    VALUES (%(event_id)s, %(device_id)s, %(event_ts)s, %(accel_g)s, %(peak_g)s, %(noise_db)s,
                        %(battery_pct)s, %(rssi)s)
                    ON CONFLICT (event_id) DO NOTHING""", points)
            if incidents:
                cur.executemany("""
                    INSERT INTO safety.incidents (event_id, device_id, worker_name, type, event_ts, peak_g, zone)
                    VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (event_id) DO NOTHING""", incidents)
            if acks:
                cur.executemany("""
                    UPDATE safety.incidents i SET worker_ack_at = r.ts
                      FROM (SELECT %s::text AS device_id, %s::timestamptz AS ts) r
                     WHERE i.device_id = r.device_id AND i.status <> 'resolved' AND i.worker_ack_at IS NULL
                       AND i.event_ts <= r.ts""", acks)
            if resolutions:
                cur.executemany("""
                    UPDATE safety.incidents i SET status = 'resolved', resolved_at = r.ts
                      FROM (SELECT %s::text AS device_id, %s::timestamptz AS ts) r
                     WHERE i.device_id = r.device_id AND i.status <> 'resolved' AND i.event_ts <= r.ts""",
                                resolutions)
        return len(events)

    def ack_incident(self, event_id, by):
        return bool(self._all("""
            UPDATE safety.incidents SET status = 'acknowledged', ack_by = %s, ack_at = now()
             WHERE event_id = %s AND status = 'open' RETURNING event_id""", (by, event_id)))

    def create_command(self, device_id, command, message, by):
        if device_id == "*":
            rows = self._all("""
                INSERT INTO safety.device_commands (device_id, command, message, created_by)
                SELECT device_id, %s, %s, %s FROM safety.worker_status RETURNING id""", (command, message, by))
        else:
            rows = self._all("""
                INSERT INTO safety.device_commands (device_id, command, message, created_by)
                VALUES (%s, %s, %s, %s) RETURNING id""", (device_id, command, message, by))
        return [r["id"] for r in rows]

    def pending_commands(self, device_id):
        rows = self._all("""
            UPDATE safety.device_commands SET delivered_at = coalesce(delivered_at, now())
             WHERE device_id = %s AND acked_at IS NULL
             RETURNING id, command, message, created_at""", (device_id,))
        return sorted(rows, key=lambda r: r["id"])

    def open_incident(self, device_id):
        """The device's latest unresolved incident as {"type", "worker_acked"}, or None."""
        rows = self._all("""
            SELECT type, worker_ack_at IS NOT NULL AS worker_acked FROM safety.incidents
             WHERE device_id = %s AND status <> 'resolved' ORDER BY event_ts DESC LIMIT 1""", (device_id,))
        return rows[0] if rows else None

    def ack_command(self, device_id, command_id):
        return bool(self._all("""
            UPDATE safety.device_commands SET acked_at = coalesce(acked_at, now())
             WHERE id = %s AND device_id = %s RETURNING id""", (command_id, device_id)))
