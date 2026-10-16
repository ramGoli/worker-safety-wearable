import ast

import render_device_config as rdc

CFG = {"DEVICE_CLIENT_ID": "id", "DEVICE_CLIENT_SECRET": "s", "WORKSPACE_HOST": "https://ws.example.com",
       "WORKSPACE_ID": "123", "ZEROBUS_HOST": "123.zerobus.example.com", "CATALOG": "c", "SCHEMA": "worker_safety",
       "TABLE": "device_events", "APP_URL": "https://app.example.com/"}


def values(text):
    return {n.targets[0].id: ast.literal_eval(n.value) for n in ast.parse(text).body}


def test_existing_wifi_is_reused_when_not_given(tmp_path):
    old = tmp_path / "config.py"
    old.write_text("WIFI_SSID = 'Home 2.4'\nWIFI_PASS = \"p'w\"\nOTHER = 1\n")
    assert rdc.existing_wifi(old) == ("Home 2.4", "p'w")
    assert rdc.existing_wifi(tmp_path / "missing.py") == (None, None)


def test_zerobus_lane_off_leaves_zerobus_host_empty():
    v = values(rdc.render({**CFG, "ZEROBUS_LANE": "off"}, "ssid", "pw", "stick-01", "Alex", True))
    assert v["ZB_HOST"] == "" and v["APP_HOST"] == "app.example.com" and v["VERIFY_TLS"] is True
    assert values(rdc.render(CFG, "ssid", "pw", "stick-01", "Alex", False))["ZB_HOST"] == "123.zerobus.example.com"


def test_zone_goes_into_the_stick_config():
    assert values(rdc.render(CFG, "ssid", "pw", "stick-01", "Alex", False, zone="Workshop"))["ZONE"] == "Workshop"
