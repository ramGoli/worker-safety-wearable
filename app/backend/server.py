"""Databricks Apps entry point: wires the Lakebase-backed Repo into the API."""
import os

import uvicorn
from databricks.sdk import WorkspaceClient

from backend.db import make_pool
from backend.genie import genie_urls
from backend.main import create_app
from backend.repo import Repo
from backend.schema import ensure_schema

pool = make_pool()
with pool.connection() as conn:
    ensure_schema(conn, os.environ["STREAM_PG_ROLE"])
space_id = os.environ.get("GENIE_SPACE_ID")
if space_id:
    w = WorkspaceClient()
    genie = genie_urls(w.config.host, os.environ.get("DATABRICKS_WORKSPACE_ID") or w.get_workspace_id(), space_id)
else:
    genie = None
app = create_app(Repo(pool), os.environ["DEVICE_PRINCIPAL"],
                 zerobus_lane=os.environ.get("ZEROBUS_LANE", "on") != "off", genie=genie)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("DATABRICKS_APP_PORT", "8000")))
