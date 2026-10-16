"""Lakebase tables owned by the App (it has CAN_CONNECT_AND_CREATE). Idempotent; runs at App startup."""
STATEMENTS = [
    "CREATE SCHEMA IF NOT EXISTS safety",
    """CREATE TABLE IF NOT EXISTS safety.worker_status (
        device_id TEXT PRIMARY KEY,
        worker_name TEXT,
        last_event_type TEXT NOT NULL,
        last_event_ts TIMESTAMPTZ NOT NULL,
        accel_g DOUBLE PRECISION,
        noise_db DOUBLE PRECISION,
        battery_pct INT,
        rssi INT,
        free_heap INT,
        fw_version TEXT,
        zone TEXT,
        landed_at TIMESTAMPTZ,
        lakebase_at TIMESTAMPTZ NOT NULL DEFAULT now())""",
    """CREATE TABLE IF NOT EXISTS safety.incidents (
        event_id TEXT PRIMARY KEY,
        device_id TEXT NOT NULL,
        worker_name TEXT,
        type TEXT NOT NULL,
        event_ts TIMESTAMPTZ NOT NULL,
        peak_g DOUBLE PRECISION,
        landed_at TIMESTAMPTZ,
        lakebase_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        status TEXT NOT NULL DEFAULT 'open',
        ack_by TEXT,
        ack_at TIMESTAMPTZ,
        confirmed_at TIMESTAMPTZ,
        resolved_at TIMESTAMPTZ,
        zone TEXT)""",
    # confirmed_at = when the Zerobus copy reached Lakebase via the stream (added with the alarm fast lane)
    "ALTER TABLE safety.incidents ADD COLUMN IF NOT EXISTS confirmed_at TIMESTAMPTZ",
    # resolved_at = the worker's "I'm OK" time (added with the response-time KPIs)
    "ALTER TABLE safety.incidents ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMPTZ",
    # zone = where on the site map the worker is (added with the simulated crew)
    "ALTER TABLE safety.worker_status ADD COLUMN IF NOT EXISTS zone TEXT",
    "ALTER TABLE safety.incidents ADD COLUMN IF NOT EXISTS zone TEXT",
    # worker_ack_at = the worker pressed M5 on the stick: conscious and responding, not yet "I'm OK"
    "ALTER TABLE safety.incidents ADD COLUMN IF NOT EXISTS worker_ack_at TIMESTAMPTZ",
    "CREATE INDEX IF NOT EXISTS incidents_by_time ON safety.incidents (event_ts DESC)",
    """CREATE TABLE IF NOT EXISTS safety.telemetry (
        event_id TEXT PRIMARY KEY,
        device_id TEXT NOT NULL,
        event_ts TIMESTAMPTZ NOT NULL,
        accel_g DOUBLE PRECISION,
        peak_g DOUBLE PRECISION,
        noise_db DOUBLE PRECISION,
        battery_pct INT,
        rssi INT)""",
    "CREATE INDEX IF NOT EXISTS telemetry_by_device_time ON safety.telemetry (device_id, event_ts DESC)",
    """CREATE TABLE IF NOT EXISTS safety.device_commands (
        id BIGSERIAL PRIMARY KEY,
        device_id TEXT NOT NULL,
        command TEXT NOT NULL,
        message TEXT,
        created_by TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        delivered_at TIMESTAMPTZ,
        acked_at TIMESTAMPTZ)""",
    "CREATE INDEX IF NOT EXISTS commands_unacked ON safety.device_commands (device_id) WHERE acked_at IS NULL",
    # Filled by the nightly job (jobs/nightly_risk.py): one row per worker for their next shift.
    """CREATE TABLE IF NOT EXISTS safety.risk_scores (
        device_id TEXT PRIMARY KEY,
        worker_name TEXT,
        zone TEXT,
        risk DOUBLE PRECISION NOT NULL,
        top_factors TEXT[],
        model_version TEXT,
        holdout_auc DOUBLE PRECISION,
        trained_shifts INT,
        scored_at TIMESTAMPTZ NOT NULL)""",
]


def grants(stream_role):
    """The streaming job (running as the deployer) and tools/pg_query.py need DML on the App's tables."""
    role = '"' + stream_role.replace('"', '""') + '"'
    return [f"GRANT USAGE ON SCHEMA safety TO {role}",
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA safety TO {role}",
            f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA safety TO {role}"]


def ensure_schema(conn, stream_role):
    with conn.cursor() as cur:
        for stmt in STATEMENTS + grants(stream_role):
            cur.execute(stmt)
