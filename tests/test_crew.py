import random

import crew


class FakeApp:
    def __init__(self, commands=()):
        self.commands, self.sent, self.acked = list(commands), [], []

    def send_events(self, rows):
        self.sent.extend(rows)
        return 200

    def pending_commands(self):
        return [c for c in self.commands if c["id"] not in self.acked]

    def ack(self, command_id):
        self.acked.append(command_id)
        return 200


def make_worker(i=0, seed=1):
    return crew.CrewWorker(i, rng=random.Random(seed), clock_us=lambda: 1760450000000000)


def test_crew_members_have_names_zones_and_simulated_firmware():
    workers = [make_worker(i) for i in range(24)]
    assert len({w.device.device_id for w in workers}) == 24
    assert all(w.device.zone in crew.ZONES for w in workers)
    w = workers[0]
    w.step(0.0, FakeApp())
    assert w.device.device_id == "crew-01" and w.device.fw_version.startswith("crew-sim")


def test_noise_follows_the_zone():
    w = make_worker()
    w.device.zone = "Office"
    app = FakeApp()
    w.step(0.0, app)
    office = [e["noise_db"] for e in app.sent if e["event_type"] == "heartbeat"]
    assert office and all(e < 70 for e in office)


def test_crew_acknowledges_an_alert_a_few_seconds_later_like_a_person():
    w = make_worker()
    app = FakeApp([{"id": 7, "command": "evacuate", "message": "Evacuate now"}])
    w.step(0.0, app)
    assert w.device.mode == "ALERT" and app.acked == []
    w.step(1.0, app)
    assert app.acked == []
    for t in range(2, 12):
        w.step(float(t), app)
    assert app.acked == [7] and w.device.mode == "OK"
    assert any(e["event_type"] == "alert_ack" for e in app.sent)


def test_crew_batteries_vary_and_drain():
    batteries = {make_worker(i, seed=i).device.battery_pct for i in range(24)}
    assert len(batteries) > 5 and all(30 <= b <= 100 for b in batteries)
    w = make_worker()
    start = w.device.battery_pct
    for t in range(0, 600, 3):
        w.step(float(t), FakeApp())
    assert w.device.battery_pct < start


def test_a_fallen_crew_member_says_im_ok_after_a_while():
    w = make_worker()
    app = FakeApp()
    w.device.fall()
    w.step(0.0, app)
    assert w.device.mode == "FALL"
    for t in range(1, 40):
        w.step(float(t), app)
    assert w.device.mode == "OK"
    assert [e["event_type"] for e in app.sent if e["event_type"] in ("fall", "im_ok")] == ["fall", "im_ok"]
