"""Clears the console's live state in Lakebase before a demo: worker cards, incidents, sparklines and
unacknowledged commands. Delta keeps the full event history, and the stream does not replay it.
Usage: uv run python tools/reset_demo.py [--yes]"""
import sys

from config import load_config
from pg_query import connect

TABLES = ("safety.device_commands", "safety.incidents", "safety.telemetry", "safety.worker_status")


def reset(conn):
    # DELETE, not TRUNCATE: the deployer's role has DML grants on the App's tables, not TRUNCATE.
    with conn.transaction(), conn.cursor() as cur:
        for table in TABLES:
            cur.execute(f"DELETE FROM {table}")


def main(yes):
    with connect(load_config()) as conn:
        with conn.cursor() as cur:
            for table in TABLES:
                cur.execute(f"SELECT count(*) FROM {table}")
                print(f"{table}: {cur.fetchone()[0]} rows")
        if not yes and input("Delete all of these rows? [y/N] ").strip().lower() != "y":
            sys.exit("aborted")
        reset(conn)
        print("console state cleared")


if __name__ == "__main__":
    main("--yes" in sys.argv)
