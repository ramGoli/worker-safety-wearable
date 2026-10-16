# main.py - Worker Safety wearable: M5StickC Plus2 on UIFlow2 (MicroPython).
# Needs config.py, fall.py, payload.py and outbox.py (plus roots.pem if VERIFY_TLS) in /flash.
import sys
sys.path.append("/flash")

import M5
from M5 import *
import _thread
import array
import gc
import json
import math
import network
import ntptime
import os
import socket
import ssl
import time
import ubinascii

import config
import payload
from fall import FallDetector
from noise import ac_dbfs
from outbox import Outbox

FW = "mpy-0.1"
HEARTBEAT_S, POLL_S, BATCH = 3, 2, 20
NOISE_EVERY_MS, NOISE_FRESH_MS = 3000, 10000
NOISE_CAL_DB = getattr(config, "NOISE_CAL_DB", 92)  # dBFS -> ≈ dB; tune against a phone sound-meter app
NOISE_BUF = bytearray(1600 * 2)  # 100 ms of 16-bit samples at 16 kHz
BOOT_ID = ubinascii.hexlify(os.urandom(3)).decode()
EPOCH_YEAR = time.gmtime(0)[0]

lock = _thread.allocate_lock()
outbox = Outbox(200)
to_app = Outbox(200)  # alarms only, or every event when the App is the only lane
ALARM_TYPES = ("fall", "sos", "im_ok", "incident_ack")
S = {"mode": "SYNC", "msg": "", "net": "...", "mag": 1.0, "seq": 0, "dirty": True, "noise": None, "noise_at": 0,
     "incident": None, "pressed": "", "pressed_at": 0, "kind": "", "incident_acked": False}  # incident = "FALL"/"SOS" until the worker presses B
seen, unacked, to_ack = set(), [], []
wlan = network.WLAN(network.STA_IF)
CADATA = None
if config.VERIFY_TLS:
    with open("/flash/roots.pem", "rb") as f:
        CADATA = f.read()


def now_us():
    try:
        ns = time.time_ns()
    except AttributeError:  # older MicroPython builds: whole seconds only
        ns = int(time.time()) * 1000000000
    return payload.unix_us_from_ns(ns, EPOCH_YEAR)


def urlencode(s):
    out = ""
    for b in s.encode():
        c = chr(b)
        safe = 48 <= b <= 57 or 65 <= b <= 90 or 97 <= b <= 122 or c in "-_.~"
        out += c if safe else "%%%02X" % b
    return out


class Https:
    """Minimal HTTP/1.1 keep-alive client, so we don't pay a TLS handshake per request."""

    def __init__(self, host):
        self.host, self.s = host, None

    def close(self):
        try:
            self.s.close()
        except Exception:
            pass
        self.s = None

    def _open(self):
        addr = socket.getaddrinfo(self.host, 443, 0, socket.SOCK_STREAM)[0][-1]
        raw = socket.socket()
        raw.settimeout(10)
        raw.connect(addr)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        if CADATA:
            ctx.verify_mode = ssl.CERT_REQUIRED
            ctx.load_verify_locations(cadata=CADATA)
        else:
            ctx.verify_mode = ssl.CERT_NONE
        self.s = ctx.wrap_socket(raw, server_hostname=self.host)

    def _readn(self, n):
        b = b""
        while len(b) < n:
            chunk = self.s.read(n - len(b))
            if not chunk:
                raise OSError("eof")
            b += chunk
        return b

    def _response(self):
        line = self.s.readline()
        if not line:
            raise OSError("no response")
        code, length, chunked, close = int(line.split()[1]), 0, False, False
        while True:
            h = self.s.readline()
            if not h or h == b"\r\n":
                break
            k, _, v = h.decode().partition(":")
            k, v = k.strip().lower(), v.strip().lower()
            if k == "content-length":
                length = int(v)
            elif k == "transfer-encoding" and "chunked" in v:
                chunked = True
            elif k == "connection" and v == "close":
                close = True
        body = b""
        if chunked:
            while True:
                size = int(self.s.readline().split(b";")[0].strip(), 16)
                if size == 0:
                    self.s.readline()
                    break
                body += self._readn(size)
                self.s.readline()
        elif length:
            body = self._readn(length)
        if close:
            self.close()
        return code, body

    def req(self, method, path, body=b"", headers=None):
        if isinstance(body, str):
            body = body.encode()
        head = "%s %s HTTP/1.1\r\nHost: %s\r\nContent-Length: %d\r\n" % (method, path, self.host, len(body))
        for k in headers or {}:
            head += "%s: %s\r\n" % (k, headers[k])
        for attempt in (0, 1):
            try:
                if self.s is None:
                    self._open()
                self.s.write(head.encode() + b"\r\n" + body)
                return self._response()
            except Exception:
                self.close()
                if attempt:
                    raise


ws = Https(config.WS_HOST)
zb = Https(config.ZB_HOST) if config.ZB_HOST else None  # empty ZB_HOST = the App is the only lane
app = Https(config.APP_HOST) if config.APP_HOST else None
if not (zb or app):
    raise ValueError("config.py needs ZB_HOST or APP_HOST")
APP_TYPES = ALARM_TYPES if zb else payload.EVENT_TYPES
TOK = {"zb": [None, 0], "app": [None, 0]}


def fetch_token(kind):
    form = "grant_type=client_credentials&scope=all-apis"
    if kind == "zb":
        cat, sch, _ = config.TABLE.split(".")

        def priv(privileges, object_type, path):
            return {"type": "unity_catalog_privileges", "privileges": privileges,
                    "object_type": object_type, "object_full_path": path}

        details = [priv(["USE CATALOG"], "CATALOG", cat), priv(["USE SCHEMA"], "SCHEMA", cat + "." + sch),
                   priv(["SELECT", "MODIFY"], "TABLE", config.TABLE)]
        form += "&resource=" + urlencode("api://databricks/workspaces/%s/zerobusDirectWriteApi" % config.WS_ID)
        form += "&authorization_details=" + urlencode(json.dumps(details))
    basic = ubinascii.b2a_base64((config.CLIENT_ID + ":" + config.CLIENT_SECRET).encode()).decode().strip()
    code, body = ws.req("POST", "/oidc/v1/token", form,
                        {"Authorization": "Basic " + basic, "Content-Type": "application/x-www-form-urlencoded"})
    ws.close()
    if code != 200:
        raise OSError("token %s HTTP %d %s" % (kind, code, body[:120]))
    j = json.loads(body)
    TOK[kind] = [j["access_token"], time.time() + int(j.get("expires_in", 3600)) - 300]


def bearer(kind):
    if TOK[kind][0] is None or time.time() > TOK[kind][1]:
        fetch_token(kind)
    return {"Authorization": "Bearer " + TOK[kind][0]}


def battery():
    try:
        return Power.getBatteryLevel()
    except Exception:
        return None


def rssi():
    try:
        return wlan.status("rssi")
    except Exception:
        return None


def fresh_noise():
    if S["noise"] is None or time.ticks_diff(time.ticks_ms(), S["noise_at"]) > NOISE_FRESH_MS:
        return None  # stale while the buzzer had the audio hardware
    return S["noise"]


class NoiseMeter:
    """Records ~100 ms of sound every few seconds without blocking the loop. The mic and the buzzer share the
    stick's audio hardware, so the meter is switched off while the stick beeps."""

    def __init__(self):
        self.on = self.recording = False
        self.skip, self.next_ms = 0, 0

    def mic_on(self):
        if not self.on:
            Speaker.end()
            Mic.begin()
            self.on, self.skip = True, 1  # the first recording after start is a loud click

    def mic_off(self):
        if self.on:
            Mic.end()
            Speaker.begin()
            self.on = self.recording = False

    def step(self, now):
        if not self.on:
            return
        if self.recording:
            if Mic.isRecording():
                return
            self.recording = False
            level = ac_dbfs(array.array("h", NOISE_BUF), step=4) + NOISE_CAL_DB
            if self.skip:
                self.skip -= 1
            else:
                S["noise"], S["noise_at"] = level, now
        elif time.ticks_diff(now, self.next_ms) >= 0:
            self.next_ms = time.ticks_add(now, 0 if self.skip else NOISE_EVERY_MS)
            Mic.record(NOISE_BUF, 16000, False)
            self.recording = True


def emit(event_type, peak=None):
    with lock:
        S["seq"] += 1
        e = payload.make_event(config.DEVICE_ID, BOOT_ID, S["seq"], event_type, now_us(),
                               worker_name=config.WORKER, accel_g=S["mag"], peak_g=peak, noise_db=fresh_noise(),
                               zone=getattr(config, "ZONE", None),
                               battery_pct=battery(), rssi=rssi(), free_heap=gc.mem_free(), fw_version=FW)
        if zb:
            outbox.push(e)
        if app and event_type in APP_TYPES:
            to_app.push(e)


def wifi_up():
    if wlan.isconnected():
        return
    wlan.active(True)
    try:
        wlan.config(pm=wlan.PM_NONE)  # no Wi-Fi power save: lower latency
    except Exception:
        pass
    wlan.connect(config.WIFI_SSID, config.WIFI_PASS)
    for _ in range(150):
        if wlan.isconnected():
            return
        time.sleep_ms(100)
    raise OSError("wifi timeout")


def send_app():
    with lock:
        batch = to_app.peek(BATCH)
    if not batch:
        return
    h = bearer("app")
    h["Content-Type"] = "application/json"
    code, _ = app.req("POST", "/api/device/%s/events" % config.DEVICE_ID, json.dumps(batch), h)
    if code in (200, 422):  # 422 = rejected rows: drop instead of retrying forever
        with lock:
            to_app.drop(len(batch))
        S["net"] = "ok"
        return
    if code == 401:
        TOK["app"][0] = None
    S["net"] = "ev%d" % code


def send_outbox():
    with lock:
        batch = outbox.peek(BATCH)
    if not batch:
        return
    h = bearer("zb")
    h["Content-Type"] = "application/json"
    code, body = zb.req("POST", "/zerobus/v1/tables/%s/insert" % config.TABLE, json.dumps(batch), h)
    if code in (200, 400):  # 400 = schema rejection: drop instead of retrying forever
        if code == 400:
            print("zerobus rejected batch:", body[:300])
        with lock:
            outbox.drop(len(batch))
        S["net"] = "ok" if code == 200 else "zb400"
        return
    if code == 401:
        TOK["zb"][0] = None
    S["net"] = "zb%d" % code


def poll():
    while to_ack:
        h = bearer("app")
        h["Content-Type"] = "application/json"
        code, _ = app.req("POST", "/api/device/%s/commands/%d/ack" % (config.DEVICE_ID, to_ack[0]), "{}", h)
        if code not in (200, 404):
            if code == 401:
                TOK["app"][0] = None
            S["net"] = "ack%d" % code
            return
        with lock:
            to_ack.pop(0)
    code, body = app.req("GET", "/api/device/%s/commands" % config.DEVICE_ID, b"", bearer("app"))
    if code != 200:
        if code == 401:
            TOK["app"][0] = None
        S["net"] = "app%d" % code
        return
    reply = json.loads(body)
    for c in reply.get("commands", []):
        if c["id"] in seen:
            continue
        seen.add(c["id"])
        with lock:
            unacked.append(c["id"])
        S["msg"] = c.get("message") or c["command"]
        S["kind"] = c["command"]  # "message" gets a calmer screen than an alert
    # The App still has an unresolved fall/SOS for us (e.g. we rebooted): show it again so the worker can say OK.
    kind = reply.get("open_incident")
    if kind and not S["incident"]:
        with lock:
            sending_ok = any(e["event_type"] == "im_ok" for e in to_app.peek(len(to_app)))
        if not sending_ok:
            S["incident"], S["incident_acked"] = kind.upper(), bool(reply.get("open_incident_acked"))
    refresh()


def net_loop():
    last_hb = last_poll = 0
    last_heap = time.time()
    while True:
        try:
            wifi_up()
            t = time.time()
            if t - last_hb >= HEARTBEAT_S:
                last_hb = t
                emit("heartbeat")
            if app:
                send_app()  # before Zerobus: this is what puts an alarm on screen in ~1 s
            if zb:
                send_outbox()
            if app and t - last_poll >= POLL_S:
                last_poll = t
                poll()
            if t - last_heap >= 60:
                last_heap = t
                print("heap", gc.mem_free(), "queued", len(outbox) + len(to_app), "net", S["net"])
        except Exception as e:
            print("net:", e)
            S["net"] = "err"
            if zb:
                zb.close()
            if app:
                app.close()
            time.sleep(1)
        gc.collect()
        time.sleep_ms(200)


COLORS = {"SYNC": 0x333333, "OK": 0x006600, "FALL": 0xAA0000, "SOS": 0xAA0000, "ALERT": 0xCC6600, "ACKED": 0x333355}
TITLES = {"SYNC": "STARTING", "OK": "ALL OK", "FALL": "FALL!", "SOS": "SOS SENT", "ALERT": "ALERT", "ACKED": "REPORTED"}
# Buttons by what's printed/visible: A is the front "M5" button, B the small side button (not power).
SUBTITLES = {"SYNC": "Wi-Fi + clock...", "FALL": "M5 = got it. Side btn = I'm OK", "SOS": "Help called. Side btn = I'm OK",
             "ACKED": "Side btn = I'm OK. Hold M5 = SOS"}


TEXT_W, BODY_Y, BODY_LINE = 228, 40, 22  # pixels: text area width, first body line, line pitch


def wrap_px(text, max_lines):
    """Wraps by measured pixel width in the current font; the last line ends with ... if it doesn't all fit."""
    lcd, lines, line = M5.Lcd, [], ""
    for word in text.split():
        trial = (line + " " + word).strip()
        if line and lcd.textWidth(trial) > TEXT_W:
            lines.append(line)
            line = word
        else:
            line = trial
    lines.append(line)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] += "..."
    return lines


def draw():
    mode = S["mode"]
    note = mode == "ALERT" and S["kind"] == "message"
    bg = 0x003399 if note else COLORS[mode]
    lcd = M5.Lcd
    lcd.fillScreen(bg)
    lcd.setTextColor(0xFFFFFF, bg)
    lcd.setFont(lcd.FONTS.ASCII7)
    lcd.setTextSize(3)
    lcd.drawString("MESSAGE" if note else TITLES[mode], 6, 6)
    hint = ""
    if mode == "ALERT":
        sub, hint = S["msg"], "M5 = got it"  # the hint gets its own line instead of crowding the message
    else:
        sub = SUBTITLES.get(mode) or config.WORKER
    lcd.setFont(lcd.FONTS.DejaVu18)  # proportional: about twice the text of the old size-2 font, less cramped
    lcd.setTextSize(1)
    y = BODY_Y
    for line in wrap_px(sub, 2 if hint else 3):
        lcd.drawString(line, 6, y)
        y += BODY_LINE
    if hint:
        lcd.setFont(lcd.FONTS.DejaVu12)
        lcd.drawString(hint, 6, y + 2)
    lcd.setFont(lcd.FONTS.ASCII7)
    lcd.setTextSize(1)
    if S["pressed"] and time.ticks_diff(time.ticks_ms(), S["pressed_at"]) < 2000:
        lcd.drawString("pressed: " + S["pressed"], 6, 108)  # so it's obvious which physical button is which
    level = fresh_noise()
    lcd.drawString("%s bat %s%% %s net %s q%d" % (config.DEVICE_ID, battery(), "--dB" if level is None else "%ddB" % level,
                                                  S["net"], len(outbox) + len(to_app)), 6, 122)


def refresh():
    """A pending supervisor alert is shown first; an open fall/SOS stays underneath until B."""
    S["mode"] = "ALERT" if unacked else ("ACKED" if S["incident"] and S["incident_acked"] else S["incident"] or "OK")
    if not unacked:
        S["msg"] = ""
    S["dirty"] = True


def raise_incident(kind, peak=None):
    S["incident"], S["incident_acked"] = kind, False
    emit("fall" if kind == "FALL" else "sos", peak)
    refresh()


def button_a():
    """Front (M5) button: "got it" to a supervisor alert, else acknowledge the fall/SOS: red and beeping stop.
    Never means I'm OK."""
    if unacked:
        emit("alert_ack")
        with lock:
            to_ack.extend(unacked)
            unacked.clear()
    elif S["incident"] and not S["incident_acked"]:
        emit("incident_ack")
        S["incident_acked"] = True
    refresh()


def button_b():
    """Side button: I'm OK, I don't need help."""
    if S["incident"]:
        emit("im_ok")
        S["incident"], S["incident_acked"] = None, False
    refresh()


def boot():
    M5.begin()
    M5.Lcd.setRotation(1)
    draw()
    while True:  # payload.make_event refuses timestamps from an unsynced clock
        try:
            wifi_up()
            ntptime.settime()
            if now_us() >= payload.MIN_PLAUSIBLE_US:
                break
        except Exception as e:
            print("boot:", e)
        time.sleep(2)
    S.update(mode="OK", dirty=True)
    emit("boot")
    _thread.stack_size(16384)  # TLS needs a big thread stack
    _thread.start_new_thread(net_loop, ())


def main():
    boot()
    detector = FallDetector()
    noise = NoiseMeter()
    a_down, a_long, last_draw, last_beep = None, False, 0, 0
    b_down = None
    while True:
        M5.update()
        now = time.ticks_ms()
        ax, ay, az = Imu.getAccel()
        g = math.sqrt(ax * ax + ay * ay + az * az)
        S["mag"] = g
        if detector.step(g, now) and not S["incident"]:
            raise_incident("FALL", detector.peak_g)
        if BtnA.isPressed():
            if a_down is None:
                a_down, a_long = now, False
                S.update(pressed="M5 button (A)", pressed_at=now, dirty=True)
            elif not a_long and time.ticks_diff(now, a_down) > 1500:
                a_long = True
                raise_incident("SOS")
        elif a_down is not None:
            if not a_long:
                button_a()
            a_down = None
        # B timed by hand like A: M5's own hold threshold is ~0.5 s, so a slow "I'm OK" press would be lost.
        if BtnB.isPressed():
            if b_down is None:
                b_down = now
                S.update(pressed="side button (B)", pressed_at=now, dirty=True)
        elif b_down is not None:
            button_b()  # I'm OK, however long it was held
            b_down = None
        if S["mode"] in ("OK", "ACKED"):  # quiet screens: the mic is free for the noise meter
            noise.mic_on()
            noise.step(now)
        else:
            noise.mic_off()  # the buzzer needs the audio hardware
            if time.ticks_diff(now, last_beep) > 1000:
                last_beep = now
                Speaker.tone(2200 if S["mode"] == "ALERT" else 3200, 150)
        if S["dirty"] or time.ticks_diff(now, last_draw) > 2000:
            S["dirty"] = False
            last_draw = now
            draw()
        time.sleep_ms(10)


main()
