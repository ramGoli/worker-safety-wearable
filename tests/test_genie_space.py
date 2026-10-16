"""The Genie space (resources/genie.yml) must point at tables the App really creates, and its sample questions
must have worked SQL."""
import re
from pathlib import Path

import yaml

from backend.schema import STATEMENTS

ROOT = Path(__file__).resolve().parents[1]
CATALOG = "worker_safety_lakebase"
TABLE_REF = re.compile(rf"\b{CATALOG}\.safety\.(\w+)")


def load(path):
    return yaml.safe_load((ROOT / path).read_text())


def space():
    return load("resources/genie.yml")["resources"]["genie_spaces"]["safety_genie"]


def app_tables():
    return {m.group(1) for s in STATEMENTS for m in [re.search(r"CREATE TABLE IF NOT EXISTS safety\.(\w+)", s)] if m}


def text(lines):
    return "".join(lines)


def test_space_reads_the_live_lakebase_tables():
    tables = [t["identifier"] for t in space()["serialized_space"]["data_sources"]["tables"]]
    assert tables == sorted(tables)  # Genie wants data sources in order
    assert {TABLE_REF.fullmatch(t).group(1) for t in tables} == app_tables()


def test_example_sql_only_uses_the_space_s_tables():
    examples = space()["serialized_space"]["instructions"]["example_question_sqls"]
    assert len(examples) >= 5
    for e in examples:
        assert set(TABLE_REF.findall(text(e["sql"]))) <= app_tables(), text(e["question"])


def test_ids_are_unique_hex_and_in_order():
    s = space()["serialized_space"]
    groups = [s["config"]["sample_questions"], s["instructions"]["text_instructions"],
              s["instructions"]["example_question_sqls"]]
    ids = [item["id"] for g in groups for item in g]
    assert len(ids) == len(set(ids)) and all(re.fullmatch(r"[0-9a-f]{32}", i) for i in ids)
    for g in groups:
        assert [i["id"] for i in g] == sorted(i["id"] for i in g)
    # Quoted, or the bundle's YAML 1.2 parser reads an id like 7e00...01 as the number 70
    raw = re.findall(r"^\s*- id: (.+)$", (ROOT / "resources/genie.yml").read_text(), re.M)
    assert len(raw) == len(ids) and all(re.fullmatch(r'"[0-9a-f]{32}"', v) for v in raw)


def test_sample_questions_have_worked_sql():
    samples = {text(q["question"]) for q in space()["serialized_space"]["config"]["sample_questions"]}
    worked = {text(e["question"]) for e in space()["serialized_space"]["instructions"]["example_question_sqls"]}
    assert samples and samples <= worked


def test_app_gets_the_space_id_for_the_embed():
    resources = load("resources/app.yml")["resources"]["apps"]["console"]["resources"]
    genie = next(r for r in resources if "genie_space" in r)
    assert genie["genie_space"]["space_id"] == "${resources.genie_spaces.safety_genie.id}"
    # The embedded Genie runs as the signed-in supervisor, so the App's own identity needs no data access
    assert not any("uc_securable" in r or "sql_warehouse" in r for r in resources)
    tmpl = load("app/app.yaml.tmpl")
    assert {"name": "GENIE_SPACE_ID", "valueFrom": genie["name"]} in tmpl["env"]
