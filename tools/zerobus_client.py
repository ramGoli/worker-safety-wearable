"""CPython clients for Zerobus REST and the App's device API; same protocol as device/main.py."""
import json
import time

import requests


class TokenCache:
    """OAuth M2M token for one audience, refreshed 5 minutes before expiry or after invalidate()."""

    def __init__(self, workspace_host, client_id, client_secret, extra_form=None, session=None, clock=time.time):
        self.url = workspace_host.rstrip("/") + "/oidc/v1/token"
        self.auth = (client_id, client_secret)
        self.form = {"grant_type": "client_credentials", "scope": "all-apis", **(extra_form or {})}
        self.session = session or requests.Session()
        self.clock = clock
        self._token, self._expires_at = None, 0.0

    def get(self):
        if self._token is None or self.clock() >= self._expires_at:
            r = self.session.post(self.url, auth=self.auth, data=self.form, timeout=15)
            if r.status_code != 200:
                raise RuntimeError(f"token request failed {r.status_code}: {r.text[:300]}")
            body = r.json()
            self._token = body["access_token"]
            self._expires_at = self.clock() + int(body.get("expires_in", 3600)) - 300
        return self._token

    def invalidate(self):
        self._token = None


def zerobus_form(workspace_id, table):
    """Extra token-request fields that scope a token to Zerobus writes on one table."""
    catalog, schema, _ = table.split(".")

    def priv(privileges, object_type, path):
        return {"type": "unity_catalog_privileges", "privileges": privileges,
                "object_type": object_type, "object_full_path": path}

    details = [priv(["USE CATALOG"], "CATALOG", catalog),
               priv(["USE SCHEMA"], "SCHEMA", f"{catalog}.{schema}"),
               priv(["SELECT", "MODIFY"], "TABLE", table)]
    return {"resource": f"api://databricks/workspaces/{workspace_id}/zerobusDirectWriteApi",
            "authorization_details": json.dumps(details)}


class ZerobusClient:
    def __init__(self, zerobus_host, table, tokens, session=None):
        self.url = f"https://{zerobus_host}/zerobus/v1/tables/{table}/insert"
        self.tokens = tokens
        self.session = session or requests.Session()

    def insert(self, rows):
        """POST one batch; returns the HTTP status. Retries once with a fresh token on 401."""
        for attempt in (0, 1):
            r = self.session.post(self.url, data=json.dumps(rows, allow_nan=False), timeout=15,
                                  headers={"Authorization": f"Bearer {self.tokens.get()}",
                                           "Content-Type": "application/json"})
            if r.status_code == 401 and attempt == 0:
                self.tokens.invalidate()
                continue
            if r.status_code != 200:
                print(f"zerobus {r.status_code}: {r.text[:300]}")
            return r.status_code


class AppClient:
    def __init__(self, app_url, device_id, tokens, session=None):
        self.base = app_url.rstrip("/") + f"/api/device/{device_id}"
        self.open_incident = None  # from the last command poll: this device's unresolved fall/SOS, if any
        self.open_incident_acked = False  # ...and whether the worker already pressed M5 on it
        self.tokens = tokens
        self.session = session or requests.Session()

    def _call(self, method, path, ok=(200,), **kw):
        for attempt in (0, 1):
            r = self.session.request(method, self.base + path, timeout=10,
                                     headers={"Authorization": f"Bearer {self.tokens.get()}"}, **kw)
            if r.status_code == 401 and attempt == 0:
                self.tokens.invalidate()
                continue
            if r.status_code not in ok:
                raise RuntimeError(f"app {method} {path} -> {r.status_code}: {r.text[:200]}")
            return r

    def pending_commands(self):
        body = self._call("GET", "/commands").json()
        self.open_incident = body.get("open_incident")
        self.open_incident_acked = bool(body.get("open_incident_acked"))
        return body["commands"]

    def send_events(self, rows):
        """Device events straight to the App. 200 = stored, 422 = rejected rows; both mean stop retrying."""
        return self._call("POST", "/events", ok=(200, 422), json=rows).status_code

    def ack(self, command_id):
        """200 = acked, 404 = unknown id; both mean the device can stop retrying."""
        return self._call("POST", f"/commands/{command_id}/ack", ok=(200, 404), json={}).status_code
