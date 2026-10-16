-- Unity Catalog objects for the demo. ${...} values come from config/demo.env via tools/run_sql.py.
CREATE SCHEMA IF NOT EXISTS ${CATALOG}.worker_safety;

-- Zerobus target: managed Delta, unpartitioned, no DEFAULT/GENERATED/IDENTITY columns.
CREATE TABLE IF NOT EXISTS ${CATALOG}.worker_safety.device_events (
  event_id    STRING    NOT NULL,
  device_id   STRING    NOT NULL,
  event_type  STRING    NOT NULL,
  event_ts    TIMESTAMP NOT NULL,
  seq         BIGINT,
  worker_name STRING,
  accel_g     DOUBLE,
  peak_g      DOUBLE,
  noise_db    DOUBLE,
  battery_pct INT,
  rssi        INT,
  free_heap   INT,
  fw_version  STRING,
  zone        STRING
);

CREATE VOLUME IF NOT EXISTS ${CATALOG}.worker_safety.checkpoints;

GRANT USE CATALOG ON CATALOG ${CATALOG} TO `${DEVICE_CLIENT_ID}`;
GRANT USE SCHEMA ON SCHEMA ${CATALOG}.worker_safety TO `${DEVICE_CLIENT_ID}`;
GRANT SELECT, MODIFY ON TABLE ${CATALOG}.worker_safety.device_events TO `${DEVICE_CLIENT_ID}`;

SHOW GRANTS ON TABLE ${CATALOG}.worker_safety.device_events;
