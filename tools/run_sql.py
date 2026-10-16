"""Runs a .sql file statement by statement on WAREHOUSE_ID, substituting ${KEY} from config/demo.env.
Usage: uv run python tools/run_sql.py sql/01_uc.sql"""
import string
import sys
import time

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import StatementState

from config import load_config


def main(path):
    cfg = load_config()
    w = WorkspaceClient(profile=cfg["DATABRICKS_PROFILE"])
    raw = "\n".join(line for line in open(path).read().splitlines() if not line.strip().startswith("--"))
    body = string.Template(raw).substitute(cfg)
    for stmt in (s.strip() for s in body.split(";")):
        if not stmt:
            continue
        r = w.statement_execution.execute_statement(warehouse_id=cfg["WAREHOUSE_ID"], statement=stmt,
                                                    wait_timeout="50s")
        while r.status.state in (StatementState.PENDING, StatementState.RUNNING):
            time.sleep(2)
            r = w.statement_execution.get_statement(r.statement_id)
        print(f"[{r.status.state.value}] {stmt.splitlines()[0][:90]}")
        if r.status.state != StatementState.SUCCEEDED:
            sys.exit(r.status.error.message if r.status.error else str(r.status))
        if r.result and r.result.data_array:
            for row in r.result.data_array:
                print("    ", row)


if __name__ == "__main__":
    main(sys.argv[1])
