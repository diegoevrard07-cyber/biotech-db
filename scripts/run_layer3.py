"""Layer 3 refresh: recompute and apply base rates."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def main() -> None:
    for script in ("compute_base_rates.py", "apply_base_rates.py"):
        subprocess.run([sys.executable, str(SCRIPTS / script), *sys.argv[1:]], cwd=str(ROOT), check=True)
    subprocess.run([sys.executable, str(SCRIPTS / "verify_layer3.py")], cwd=str(ROOT), check=True)


if __name__ == "__main__":
    main()
