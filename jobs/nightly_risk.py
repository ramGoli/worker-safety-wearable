"""Nightly: Lakebase -> Delta history, shift features, train + register the next-shift risk model with MLflow,
score every worker's next shift, write the scores back to Lakebase for the console, and trim old heartbeats.

Args: --catalog workspace --lakebase-catalog worker_safety_lakebase --pg-project worker-safety
      --pg-database worker_safety [--keep-days 7]
"""
import argparse
import inspect
import os
import ssl
import sys
import time

# Databricks runs this file through exec() without __file__ (see stream/stream_to_lakebase.py)
sys.path.insert(0, os.path.dirname(os.path.abspath(inspect.currentframe().f_code.co_filename)))

import mlflow
import pandas as pd
import pg8000.dbapi  # pure Python: psycopg's binary libpq aborts on this serverless compute (OpenSSL clash)
from databricks.sdk import WorkspaceClient
from mlflow.models import infer_signature
from pyspark.sql import SparkSession

import risk_model as rm

# Lakebase table -> primary key, copied into <catalog>.worker_safety.<table>_history
COPY = {"incidents": "event_id", "telemetry": "event_id", "device_commands": "id"}


def copy_to_delta(spark, lakebase, delta):
    for table, key in COPY.items():
        spark.sql(f"CREATE TABLE IF NOT EXISTS {delta}.{table}_history AS SELECT * FROM {lakebase}.{table} WHERE false")
        spark.sql(f"""MERGE WITH SCHEMA EVOLUTION INTO {delta}.{table}_history h USING {lakebase}.{table} s
                      ON h.{key} = s.{key} WHEN MATCHED THEN UPDATE SET * WHEN NOT MATCHED THEN INSERT *""")
    spark.sql(f"CREATE OR REPLACE TABLE {delta}.workers AS SELECT * FROM {lakebase}.worker_status")


def live_shift_features(spark, delta):
    """One row per real/crew device per day, from the copied heartbeats and incidents."""
    return spark.sql(f"""
        WITH shifts AS (
          SELECT t.device_id, to_date(t.event_ts) AS shift_date, mode(w.zone) AS zone,
                 (unix_timestamp(max(t.event_ts)) - unix_timestamp(min(t.event_ts))) / 3600 AS hours,
                 avg(t.noise_db) AS avg_noise_db,
                 sum(CASE WHEN t.noise_db >= 80 THEN power(2, (t.noise_db - 85) / 3) ELSE 0 END) * 3 / 28800 * 100
                   AS noise_dose_pct,
                 coalesce(stddev(t.accel_g), 0) AS motion_std
            FROM {delta}.telemetry_history t LEFT JOIN {delta}.workers w USING (device_id)
           WHERE t.device_id NOT LIKE 'live-%'
           GROUP BY t.device_id, to_date(t.event_ts)),
        daily_incidents AS (
          SELECT device_id, to_date(event_ts) AS d, count(*) AS n FROM {delta}.incidents_history GROUP BY 1, 2)
        SELECT s.device_id, first(w.worker_name) AS worker_name, s.shift_date, coalesce(s.zone, 'Office') AS zone,
               s.hours, coalesce(s.avg_noise_db, 60) AS avg_noise_db, s.noise_dose_pct, s.motion_std,
               coalesce(sum(CASE WHEN i.d >= date_sub(s.shift_date, 7) AND i.d < s.shift_date THEN i.n END), 0)
                 AS incidents_7d,
               CASE WHEN coalesce(sum(CASE WHEN i.d = s.shift_date THEN i.n END), 0) > 0 THEN 1 ELSE 0 END
                 AS had_incident,
               'live' AS source
          FROM shifts s
          LEFT JOIN daily_incidents i ON i.device_id = s.device_id
          LEFT JOIN {delta}.workers w ON w.device_id = s.device_id
         WHERE s.hours >= 0.25
         GROUP BY s.device_id, s.shift_date, s.zone, s.hours, s.avg_noise_db, s.noise_dose_pct, s.motion_std
    """).toPandas()


def lakebase(project, database):
    w = WorkspaceClient()
    ep = next(iter(w.postgres.list_endpoints(parent=f"projects/{project}/branches/production")))
    token = w.postgres.generate_database_credential(endpoint=ep.name).token
    return pg8000.dbapi.connect(host=ep.status.hosts.host, database=database, user=w.current_user.me().user_name,
                                password=token, ssl_context=ssl.create_default_context())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog", required=True)
    ap.add_argument("--lakebase-catalog", required=True)
    ap.add_argument("--pg-project", required=True)
    ap.add_argument("--pg-database", required=True)
    ap.add_argument("--keep-days", type=int, default=7)
    args = ap.parse_args()
    spark = SparkSession.builder.getOrCreate()
    delta, lake = f"{args.catalog}.worker_safety", f"{args.lakebase_catalog}.safety"

    t0 = time.time()
    copy_to_delta(spark, lake, delta)
    print(f"copied Lakebase -> Delta in {time.time() - t0:.0f} s")

    live = live_shift_features(spark, delta)
    history = rm.simulate_history(workers=30, days=30, seed=7)
    features = pd.concat([history, live], ignore_index=True)
    spark.createDataFrame(features.astype({"shift_date": "datetime64[ns]"})).write.mode("overwrite") \
        .option("overwriteSchema", "true").saveAsTable(f"{delta}.shift_features")
    print(f"shift_features: {len(history)} simulated + {len(live)} live shifts")

    model, metrics = rm.train(features)
    mlflow.set_registry_uri("databricks-uc")
    me = WorkspaceClient().current_user.me().user_name
    mlflow.set_experiment(f"/Users/{me}/worker-safety-risk")
    with mlflow.start_run(run_name="nightly"):
        mlflow.log_params({"model": "logistic_regression", "features": ",".join(rm.FEATURES)})
        mlflow.log_metrics({k: v for k, v in metrics.items() if v == v})  # skip NaN
        x = rm.matrix(features.head(20))
        info = mlflow.sklearn.log_model(model, artifact_path="model", registered_model_name=f"{delta}.shift_risk_model",
                                        signature=infer_signature(x, model.predict_proba(x)[:, 1]), input_example=x)
    version = str(getattr(info, "registered_model_version", "") or "")
    print(f"trained: {metrics}; registered {delta}.shift_risk_model v{version}")

    workers = spark.table(f"{delta}.workers").toPandas()
    recent = spark.sql(f"""SELECT device_id, count(*) AS n FROM {delta}.incidents_history
                            WHERE event_ts >= current_timestamp() - INTERVAL 7 DAYS GROUP BY device_id""").toPandas()
    scored = rm.score(model, rm.next_shift_rows(live, workers, dict(zip(recent["device_id"], recent["n"]))))

    conn = lakebase(args.pg_project, args.pg_database)
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM safety.risk_scores")
        cur.executemany("""
            INSERT INTO safety.risk_scores (device_id, worker_name, zone, risk, top_factors, model_version,
                holdout_auc, trained_shifts, scored_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now())""",
            [(r["device_id"], r["worker_name"], r["zone"], float(r["risk"]), r["top_factors"], version,
              metrics["holdout_auc"], metrics["shifts"]) for r in scored.to_dict("records")])
        # Heartbeats are safe in Delta now; keep a week in Lakebase for the console's charts.
        cur.execute("DELETE FROM safety.telemetry WHERE event_ts < now() - make_interval(days => %s)", (args.keep_days,))
        trimmed = cur.rowcount
        conn.commit()  # scores and trim land together
    finally:
        conn.close()
    print(f"scored {len(scored)} workers; trimmed {trimmed} old heartbeats from Lakebase")


if __name__ == "__main__":
    main()
