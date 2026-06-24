"""Layer 1 refresh: re-ingest trials and catalysts."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "ingest_layer1.py"), *sys.argv[1:]],
        cwd=str(ROOT),
        check=True,
    )


if __name__ == "__main__":
    main()
