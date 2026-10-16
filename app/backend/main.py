"""Supervisor console API plus the device command API for the worker-safety demo."""
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from backend.auth import caller_identities, caller_name, is_device

COMMANDS = ("help_coming", "evacuate", "all_clear", "message")
MESSAGE_MAX = 40  # about three short lines on the stick's screen
STATIC = Path(__file__).parent / "static"


class CommandIn(BaseModel):
    device_id: str  # "*" = every known device
    command: str
    message: str | None = None


def create_app(repo, device_principal, zerobus_lane=True, genie=None):
    api = FastAPI(title="Worker Safety Console")

    def require_device(request):
        if not is_device(request.headers, device_principal):
            raise HTTPException(403, "device endpoints are for the worker-safety-device principal")

    @api.get("/api/whoami")
    def whoami(request: Request):
        return {"identities": sorted(caller_identities(request.headers)),
                "forwarded_headers": sorted(k for k in request.headers.keys() if k.startswith("x-forwarded-"))}

    @api.get("/api/state")
    def state(series: str | None = None):
        """series = comma-separated device ids that get sparkline data (default: everyone)."""
        devices = [d for d in series.split(",") if d] if series is not None else None
        return {"now": datetime.now(timezone.utc).isoformat(), "zerobus_lane": zerobus_lane,
                **repo.state(series_devices=devices)}

    @api.post("/api/incidents/{event_id}/ack")
    def ack_incident(event_id: str, request: Request):
        if not repo.ack_incident(event_id, caller_name(request.headers)):
            raise HTTPException(404, "no open incident with that id")
        return {"ok": True}

    @api.post("/api/commands")
    def send_command(body: CommandIn, request: Request):
        if body.command not in COMMANDS:
            raise HTTPException(422, f"command must be one of {COMMANDS}")
        if body.command == "message":
            body.message = (body.message or "").strip()
            if not body.message or len(body.message) > MESSAGE_MAX:
                raise HTTPException(422, f"a message needs 1-{MESSAGE_MAX} characters")
        return {"ids": repo.create_command(body.device_id, body.command, body.message, caller_name(request.headers))}

    @api.get("/api/genie")
    def genie_space(request: Request):
        """URLs for the console's embedded Genie (genie = backend.genie.genie_urls(...), or None without a space)."""
        if is_device(request.headers, device_principal):
            raise HTTPException(403, "Genie is for supervisors")
        if not genie:
            raise HTTPException(404, "no Genie space configured (GENIE_SPACE_ID)")
        return genie

    @api.get("/api/device/{device_id}/commands")
    def device_commands(device_id: str, request: Request):
        require_device(request)
        # open_incident lets a stick that rebooted show its fall/SOS again, so the worker can still say I'm OK
        incident = repo.open_incident(device_id) or {}
        return {"commands": repo.pending_commands(device_id), "open_incident": incident.get("type"),
                "open_incident_acked": bool(incident.get("worker_acked"))}

    @api.post("/api/device/{device_id}/events")
    def device_events(device_id: str, events: list[dict], request: Request):
        """Device events straight into Lakebase: alarms only when the Zerobus lane is on, otherwise everything."""
        require_device(request)
        if any(e.get("device_id") != device_id for e in events):
            raise HTTPException(422, "every event must belong to the device in the path")
        return {"accepted": repo.record_events(events)}

    @api.post("/api/device/{device_id}/commands/{command_id}/ack")
    def device_ack(device_id: str, command_id: int, request: Request):
        require_device(request)
        if not repo.ack_command(device_id, command_id):
            raise HTTPException(404, "no command with that id for this device")
        return {"ok": True}

    @api.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    return api
