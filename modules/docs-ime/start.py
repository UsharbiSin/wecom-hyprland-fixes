#!/usr/bin/env python3
"""校验实际系统与前缀组件后启动共享文档输入法桥。"""
import argparse
import hashlib
import json
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
VERSION = "5.0.11.6018"


def verify(prefix, version, manifest=None):
    if version != VERSION:
        raise ValueError("未验证的企业微信版本，拒绝加载输入法桥。")
    context = {
        "prefix": str(Path(prefix).resolve()),
        "app_dir": str(Path(prefix).resolve() /
                       "drive_c/Program Files (x86)/WXWork"),
        "app_version": version,
    }
    if manifest is None:
        manifest = json.loads((HERE / "module.json").read_text())
    for item in manifest["guards"]:
        path = Path(item["target"].format_map(context))
        with path.open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if actual != item["sha256"]:
            raise ValueError(f"输入法桥组件哈希不匹配：{path.name}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--app-version", default=VERSION)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--stop", action="store_true")
    args = parser.parse_args()
    try:
        if not args.stop:
            verify(args.prefix, args.app_version)
        if args.verify_only:
            return 0
        env = os.environ.copy()
        env["WINEPREFIX"] = str(Path(args.prefix).resolve())
        env["WECOM_DOCS_IME_APP_DIR"] = r"C:\Program Files (x86)\WXWork"
        command = ["wine", str(HERE / "docs-ime-guard.exe")]
        if args.stop:
            command.append("--stop")
        else:
            command += ["--app-version", args.app_version]
        os.execvpe(command[0], command, env)
    except (OSError, ValueError) as error:
        parser.exit(1, f"输入法桥未启动：{error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
