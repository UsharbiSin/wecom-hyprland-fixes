#!/usr/bin/env python3
"""显式运行已校验的微软离线安装包；不下载或分发运行库。"""
import argparse
import hashlib
import os
from pathlib import Path
import subprocess

EXPECTED_SHA256 = (
    "0c09f2611660441084ce0df425c51c11e147e6447963c3690f97e0b25c55ed64"
)


def process_prefix(environment):
    values = dict(item.split(b"=", 1) for item in environment
                  if b"=" in item)
    raw = values.get(b"WINEPREFIX")
    if raw:
        return Path(os.fsdecode(raw)).expanduser().resolve()
    home = values.get(b"HOME", os.fsencode(Path.home()))
    return (Path(os.fsdecode(home)) / ".wine").resolve()


def ensure_stopped(prefix, proc_root=Path("/proc")):
    prefix = prefix.resolve()
    for path in proc_root.glob("[0-9]*/environ"):
        try:
            if path.stat().st_uid != os.getuid():
                continue
            environment = path.read_bytes().split(b"\0")
            first = (path.parent / "cmdline").read_bytes().split(b"\0")[0]
            first = first.lower().replace(b"\\", b"/")
            name = first.rsplit(b"/", 1)[-1]
            executable = (path.parent / "exe").resolve().name.lower()
        except (OSError, PermissionError):
            continue
        is_wine = (
            executable.startswith("wine") or name.startswith(b"wine")
            or name.endswith(b".exe")
            or (first.startswith(b"--module=") and name.endswith(b".dll"))
        )
        if is_wine and process_prefix(environment) == prefix:
            raise ValueError("该前缀仍有 Wine 进程，请先正常退出企业微信")


def validate(prefix, installer):
    prefix = prefix.expanduser().resolve()
    if not (prefix / "user.reg").is_file():
        raise ValueError("不是已初始化的专用 Wine 前缀")
    installer = installer.expanduser().resolve()
    with installer.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual != EXPECTED_SHA256:
        raise ValueError("安装包版本不匹配；请审查新版，不能只更改白名单")
    return prefix, installer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("installer", type=Path)
    parser.add_argument("--prefix", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        prefix, installer = validate(args.prefix, args.installer)
        print(f"计划：在 {prefix} 运行已校验的微软 x86 VC++ 安装包。")
        print("安装可能更新多个运行库；卸载请使用 Wine 的程序卸载界面。")
        if args.apply:
            ensure_stopped(prefix)
            env = dict(os.environ, WINEPREFIX=str(prefix))
            result = subprocess.run(
                ["wine", str(installer), "/install", "/passive",
                 "/norestart"], env=env,
            )
            if result.returncode:
                raise ValueError("安装返回非零状态，请查看安装器结果")
    except (ValueError, OSError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
