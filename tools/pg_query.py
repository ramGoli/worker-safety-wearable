"""Runs SQL on the demo's Lakebase database as you (the deployer).
Usage: uv run python tools/pg_query.py "SELECT ...\""""
import sys

import psycopg
from databricks.sdk import WorkspaceClient

from config import load_config


def connect(cfg):
    w = WorkspaceClient(profile=cfg["DATABRICKS_PROFILE"])
    ep = next(iter(w.postgres.list_endpoints(parent=f"projects/{cfg['PG_PROJECT']}/branches/production")))
    token = w.postgres.generate_database_credential(endpoint=ep.name).token
    return psycopg.connect(host=ep.status.hosts.host, dbname=cfg["PG_DATABASE"], user=w.current_user.me().user_name,
                           password=token, sslmode="require", autocommit=True)


def main(sql):
    with connect(load_config()) as conn, conn.cursor() as cur:
        cur.execute(sql)
        if cur.description:
            print(" | ".join(c.name for c in cur.description))
            for row in cur.fetchall():
                print(" | ".join(str(v) for v in row))


if __name__ == "__main__":
    main(sys.argv[1])
