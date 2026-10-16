#!/usr/bin/env bash
# Runs a bundle command against BUNDLE_TARGET with values from config/demo.env.
# Usage: scripts/deploy.sh validate | deploy | summary | run <resource_key>
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
set -a; source "$ROOT/config/demo.env"; set +a
if [ -f "$ROOT/app/app.yaml.tmpl" ]; then
  python3 - "$ROOT" <<'PY'
import os, pathlib, string, sys
root = pathlib.Path(sys.argv[1])
tmpl = (root / "app" / "app.yaml.tmpl").read_text()
(root / "app" / "app.yaml").write_text(string.Template(tmpl).substitute(os.environ))
PY
fi
export DATABRICKS_CONFIG_PROFILE="$DATABRICKS_PROFILE"
export BUNDLE_VAR_catalog="$CATALOG" BUNDLE_VAR_device_sp_app_id="$DEVICE_CLIENT_ID" BUNDLE_VAR_warehouse_id="$WAREHOUSE_ID"
cd "$ROOT"
exec databricks bundle "$@" -t "${BUNDLE_TARGET:-free}"
