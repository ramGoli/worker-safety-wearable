"""Reads config/demo.env (KEY=VALUE lines); environment variables with the same name win."""
import os
from pathlib import Path

ENV_FILE = Path(__file__).resolve().parent.parent / "config" / "demo.env"


def load_config(path=ENV_FILE):
    cfg = {}
    if Path(path).exists():
        for line in Path(path).read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip()
    return {k: os.environ.get(k, v) for k, v in cfg.items()}
