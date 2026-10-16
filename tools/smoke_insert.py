"""Inserts one row through Zerobus REST, then polls SQL until it is visible. Prints both latencies.
Usage: uv run python tools/smoke_insert.py"""
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "shared"))

from databricks.sdk import WorkspaceClient

import payload
from config import load_config
from zerobus_client import TokenCache, ZerobusClient, zerobus_form


def main():
    cfg = load_config()
    table = f"{cfg['CATALOG']}.{cfg['SCHEMA']}.{cfg['TABLE']}"
    tokens = TokenCache(cfg["WORKSPACE_HOST"], cfg["DEVICE_CLIENT_ID"], cfg["DEVICE_CLIENT_SECRET"],
                        extra_form=zerobus_form(cfg["WORKSPACE_ID"], table))
    e = payload.make_event("smoke", uuid.uuid4().hex[:6], 1, "boot", time.time_ns() // 1000, worker_name="Smoke Test")
    t0 = time.time()
    status = ZerobusClient(cfg["ZEROBUS_HOST"], table, tokens).insert([e])
    t1 = time.time()
    print(f"insert -> HTTP {status} in {(t1 - t0) * 1000:.0f} ms (includes token fetch)")
    if status != 200:
        sys.exit(1)
    w = WorkspaceClient(profile=cfg["DATABRICKS_PROFILE"])
    query = f"SELECT event_ts FROM {table} WHERE event_id = '{e['event_id']}'"
    while time.time() - t1 < 120:
        r = w.statement_execution.execute_statement(warehouse_id=cfg["WAREHOUSE_ID"], statement=query,
                                                    wait_timeout="30s")
        if r.result and r.result.data_array:
            print(f"visible after {time.time() - t1:.1f} s (includes SQL round trip); "
                  f"event_ts round-trip = {r.result.data_array[0][0]}")
            return
        time.sleep(0.5)
    sys.exit("row not visible after 120 s")


if __name__ == "__main__":
    main()
