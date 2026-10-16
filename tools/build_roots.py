"""Copies named root CAs from certifi's Mozilla bundle into device/roots.pem.
Usage: uv run python tools/build_roots.py "ISRG Root X1" "ISRG Root X2" "DigiCert Global Root G2" [...]"""
import sys
from pathlib import Path

import certifi

OUT = Path(__file__).resolve().parent.parent / "device" / "roots.pem"


def main(names):
    blocks = Path(certifi.where()).read_text().split("\n\n")
    pems = []
    for name in names:
        hits = [b for b in blocks if f'# Label: "{name}"' in b]
        if not hits:
            sys.exit(f"root not found in certifi: {name}")
        pems.append(hits[0][hits[0].index("-----BEGIN CERTIFICATE-----"):].strip())
    OUT.write_text("\n".join(pems) + "\n")
    print(f"wrote {len(pems)} roots to {OUT}")


if __name__ == "__main__":
    main(sys.argv[1:])
