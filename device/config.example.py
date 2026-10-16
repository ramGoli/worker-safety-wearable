# Copy to device/config.py (gitignored), or better, generate it from config/demo.env:
#   uv run python tools/render_device_config.py --ssid ... --password ... --worker "Your Name"
WIFI_SSID = "your-2.4GHz-hotspot"
WIFI_PASS = "your-password"
CLIENT_ID = "worker-safety-device application id"
CLIENT_SECRET = "worker-safety-device OAuth secret"
WS_HOST = "<your-workspace>.cloud.databricks.com"
WS_ID = "<workspace id>"
ZB_HOST = ""  # empty = every event goes straight to the App (Free Edition)
TABLE = "workspace.worker_safety.device_events"
APP_HOST = "<app host, from APP_URL>"  # empty disables command polling
DEVICE_ID = "stick-01"
WORKER = "Your Name"
ZONE = "Assembly line"
VERIFY_TLS = False  # True once /flash/roots.pem is on the device
