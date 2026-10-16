"""The Genie space against the real workspace: its worked SQL runs, the headline question gets the right answer,
and the console's iframe URL may be framed."""
import time
import urllib.request

import pytest
from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import StatementState

from backend.genie import genie_urls
from config import load_config
from test_genie_space import space, text

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def ws():
    cfg = load_config()
    return WorkspaceClient(profile=cfg["DATABRICKS_PROFILE"]), cfg


def run(w, warehouse_id, sql):
    r = w.statement_execution.execute_statement(warehouse_id=warehouse_id, statement=sql, wait_timeout="50s")
    while r.status.state in (StatementState.PENDING, StatementState.RUNNING):
        time.sleep(2)
        r = w.statement_execution.get_statement(r.statement_id)
    return r


@pytest.mark.parametrize("example", space()["serialized_space"]["instructions"]["example_question_sqls"],
                         ids=lambda e: text(e["question"]))
def test_worked_sql_runs_on_the_warehouse(ws, example):
    w, cfg = ws
    r = run(w, cfg["WAREHOUSE_ID"], text(example["sql"]))
    assert r.status.state == StatementState.SUCCEEDED, r.status.error and r.status.error.message


def deployed_space(w):
    title = space()["title"]
    found = next((s for s in w.genie.list_spaces().spaces or [] if s.title == title), None)
    assert found, f"deploy the bundle first: no Genie space titled {title!r}"
    return found


def test_genie_names_the_highest_risk_worker(ws):
    w, cfg = ws
    sid = deployed_space(w).space_id
    top = run(w, cfg["WAREHOUSE_ID"], "SELECT worker_name FROM worker_safety_lakebase.safety.risk_scores "
                                      "ORDER BY risk DESC LIMIT 1").result.data_array[0][0]
    m = w.genie.start_conversation_and_wait(sid, "Who is most likely to get injured today?")
    q = next(a for a in m.attachments if a.query)
    rows = w.genie.get_message_attachment_query_result(sid, m.conversation_id, m.message_id,
                                                        q.attachment_id).statement_response.result.data_array
    assert rows and top in rows[0], rows


def test_console_iframe_url_can_be_framed(ws):
    w, _ = ws
    url = genie_urls(w.config.host, w.get_workspace_id(), deployed_space(w).space_id)["embed_url"]
    req = urllib.request.Request(url, headers=w.config.authenticate())
    with urllib.request.urlopen(req, timeout=30) as r:
        csp, xfo = r.headers.get("Content-Security-Policy", ""), r.headers.get("X-Frame-Options")
        assert r.status == 200 and xfo is None
    ancestors = next((d.split()[1:] for d in csp.split(";") if d.strip().startswith("frame-ancestors")), [])
    assert "*" in ancestors or any("databricksapps.com" in a for a in ancestors), csp
