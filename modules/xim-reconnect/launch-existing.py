#!/usr/bin/env python3
"""Use the verified XIM reconnect library in an existing WeCom launcher."""
import hashlib
import json
import os
from pathlib import Path
import sys


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    if len(sys.argv) < 2:
        raise SystemExit(
            "usage: launch-existing.py EXISTING_LAUNCHER [ARGS ...]"
        )
    directory = Path(__file__).resolve().parent
    command = sys.argv[1:]
    environment = dict(os.environ, WECOM_XIM_RECONNECT_VIEW="v1")
    try:
        policy = json.loads((directory / "installed.json").read_text())
        if not isinstance(policy, dict):
            raise ValueError("invalid installation policy")
        if not os.access("/usr/bin/bwrap", os.X_OK):
            raise ValueError("bwrap is not executable")
        target = Path(policy["system_library"])
        patched = directory / "libX11.so.6.4.0"
        if Path("/usr/lib/libX11.so.6").resolve() != target:
            raise ValueError("system library target changed")
        if sha256(target) != policy["system_sha256"]:
            raise ValueError("system library version changed")
        if sha256(patched) != policy["patched_sha256"]:
            raise ValueError("patched library checksum mismatch")
        command = [
            "/usr/bin/bwrap", "--bind", "/", "/",
            "--dev-bind", "/dev", "/dev",
            "--ro-bind", str(patched), str(target), *command,
        ]
    except (OSError, ValueError, KeyError, TypeError) as error:
        print("企业微信 XIM 重连修复未加载：" + str(error), file=sys.stderr)
    os.execvpe(command[0], command, environment)


if __name__ == "__main__":
    main()
