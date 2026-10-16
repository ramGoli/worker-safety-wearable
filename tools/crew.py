"""Simulated crew: virtual workers on the site map, talking to the App through the same device API as the stick.
They walk between zones, report zone-typical noise, acknowledge alerts a few seconds after they arrive, and
press I'm OK 12-30 s after a fall.
Type a key and press Enter: f = a random crew member falls, q = quit.
Usage: uv run python tools/crew.py [--count 24]"""
import argparse
import queue
import random
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "shared"))

from config import load_config
from fake_device import HEARTBEAT_S, Device, _read_keys
from zerobus_client import AppClient, TokenCache

# Typical noise per zone (≈ dB); the console draws the same zones on its site map.
ZONES = {"Assembly line": 88, "Workshop": 92, "Office": 52, "Warehouse": 72, "Loading dock": 80}
NAMES = ["Maya Chen", "Luis Ortega", "Priya Nair", "Tom Becker", "Aisha Bello", "Kenji Sato", "Sofia Rossi",
         "Omar Haddad", "Grace Kim", "Diego Silva", "Lena Novak", "Sam Okafor", "Nina Petrova", "Raj Patel",
         "Emma Dubois", "Jonas Berg", "Fatima Zahra", "Leo Martins", "Hana Yoshida", "Carlos Mendes",
         "Zoe Walker", "Ivan Kovac", "Amara Diallo", "Ben Cohen", "Mia Lopez", "Arjun Rao", "Eva Lindqvist",
         "Kofi Mensah", "Sara Haddad", "Max Weber"]
POLL_S, MOVE_CHANCE, ACK_AFTER_S, OK_AFTER_S = 4, 0.02, (2.0, 8.0), (12.0, 30.0)
DRAIN_PER_HEARTBEAT = 0.02  # ≈ 1% every 2.5 min, like the real stick with Wi-Fi always on


class CrewWorker:
    """One virtual worker; step(now, app) does whatever is due at time `now` (seconds)."""

    def __init__(self, i, rng=random, clock_us=lambda: time.time_ns() // 1000, zerobus=False):
        self.rng = rng
        zone = rng.choice(list(ZONES))
        self.device = Device("crew-%02d" % (i + 1), NAMES[i % len(NAMES)], clock_us=clock_us, zerobus=zerobus,
                             zone=zone, noise_db=ZONES[zone], fw_version="crew-sim-0.1",
                             battery_pct=rng.uniform(35, 100))
        self.next_hb = self.next_poll = 0.0
        self.ack_at = self.ok_at = None

    def step(self, now, app):
        d = self.device
        if now >= self.next_hb:
            self.next_hb = now + HEARTBEAT_S
            if self.rng.random() < MOVE_CHANCE:
                d.zone = self.rng.choice(list(ZONES))
            d.noise_db = ZONES[d.zone]  # wherever they are now
            d.battery_pct = max(1.0, d.battery_pct - DRAIN_PER_HEARTBEAT)
            d.heartbeat()
        if now >= self.next_poll:
            self.next_poll = now + POLL_S
            while d.to_ack:
                app.ack(d.to_ack[0])
                d.to_ack.pop(0)
            d.on_commands(app.pending_commands())
            d.on_server_incident(getattr(app, "open_incident", None), getattr(app, "open_incident_acked", False))
            if d.mode == "ALERT" and self.ack_at is None:
                self.ack_at = now + self.rng.uniform(*ACK_AFTER_S)
        if d.incident and self.ok_at is None:  # gets back up and presses I'm OK a little later
            self.ok_at = now + self.rng.uniform(*OK_AFTER_S)
        if self.ok_at is not None and now >= self.ok_at:
            self.ok_at = None
            d.button_b()
        if self.ack_at is not None and now >= self.ack_at:
            self.ack_at = None
            d.button_a()
            while d.to_ack:  # acknowledge straight away rather than at the next poll
                app.ack(d.to_ack[0])
                d.to_ack.pop(0)
        d.flush_app(app)


def run(worker, app, stop):
    time.sleep(worker.rng.uniform(0, HEARTBEAT_S))  # stagger so the crew doesn't post in lockstep
    while not stop.is_set():
        try:
            worker.step(time.monotonic(), app)
        except Exception as exc:  # keep walking through network blips, like a real device
            print(f"\n{worker.device.device_id}: {exc}")
        time.sleep(0.5)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=24)
    args = ap.parse_args()
    cfg = load_config()
    if not cfg.get("APP_URL"):
        sys.exit("set APP_URL in config/demo.env")
    tokens = TokenCache(cfg["WORKSPACE_HOST"], cfg["DEVICE_CLIENT_ID"], cfg["DEVICE_CLIENT_SECRET"])
    crew = [CrewWorker(i) for i in range(args.count)]
    stop = threading.Event()
    for w in crew:
        threading.Thread(target=run, args=(w, AppClient(cfg["APP_URL"], w.device.device_id, tokens), stop),
                         daemon=True).start()
    keys = queue.Queue()
    threading.Thread(target=_read_keys, args=(keys,), daemon=True).start()
    print(__doc__)
    while True:
        k = keys.get()
        if k == "q":
            stop.set()
            return
        if k == "f":
            w = random.choice(crew)
            w.device.fall()
            print(f"{w.device.worker_name} ({w.device.device_id}) fell in {w.device.zone}")


if __name__ == "__main__":
    main()
