"""Reject tracked secrets and local runtime artifacts before GitHub upload."""

import re
import subprocess
import sys
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parent.parent
KEY_ASSIGNMENT = re.compile(r"(?im)^\s*(STEAM_API_KEY|ITAD_API_KEY)\s*=\s*['\"]?([^\s#'\"]+)")
TOKEN_PATTERNS = (
    re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{50,}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)
PLACEHOLDER_MARKERS = ("your_", "placeholder", "example", "changeme", "replace_me", "<", "${")


def repository_files():
    result = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return [item.decode("utf-8") for item in result.stdout.split(b"\0") if item]


def is_runtime_artifact(name):
    path = PurePosixPath(name.replace("\\", "/"))
    lower = path.as_posix().lower()
    if (path.name.lower().startswith(".env") and path.name.lower() != ".env.example") or lower.startswith("data/"):
        return True
    return lower.endswith((".db", ".sqlite3", ".sqlite3-wal", ".sqlite3-shm", ".log"))


def is_placeholder(value):
    lowered = value.lower()
    return any(marker in lowered for marker in PLACEHOLDER_MARKERS) or value in {"PROXY", "none", "false"}


def looks_like_key(value):
    return bool(re.fullmatch(r"[A-Za-z0-9_-]{24,}", value))


def main():
    problems = []
    for name in repository_files():
        if is_runtime_artifact(name):
            problems.append(f"tracked runtime/secret file: {name}")
            continue
        path = ROOT / name
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for match in KEY_ASSIGNMENT.finditer(text):
            if looks_like_key(match.group(2)) and not is_placeholder(match.group(2)):
                problems.append(f"possible real {match.group(1)} in {name}")
        for pattern in TOKEN_PATTERNS:
            if pattern.search(text):
                problems.append(f"possible credential in {name}")

    if problems:
        print("Secret check failed:", file=sys.stderr)
        for problem in sorted(set(problems)):
            print(f"- {problem}", file=sys.stderr)
        return 1
    print("Secret check passed: no tracked secrets or runtime artifacts found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
