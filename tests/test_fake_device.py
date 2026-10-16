import fake_device as fd

TS = 1760450000000000


def make():
    return fd.Device("fake-01", "Sam", clock_us=lambda: TS)


class FakeZB:
    def __init__(self, statuses):
        self.statuses, self.batches = list(statuses), []

    def insert(self, rows):
        self.batches.append(list(rows))
        return self.statuses.pop(0)


def test_events_survive_failed_insert_and_flush_later():
    d = make()
    d.fall()
    zb = FakeZB([503, 200])
    d.flush(zb)
    assert len(d.outbox) == 1
    d.flush(zb)
    assert len(d.outbox) == 0
    assert zb.batches[1][0]["event_type"] == "fall"


def test_schema_rejection_is_dropped_not_retried_forever():
    d = make()
    d.heartbeat()
    d.flush(FakeZB([400]))
    assert len(d.outbox) == 0


def test_command_is_shown_once_even_if_redelivered_and_acked_on_button_a():
    d = make()
    cmds = [{"id": 5, "command": "evacuate", "message": "Evacuate now"}]
    d.on_commands(cmds)
    assert (d.mode, d.message) == ("ALERT", "Evacuate now")
    d.button_a()
    d.on_commands(cmds)  # the server keeps redelivering until our ack lands
    assert d.mode == "OK"
    assert d.to_ack == [5]
    assert d.outbox.peek(10)[-1]["event_type"] == "alert_ack"


def test_two_alerts_are_both_acked():
    d = make()
    d.on_commands([{"id": 1, "command": "help_coming", "message": None},
                   {"id": 2, "command": "evacuate", "message": None}])
    assert d.message == "evacuate"
    d.button_a()
    assert d.to_ack == [1, 2]


def test_button_b_is_im_ok_and_resolves_the_fall():
    d = make()
    d.fall()
    d.button_b()
    assert [e["event_type"] for e in d.outbox.peek(10)] == ["fall", "im_ok"]
    assert d.mode == "OK"


def test_m5_acknowledges_the_fall_and_silences_the_stick_but_is_not_im_ok():
    d = make()
    d.fall()
    d.button_a()
    assert [e["event_type"] for e in d.outbox.peek(10)] == ["fall", "incident_ack"]
    assert d.mode == "ACKED"
    d.button_a()  # pressing again changes nothing
    assert len(d.outbox) == 2
    d.button_b()
    assert d.mode == "OK" and d.outbox.peek(5)[-1]["event_type"] == "im_ok"


def test_after_a_reboot_an_already_acknowledged_fall_stays_silent():
    d = make()
    d.on_server_incident("fall", acked=True)
    assert d.mode == "ACKED"


def test_alert_during_a_fall_does_not_clear_the_fall():
    d = make()
    d.fall()
    d.on_commands([{"id": 3, "command": "help_coming", "message": "Help is on the way"}])
    assert d.mode == "ALERT"
    d.button_a()  # "got it" to the supervisor: still on the floor
    assert d.mode == "FALL" and d.to_ack == [3]
    assert [e["event_type"] for e in d.outbox.peek(10)] == ["fall", "alert_ack"]
    d.button_b()
    assert d.mode == "OK"
    assert d.outbox.peek(10)[-1]["event_type"] == "im_ok"


def test_button_b_with_nothing_open_sends_nothing():
    d = make()
    d.button_b()
    assert len(d.outbox) == 0 and d.mode == "OK"


def test_event_ids_are_unique():
    d = make()
    for _ in range(3):
        d.heartbeat()
    assert len({e["event_id"] for e in d.outbox.peek(10)}) == 3


class FakeApp:
    def __init__(self, statuses):
        self.statuses, self.sent = list(statuses), []

    def send_events(self, rows):
        self.sent.append(list(rows))
        status = self.statuses.pop(0)
        if status >= 500:
            raise RuntimeError(f"app {status}")
        return status


def test_alarms_also_queue_for_the_fast_lane_but_heartbeats_do_not():
    d = make()
    d.heartbeat()
    d.fall()
    d.button_b()
    assert [e["event_type"] for e in d.to_app.peek(10)] == ["fall", "im_ok"]
    assert [e["event_type"] for e in d.outbox.peek(10)] == ["heartbeat", "fall", "im_ok"]


def test_fast_lane_keeps_alarms_until_the_app_takes_them():
    d = make()
    d.sos()
    app = FakeApp([503, 200])
    try:
        d.flush_app(app)
    except RuntimeError:
        pass
    assert len(d.to_app) == 1
    d.flush_app(app)
    assert len(d.to_app) == 0
    assert app.sent[1][0]["event_type"] == "sos"


def test_app_only_device_sends_every_event_to_the_app_and_nothing_to_zerobus():
    d = fd.Device("fake-01", "Sam", clock_us=lambda: TS, zerobus=False)
    d.heartbeat()
    d.fall()
    assert [e["event_type"] for e in d.to_app.peek(10)] == ["heartbeat", "fall"]
    assert len(d.outbox) == 0


def test_heartbeat_carries_zone_and_noise_level():
    d = fd.Device("fake-01", "Sam", clock_us=lambda: TS, zone="Workshop", noise_db=90)
    d.heartbeat()
    e = d.outbox.peek(1)[0]
    assert e["zone"] == "Workshop" and 80 < e["noise_db"] < 100


def test_device_takes_back_an_unresolved_incident_it_forgot_after_a_reboot():
    d = make()
    d.on_server_incident("fall")
    assert d.mode == "FALL" and len(d.outbox) == 0  # nothing re-sent: the server already has it
    d.button_b()
    assert d.mode == "OK" and d.outbox.peek(1)[0]["event_type"] == "im_ok"


def test_server_incident_is_ignored_while_our_im_ok_is_still_on_its_way():
    d = make()
    d.fall()
    d.button_b()  # im_ok queued, not yet sent
    d.on_server_incident("fall")
    assert d.mode == "OK"
