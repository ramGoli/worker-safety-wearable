from fastapi.testclient import TestClient

from backend.genie import genie_urls
from backend.main import create_app
from test_app_api import AS_DEVICE, AS_SUPERVISOR, DEVICE, FakeRepo

HOST, WS, SPACE = "https://dbc-1.cloud.databricks.com", 1234567890123456, "01f0000000000000000000000000abcd"


def test_urls_for_the_embedded_and_the_full_genie():
    assert genie_urls(HOST + "/", WS, SPACE) == {
        "embed_url": f"{HOST}/embed/genie/rooms/{SPACE}?o={WS}",
        "open_url": f"{HOST}/genie/rooms/{SPACE}?o={WS}"}


def test_console_gets_the_genie_urls():
    c = TestClient(create_app(FakeRepo(), DEVICE, genie=genie_urls(HOST, WS, SPACE)))
    r = c.get("/api/genie", headers=AS_SUPERVISOR)
    assert r.status_code == 200 and r.json()["embed_url"].endswith(f"/embed/genie/rooms/{SPACE}?o={WS}")
    assert c.get("/api/genie", headers=AS_DEVICE).status_code == 403


def test_no_genie_space_configured():
    c = TestClient(create_app(FakeRepo(), DEVICE))
    assert c.get("/api/genie", headers=AS_SUPERVISOR).status_code == 404
