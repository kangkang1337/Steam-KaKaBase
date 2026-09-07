"""Validate inline JavaScript embedded in the single-page frontend."""

import re
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def main():
    html = (ROOT / "steamkb.html").read_text(encoding="utf-8")
    scripts = re.findall(r"<script(?:\s[^>]*)?>(.*?)</script>", html, flags=re.IGNORECASE | re.DOTALL)
    inline = [script for script in scripts if script.strip()]
    if not inline:
        print("No inline JavaScript found.", file=sys.stderr)
        return 1
    with tempfile.NamedTemporaryFile("w", suffix=".js", encoding="utf-8", delete=False) as handle:
        handle.write("\n".join(inline))
        temporary = Path(handle.name)
    try:
        result = subprocess.run(["node", "--check", str(temporary)], cwd=ROOT)
        if result.returncode:
            return result.returncode
    finally:
        temporary.unlink(missing_ok=True)
    print("Frontend JavaScript syntax check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
