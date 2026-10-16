"""Calls the App as the device service principal with each token type and reports what works.
Usage: uv run python tools/app_probe.py"""
import requests

from config import load_config
from zerobus_client import TokenCache, zerobus_form


def main():
    cfg = load_config()
    table = f"{cfg['CATALOG']}.{cfg['SCHEMA']}.{cfg['TABLE']}"
    creds = (cfg["WORKSPACE_HOST"], cfg["DEVICE_CLIENT_ID"], cfg["DEVICE_CLIENT_SECRET"])
    probes = [("all-apis token", {"Authorization": f"Bearer {TokenCache(*creds).get()}"})]
    if cfg.get("ZEROBUS_LANE", "on") != "off":  # the Zerobus-scoped token should get 401 from the App
        zb = TokenCache(*creds, extra_form=zerobus_form(cfg["WORKSPACE_ID"], table))
        probes.append(("zerobus token", {"Authorization": f"Bearer {zb.get()}"}))
    base = cfg["APP_URL"].rstrip("/")
    for label, headers in probes + [("no token", {})]:
        who = requests.get(base + "/api/whoami", headers=headers, timeout=15, allow_redirects=False)
        cmd = requests.get(base + "/api/device/probe-01/commands", headers=headers, timeout=15, allow_redirects=False)
        print(f"{label:15} whoami={who.status_code} {who.text[:160]!r}  device-commands={cmd.status_code}")


if __name__ == "__main__":
    main()
