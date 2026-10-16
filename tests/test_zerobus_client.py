import json

import zerobus_client as zc


class Resp:
    def __init__(self, status, body=None):
        self.status_code, self._body = status, body or {}
        self.text = json.dumps(self._body)

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def post(self, url, **kw):
        return self.request("POST", url, **kw)

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        return self.responses.pop(0)


def token_cache(*tokens):
    return zc.TokenCache("https://ws", "id", "secret",
                         session=FakeSession([Resp(200, {"access_token": t, "expires_in": 3600}) for t in tokens]))


def test_zerobus_form_scopes_token_to_one_table():
    form = zc.zerobus_form("123", "cat.worker_safety.device_events")
    assert form["resource"] == "api://databricks/workspaces/123/zerobusDirectWriteApi"
    details = json.loads(form["authorization_details"])
    assert [d["object_full_path"] for d in details] == ["cat", "cat.worker_safety", "cat.worker_safety.device_events"]
    assert [d["object_type"] for d in details] == ["CATALOG", "SCHEMA", "TABLE"]
    assert details[2]["privileges"] == ["SELECT", "MODIFY"]


def test_token_cached_until_five_minutes_before_expiry():
    now = [1000.0]
    s = FakeSession([Resp(200, {"access_token": "t1", "expires_in": 3600}),
                     Resp(200, {"access_token": "t2", "expires_in": 3600})])
    tc = zc.TokenCache("https://ws", "id", "secret", session=s, clock=lambda: now[0])
    assert tc.get() == "t1"
    now[0] += 3600 - 301
    assert tc.get() == "t1"
    now[0] += 2
    assert tc.get() == "t2"
    assert s.calls[0][1] == "https://ws/oidc/v1/token"
    assert s.calls[0][2]["data"]["grant_type"] == "client_credentials"


def test_insert_refreshes_token_once_on_401():
    zb = FakeSession([Resp(401), Resp(200)])
    client = zc.ZerobusClient("zb.host", "c.s.t", token_cache("old", "new"), session=zb)
    assert client.insert([{"event_id": "x"}]) == 200
    assert [c[2]["headers"]["Authorization"] for c in zb.calls] == ["Bearer old", "Bearer new"]
    assert zb.calls[0][1] == "https://zb.host/zerobus/v1/tables/c.s.t/insert"


def test_insert_body_is_json_array():
    zb = FakeSession([Resp(200)])
    zc.ZerobusClient("h", "c.s.t", token_cache("t"), session=zb).insert([{"a": 1}])
    assert json.loads(zb.calls[0][2]["data"]) == [{"a": 1}]


def test_app_client_ack_treats_404_as_done():
    app = FakeSession([Resp(404, {"detail": "no pending command"})])
    assert zc.AppClient("https://app", "stick-01", token_cache("t"), session=app).ack(9) == 404
    assert app.calls[0][1] == "https://app/api/device/stick-01/commands/9/ack"


def test_app_client_lists_pending_commands():
    app = FakeSession([Resp(200, {"commands": [{"id": 1, "command": "evacuate"}]})])
    assert zc.AppClient("https://app/", "stick-01", token_cache("t"), session=app).pending_commands() == [
        {"id": 1, "command": "evacuate"}]


def test_app_client_sends_events_to_the_app():
    app = FakeSession([Resp(200, {"accepted": 1})])
    rows = [{"event_id": "e", "device_id": "stick-01", "event_type": "fall"}]
    assert zc.AppClient("https://app", "stick-01", token_cache("t"), session=app).send_events(rows) == 200
    method, url, kw = app.calls[0]
    assert (method, url, kw["json"]) == ("POST", "https://app/api/device/stick-01/events", rows)


def test_app_client_remembers_the_open_incident_from_the_poll():
    app = FakeSession([Resp(200, {"commands": [], "open_incident": "sos"})])
    client = zc.AppClient("https://app", "stick-01", token_cache("t"), session=app)
    assert client.pending_commands() == [] and client.open_incident == "sos"
