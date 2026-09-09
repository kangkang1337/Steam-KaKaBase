"""Validate inline JavaScript embedded in the single-page frontend."""

import hashlib
from html.parser import HTMLParser
import re
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
VENDOR_FILES = {
    "assets/vendor/vue-3.5.13.global.prod.js": "c459ba7cc8db65c982589fa5d64c7ff478877e8e5b0fd75683207cec6a4e89e8",
    "assets/vendor/echarts-5.6.0.min.js": "bf4a223524e40b77c304bec67e1222cf551f14880cf42c69dc046558e11c07b1",
}


class InlineScriptExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.scripts = []
        self._script_parts = None

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "script":
            return
        attributes = dict(attrs)
        if "src" not in attributes:
            self._script_parts = []

    def handle_data(self, data):
        if self._script_parts is not None:
            self._script_parts.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == "script" and self._script_parts is not None:
            self.scripts.append("".join(self._script_parts))
            self._script_parts = None


def main():
    html = (ROOT / "steamkb.html").read_text(encoding="utf-8")
    external_scripts = re.findall(r'<script[^>]+src=["\']([^"\']+)', html, flags=re.IGNORECASE)
    if set(external_scripts) != {f"/{path}" for path in VENDOR_FILES}:
        print(f"Unexpected frontend script sources: {external_scripts}", file=sys.stderr)
        return 1
    for relative_path, expected_hash in VENDOR_FILES.items():
        vendor_path = ROOT / relative_path
        digest = hashlib.sha256(vendor_path.read_bytes()).hexdigest()
        if digest != expected_hash:
            print(f"Vendor checksum mismatch: {relative_path}", file=sys.stderr)
            return 1
        result = subprocess.run(["node", "--check", str(vendor_path)], cwd=ROOT)
        if result.returncode:
            return result.returncode
    extractor = InlineScriptExtractor()
    extractor.feed(html)
    extractor.close()
    inline = [script for script in extractor.scripts if script.strip()]
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
