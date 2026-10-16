from outbox import Outbox


def ev(event_type, seq):
    return {"event_type": event_type, "seq": seq}


def test_fifo_peek_and_drop():
    o = Outbox()
    for i in range(5):
        o.push(ev("heartbeat", i))
    assert [e["seq"] for e in o.peek(3)] == [0, 1, 2]
    o.drop(3)
    assert [e["seq"] for e in o.peek(10)] == [3, 4]


def test_long_outage_keeps_falls_and_drops_oldest_heartbeats():
    o = Outbox(maxlen=5)
    o.push(ev("fall", 0))
    for i in range(1, 20):
        o.push(ev("heartbeat", i))
    o.push(ev("sos", 20))
    kept = o.peek(10)
    assert len(o) == 5
    assert kept[0] == ev("fall", 0)
    assert kept[-1] == ev("sos", 20)
    assert [e["seq"] for e in kept[1:-1]] == [17, 18, 19]


def test_full_of_critical_events_drops_oldest():
    o = Outbox(maxlen=2)
    for i in range(3):
        o.push(ev("fall", i))
    assert [e["seq"] for e in o.peek(5)] == [1, 2]


def test_drop_more_than_present_empties():
    o = Outbox()
    o.push(ev("boot", 0))
    o.drop(5)
    assert len(o) == 0
