"""Laptop stand-in for the M5StickC: same payloads, tokens and command handling as device/main.py.
Type a key and press Enter: f = fall, s = SOS, a = button A (got the alert), b = button B (I'm OK),
n = loud/quiet, q = quit.
Usage: uv run python tools/fake_device.py [--device-id fake-01] [--worker "Sam Fake"] [--zone Workshop]"""
import argparse
import queue
import random
import sys
import threading
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "shared"))

import payload
from config import load_config
from outbox import Outbox
from zerobus_client import AppClient, TokenCache, ZerobusClient, zerobus_form

HEARTBEAT_S, POLL_S, BATCH = 3, 2, 20
ALARM_TYPES = ("fall", "sos", "im_ok", "incident_ack")  # sent straight to the App even when Zerobus carries everything


class Device:
    def __init__(self, device_id, worker_name, clock_us=lambda: time.time_ns() // 1000, zerobus=True, zone=None,
                 noise_db=58, fw_version="fake-0.1", battery_pct=87.0):
        self.device_id, self.worker_name, self.clock_us = device_id, worker_name, clock_us
        self.boot_id = uuid.uuid4().hex[:6]
        self.seq = 0
        self.outbox = Outbox()
        self.zerobus = zerobus
        self.zone, self.noise_db, self.fw_version, self.battery_pct = zone, noise_db, fw_version, battery_pct
        self.to_app = Outbox()  # alarms only, or every event when the App is the only lane
        self.app_types = ALARM_TYPES if zerobus else payload.EVENT_TYPES
        self.mode, self.message = "OK", ""
        self.incident = None  # "FALL" or "SOS" until the worker presses B (I'm OK)
        self.incident_acked = False  # the worker pressed M5 on it: silent, but not resolved
        self.kind = ""  # command type of the latest alert ("message" = a note from the supervisor)
        self.seen, self.unacked, self.to_ack = set(), [], []

    def emit(self, event_type, **fields):
        self.seq += 1
        e = payload.make_event(self.device_id, self.boot_id, self.seq, event_type, self.clock_us(),
                               worker_name=self.worker_name, fw_version=self.fw_version, zone=self.zone, **fields)
        if self.zerobus:
            self.outbox.push(e)
        if event_type in self.app_types:
            self.to_app.push(e)

    def heartbeat(self):
        self.emit("heartbeat", accel_g=random.gauss(1.0, 0.03), battery_pct=round(self.battery_pct), rssi=-55,
                  noise_db=random.gauss(self.noise_db, 2))

    def _refresh(self):
        """A pending supervisor alert is shown first; an open fall/SOS stays underneath until B."""
        self.mode = "ALERT" if self.unacked else ("ACKED" if self.incident and self.incident_acked
                                                  else self.incident or "OK")
        if not self.unacked:
            self.message = ""

    def fall(self):
        self.incident, self.incident_acked = "FALL", False
        self.emit("fall", accel_g=1.0, peak_g=3.4)
        self._refresh()

    def sos(self):
        self.incident, self.incident_acked = "SOS", False
        self.emit("sos")
        self._refresh()

    def button_a(self):
        """Front (M5) button: "got it" to a supervisor alert, else acknowledge the fall/SOS (silence it).
        Never means I'm OK."""
        if self.unacked:
            self.emit("alert_ack")
            self.to_ack.extend(self.unacked)
            self.unacked = []
        elif self.incident and not self.incident_acked:
            self.emit("incident_ack")
            self.incident_acked = True
        self._refresh()

    def button_b(self):
        """Side button: I'm OK, I don't need help."""
        if self.incident:
            self.emit("im_ok")
            self.incident, self.incident_acked = None, False
        self._refresh()

    def on_server_incident(self, kind, acked=False):
        """The App still has an unresolved fall/SOS for us (e.g. we rebooted): show it again so B can clear it."""
        sending_ok = any(e["event_type"] == "im_ok" for e in self.to_app.peek(len(self.to_app)))
        if kind and not self.incident and not sending_ok:
            self.incident, self.incident_acked = kind.upper(), acked
            self._refresh()

    def on_commands(self, commands):
        for c in commands:
            if c["id"] in self.seen:
                continue
            self.seen.add(c["id"])
            self.unacked.append(c["id"])
            self.message = c.get("message") or c["command"]
            self.kind = c["command"]
        self._refresh()

    def flush_app(self, app):
        batch = self.to_app.peek(BATCH)
        if batch:
            app.send_events(batch)  # raises on network/5xx errors, so the batch stays queued
            self.to_app.drop(len(batch))

    def flush(self, zb):
        batch = self.outbox.peek(BATCH)
        if not batch:
            return
        status = zb.insert(batch)
        if status in (200, 400):  # 400 = schema rejection: retrying would loop forever
            self.outbox.drop(len(batch))


def _read_keys(keys):
    for line in sys.stdin:
        keys.put(line.strip().lower())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--device-id", default="fake-01")
    ap.add_argument("--worker", default="Sam Fake")
    ap.add_argument("--zone", default="Office", help="site-map zone, e.g. 'Assembly line'")
    args = ap.parse_args()
    cfg = load_config()
    table = f"{cfg['CATALOG']}.{cfg['SCHEMA']}.{cfg['TABLE']}"
    creds = (cfg["WORKSPACE_HOST"], cfg["DEVICE_CLIENT_ID"], cfg["DEVICE_CLIENT_SECRET"])
    zb = None
    if cfg.get("ZEROBUS_LANE", "on") != "off":
        zb = ZerobusClient(cfg["ZEROBUS_HOST"], table,
                           TokenCache(*creds, extra_form=zerobus_form(cfg["WORKSPACE_ID"], table)))
    app = AppClient(cfg["APP_URL"], args.device_id, TokenCache(*creds)) if cfg.get("APP_URL") else None
    if zb is None and app is None:
        sys.exit("ZEROBUS_LANE=off needs APP_URL in config/demo.env")
    dev = Device(args.device_id, args.worker, zerobus=zb is not None, zone=args.zone)
    keys = queue.Queue()
    threading.Thread(target=_read_keys, args=(keys,), daemon=True).start()
    dev.emit("boot")
    print(__doc__)
    last_hb = last_poll = 0.0
    while True:
        try:
            while not keys.empty():
                k = keys.get()
                if k == "q":
                    return
                if k == "n":
                    dev.noise_db = 58 if dev.noise_db > 80 else 96  # jackhammer next to you
                {"f": dev.fall, "s": dev.sos, "a": dev.button_a, "b": dev.button_b}.get(k, lambda: None)()
            now = time.time()
            if now - last_hb >= HEARTBEAT_S:
                last_hb = now
                dev.heartbeat()
            if app:
                dev.flush_app(app)
            if zb:
                dev.flush(zb)
            if app and now - last_poll >= POLL_S:
                last_poll = now
                while dev.to_ack:
                    app.ack(dev.to_ack[0])
                    dev.to_ack.pop(0)
                was_alert = dev.mode == "ALERT"
                dev.on_commands(app.pending_commands())
                dev.on_server_incident(app.open_incident, app.open_incident_acked)
                if dev.mode == "ALERT" and not was_alert:
                    print(f"\a\n*** {'MESSAGE' if dev.kind == 'message' else 'ALERT'} from supervisor: {dev.message}"
                          "  (type a + Enter: got it)")
            print(f"\r[{dev.mode}] seq={dev.seq} queued={len(dev.outbox) + len(dev.to_app)}   ", end="", flush=True)
        except Exception as exc:  # keep running through network blips, like the real device
            print(f"\nnet error: {exc}")
            time.sleep(1)
        time.sleep(0.2)


if __name__ == "__main__":
    main()
