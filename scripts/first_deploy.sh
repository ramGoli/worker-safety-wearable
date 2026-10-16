#!/usr/bin/env bash
# First deploy to a new workspace. Genie refuses tables that don't exist yet, and the App creates its Lakebase tables
# when it first starts. So this deploys everything but the Genie space (from a temporary copy of the bundle with the
# same name and target, so it shares the deployment state), starts the App, then deploys the whole bundle.
# Later deploys only need scripts/deploy.sh deploy. Usage: scripts/first_deploy.sh [--validate-only]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

rsync -a --exclude .git --exclude .venv --exclude .databricks "$ROOT/" "$TMP/"
rm "$TMP/resources/genie.yml"
python3 - "$TMP" <<'PY'
import pathlib, sys
tmp = pathlib.Path(sys.argv[1])
def drop(rel, block):
    p = tmp / rel
    s = p.read_text()
    if s.count(block) != 1:
        sys.exit(f"first_deploy.sh: can't find the Genie block in {rel}; update this script to match it")
    p.write_text(s.replace(block, ""))
drop("resources/app.yml", """        - name: genie
          description: "Worker Safety Assistant, embedded in the console (it runs as the signed-in supervisor)"
          genie_space:
            name: "Worker Safety Assistant"
            space_id: ${resources.genie_spaces.safety_genie.id}
            permission: CAN_VIEW
""")
drop("app/app.yaml.tmpl", """  - name: GENIE_SPACE_ID
    valueFrom: genie
""")
PY

if [ "${1:-}" = "--validate-only" ]; then
  "$TMP/scripts/deploy.sh" validate
  "$ROOT/scripts/deploy.sh" validate
  exit
fi
echo "== 1/3 deploying everything except the Genie space"
"$TMP/scripts/deploy.sh" deploy
echo "== 2/3 starting the App, which creates its Lakebase tables"
"$TMP/scripts/deploy.sh" run console
echo "== 3/3 deploying the whole bundle, Genie space included"
"$ROOT/scripts/deploy.sh" deploy
"$ROOT/scripts/deploy.sh" run console
