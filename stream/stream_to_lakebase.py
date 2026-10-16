"""Continuously copies new device_events rows from the Zerobus Delta table into Lakebase.

Args: --table cat.schema.table --checkpoint /Volumes/... --pg-project ID --pg-database NAME
      --trigger processing (classic, 1 s micro-batches) | available-now-loop (serverless)
"""
import argparse
import inspect
import os
import sys
import time
from datetime import timezone

# spark_python_task exec()s this file without __file__; the code object still knows its path.
sys.path.insert(0, os.path.dirname(os.path.abspath(inspect.currentframe().f_code.co_filename)))

import psycopg
from databricks.sdk import WorkspaceClient
from pyspark.sql import SparkSession
from pyspark.sql import functions as F

import transform
from lakebase_sql import INSERT_INCIDENT, INSERT_TELEMETRY, PRUNE, RESOLVE, UPSERT_STATUS

PRUNE_EVERY = 100
RETRY_FOR_S = 60


class Lakebase:
    """One driver-side connection; the OAuth password is re-minted every 45 minutes or after a failure."""

    def __init__(self, project, database):
        self.w = WorkspaceClient()
        self.endpoint = next(iter(self.w.postgres.list_endpoints(parent=f"projects/{project}/branches/production")))
        self.database = database
        self.user = self.w.current_user.me().user_name
        self.conn, self.opened_at = None, 0.0

    def connection(self):
        if self.conn is None or self.conn.closed or time.time() - self.opened_at > 45 * 60:
            self.reset()
            token = self.w.postgres.generate_database_credential(endpoint=self.endpoint.name).token
            self.conn = psycopg.connect(host=self.endpoint.status.hosts.host, dbname=self.database, user=self.user,
                                        password=token, sslmode="require", autocommit=True)
            self.opened_at = time.time()
        return self.conn

    def reset(self):
        if self.conn is not None and not self.conn.closed:
            self.conn.close()
        self.conn = None


class RemoteLakebase(Lakebase):
    """Picklable stand-in for Spark Connect, which cloudpickles foreachBatch and runs it on the server:
    carries plain strings and a short-lived token instead of the SDK client."""

    def __init__(self, host, database, user, token):
        self.host, self.database, self.user, self.token = host, database, user, token
        self.conn = None

    def connection(self):
        if self.conn is None or self.conn.closed:
            self.conn = psycopg.connect(host=self.host, dbname=self.database, user=self.user,
                                        password=self.token, sslmode="require", autocommit=True)
        return self.conn


def remote_lakebase(lb):
    token = lb.w.postgres.generate_database_credential(endpoint=lb.endpoint.name).token
    return RemoteLakebase(lb.endpoint.status.hosts.host, lb.database, lb.user, token)


def with_retry(lb, fn):
    """Ride out Lakebase blips for up to RETRY_FOR_S: a crash kills the continuous run, and its
    replacement waits minutes for a new job cluster while the console freezes."""
    deadline, delay = time.time() + RETRY_FOR_S, 1.0
    while True:
        try:
            return fn(lb.connection())
        except psycopg.OperationalError as e:
            lb.reset()
            if time.time() + delay > deadline:
                raise
            print(f"lakebase error, retrying in {delay:.0f} s: {e}")
            time.sleep(delay)
            delay = min(delay * 2, 10.0)


def prune(lb):
    try:
        lb.connection().execute(PRUNE)
    except Exception as e:  # housekeeping only: never fail the stream over it
        print(f"prune skipped: {e}")
        lb.reset()


def make_writer(lb):
    state = {"batches": 0}

    def write(batch_df, batch_id):
        rows = [r.asDict() for r in batch_df.collect()]
        if not rows:
            return
        for r in rows:
            for k in ("event_ts", "landed_at"):
                r[k] = r[k].replace(tzinfo=timezone.utc)

        def upsert(conn):
            with conn.transaction(), conn.cursor() as cur:
                for sql, params in ((UPSERT_STATUS, transform.latest_status(rows)),
                                    (INSERT_INCIDENT, transform.incidents(rows)),
                                    (INSERT_TELEMETRY, transform.telemetry(rows)),
                                    (RESOLVE, transform.resolutions(rows))):
                    if params:
                        cur.executemany(sql, params)

        with_retry(lb, upsert)
        state["batches"] += 1
        if state["batches"] % PRUNE_EVERY == 0:
            prune(lb)
        print(f"batch {batch_id}: {len(rows)} rows")

    return write


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--table", required=True)
    ap.add_argument("--checkpoint", required=True)
    ap.add_argument("--pg-project", required=True)
    ap.add_argument("--pg-database", required=True)
    ap.add_argument("--trigger", choices=["processing", "available-now-loop"], default="processing")
    args = ap.parse_args()

    spark = SparkSession.builder.getOrCreate()
    spark.conf.set("spark.sql.session.timeZone", "UTC")
    lb = Lakebase(args.pg_project, args.pg_database)
    source = (spark.readStream.table(args.table)
              .select("*", F.col("_metadata.file_modification_time").alias("landed_at")))

    def start(writer, **trigger):
        return (source.writeStream.foreachBatch(writer)
                .option("checkpointLocation", args.checkpoint).trigger(**trigger).start())

    if args.trigger == "processing":
        start(make_writer(lb), processingTime="1 second").awaitTermination()
    else:  # serverless: Spark Connect allows only availableNow, so run passes back to back
        from pyspark import cloudpickle
        cloudpickle.register_pickle_by_value(transform)  # the server can't import our workspace module
        writer, minted, passes = None, 0.0, 0
        while True:
            if writer is None or time.time() - minted > 45 * 60:  # Lakebase tokens last 1 h
                writer, minted = make_writer(remote_lakebase(lb)), time.time()
            t0 = time.time()
            start(writer, availableNow=True).awaitTermination()
            print(f"availableNow pass took {time.time() - t0:.2f} s")
            passes += 1
            if passes % PRUNE_EVERY == 0:  # each pass unpickles a fresh writer, so prune from here
                prune(lb)


if __name__ == "__main__":
    main()
