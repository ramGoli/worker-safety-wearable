from fastapi.testclient import TestClient

from backend import schema
from backend.main import create_app

DEVICE = "11111111-2222-3333-4444-555555555555"
AS_DEVICE = {"X-Forwarded-Email": DEVICE}
AS_SUPERVISOR = {"X-Forwarded-Email": "boss@example.com"}


class FakeRepo:
    def __init__(self):
        self.incidents = {"f1": {"event_id": "f1", "status": "open"}}
        self.commands = []

    def state(self, telemetry_minutes=5, series_devices=None):
        return {"workers": [], "incidents": list(self.incidents.values()), "telemetry": {},
                "commands": self.commands}

    def ack_incident(self, event_id, by):
        i = self.incidents.get(event_id)
        if not i or i["status"] != "open":
            return False
        i.update(status="acknowledged", ack_by=by)
        return True

    def create_command(self, device_id, command, message, by):
        c = {"id": len(self.commands) + 1, "device_id": device_id, "command": command,
             "message": message, "created_by": by, "acked": False}
        self.commands.append(c)
        return [c["id"]]

    def pending_commands(self, device_id):
        return [{"id": c["id"], "command": c["command"], "message": c["message"]}
                for c in self.commands if c["device_id"] == device_id and not c["acked"]]

    def record_events(self, events):
        self.alarms = getattr(self, "alarms", []) + list(events)
        return len(events)

    def open_incident(self, device_id):
        return getattr(self, "open_for", {}).get(device_id)  # e.g. {"type": "fall", "worker_acked": False}

    def ack_command(self, device_id, command_id):
        for c in self.commands:
            if c["id"] == command_id and c["device_id"] == device_id:
                c["acked"] = True
                return True
        return False


def client():
    repo = FakeRepo()
    return TestClient(create_app(repo, DEVICE)), repo


def test_state_lists_incidents_with_server_time():
    c, _ = client()
    body = c.get("/api/state").json()
    assert body["incidents"][0]["event_id"] == "f1"
    assert "now" in body


def test_supervisor_ack_records_who_and_only_once():
    c, repo = client()
    assert c.post("/api/incidents/f1/ack", headers=AS_SUPERVISOR).status_code == 200
    assert repo.incidents["f1"]["ack_by"] == "boss@example.com"
    assert c.post("/api/incidents/f1/ack", headers=AS_SUPERVISOR).status_code == 404


def test_unknown_command_rejected():
    c, _ = client()
    r = c.post("/api/commands", json={"device_id": "stick-01", "command": "self_destruct"}, headers=AS_SUPERVISOR)
    assert r.status_code == 422


def test_command_redelivered_until_acked_and_ack_is_idempotent():
    c, _ = client()
    c.post("/api/commands", json={"device_id": "stick-01", "command": "evacuate", "message": "Go"},
           headers=AS_SUPERVISOR)
    first = c.get("/api/device/stick-01/commands", headers=AS_DEVICE).json()["commands"]
    again = c.get("/api/device/stick-01/commands", headers=AS_DEVICE).json()["commands"]
    assert first == again and first[0]["command"] == "evacuate"
    cid = first[0]["id"]
    assert c.post(f"/api/device/stick-01/commands/{cid}/ack", headers=AS_DEVICE).status_code == 200
    assert c.post(f"/api/device/stick-01/commands/{cid}/ack", headers=AS_DEVICE).status_code == 200
    assert c.get("/api/device/stick-01/commands", headers=AS_DEVICE).json()["commands"] == []


def test_ack_of_unknown_command_is_404():
    c, _ = client()
    assert c.post("/api/device/stick-01/commands/99/ack", headers=AS_DEVICE).status_code == 404


def test_device_routes_reject_other_callers():
    c, _ = client()
    assert c.get("/api/device/stick-01/commands", headers=AS_SUPERVISOR).status_code == 403
    assert c.get("/api/device/stick-01/commands").status_code == 403


def test_device_identity_matched_inside_forwarded_user():
    c, _ = client()
    r = c.get("/api/device/stick-01/commands", headers={"X-Forwarded-User": f"{DEVICE}@1234567890123456"})
    assert r.status_code == 200


def test_whoami_lists_identity_and_header_names_only():
    c, _ = client()
    body = c.get("/api/whoami", headers={**AS_SUPERVISOR, "X-Forwarded-Access-Token": "secret"}).json()
    assert body["identities"] == ["boss@example.com"]
    assert "x-forwarded-access-token" in body["forwarded_headers"]
    assert "secret" not in str(body)


def test_grants_quote_the_stream_role():
    assert schema.grants("ram@example.com")[0] == 'GRANT USAGE ON SCHEMA safety TO "ram@example.com"'


FALL = {"event_id": "stick-01-ab-9", "device_id": "stick-01", "event_type": "fall", "event_ts": 1791997777000890}


def test_device_posts_alarms_to_the_fast_lane():
    c, repo = client()
    r = c.post("/api/device/stick-01/events", json=[FALL], headers=AS_DEVICE)
    assert r.status_code == 200 and r.json() == {"accepted": 1}
    assert repo.alarms == [FALL]


def test_fast_lane_is_device_only():
    c, _ = client()
    assert c.post("/api/device/stick-01/events", json=[FALL], headers=AS_SUPERVISOR).status_code == 403


def test_fast_lane_rejects_rows_for_another_device():
    c, repo = client()
    r = c.post("/api/device/stick-02/events", json=[FALL], headers=AS_DEVICE)
    assert r.status_code == 422
    assert not getattr(repo, "alarms", [])


def test_state_reports_which_lanes_are_on():
    assert TestClient(create_app(FakeRepo(), DEVICE)).get("/api/state").json()["zerobus_lane"] is True
    app_only = TestClient(create_app(FakeRepo(), DEVICE, zerobus_lane=False))
    assert app_only.get("/api/state").json()["zerobus_lane"] is False


def test_state_can_limit_sparkline_series_to_some_workers():
    seen = {}

    class SeriesRepo(FakeRepo):
        def state(self, telemetry_minutes=5, series_devices=None):
            seen["series"] = series_devices
            return super().state(telemetry_minutes)

    c = TestClient(create_app(SeriesRepo(), DEVICE))
    c.get("/api/state")
    assert seen["series"] is None
    c.get("/api/state?series=stick-01,crew-07")
    assert seen["series"] == ["stick-01", "crew-07"]


def test_command_poll_tells_the_stick_about_its_unresolved_incident():
    c, repo = client()
    assert c.get("/api/device/stick-01/commands", headers=AS_DEVICE).json()["open_incident"] is None
    repo.open_for = {"stick-01": {"type": "fall", "worker_acked": True}}
    body = c.get("/api/device/stick-01/commands", headers=AS_DEVICE).json()
    assert (body["open_incident"], body["open_incident_acked"]) == ("fall", True)


def test_message_needs_text_that_fits_the_stick_screen():
    c, repo = client()
    send = lambda text: c.post("/api/commands", json={"device_id": "stick-01", "command": "message", "message": text},
                               headers=AS_SUPERVISOR)
    assert send("  Lunch in 10 min  ").status_code == 200
    assert repo.commands[-1]["message"] == "Lunch in 10 min"
    assert send("").status_code == 422
    assert send("   ").status_code == 422
    assert send("x" * 41).status_code == 422
    assert send("x" * 40).status_code == 200
