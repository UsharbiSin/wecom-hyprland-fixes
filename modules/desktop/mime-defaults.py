#!/usr/bin/env python3
"""单独管理桌面默认程序；默认只显示计划，显式 --apply 才写入。"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

ASSOCIATIONS = {
    "inode/directory": "thunar.desktop",
    "application/msword": "wps-office-wps.desktop",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        "wps-office-wps.desktop",
    "application/vnd.ms-excel": "wps-office-et.desktop",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":
        "wps-office-et.desktop",
    "application/vnd.ms-powerpoint": "wps-office-wpp.desktop",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation":
        "wps-office-wpp.desktop",
    "application/pdf": "wps-office-pdf.desktop",
    "x-scheme-handler/wemeet": "wemeetapp.desktop",
}


def config_file():
    base = os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
    return Path(base) / "mimeapps.list"


def sha256(path):
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def desktop_exists(name):
    local = os.environ.get(
        "XDG_DATA_HOME", str(Path.home() / ".local/share"),
    )
    roots = [local] + os.environ.get(
        "XDG_DATA_DIRS", "/usr/local/share:/usr/share",
    ).split(":")
    return any((Path(root) / "applications" / name).is_file()
               for root in roots)


def run(command):
    return subprocess.run(command, check=True, capture_output=True, text=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--apply", action="store_true", help="写入默认程序")
    actions.add_argument("--restore", action="store_true", help="恢复本次备份")
    parser.add_argument("--backup-dir", type=Path, help="应用/恢复所用目录")
    parser.add_argument(
        "--meeting", action="store_true", help="同时关联原生腾讯会议协议",
    )
    args = parser.parse_args()
    associations = dict(ASSOCIATIONS)
    if not args.meeting:
        associations.pop("x-scheme-handler/wemeet")
    target = config_file()
    if args.apply or args.restore:
        if args.backup_dir is None:
            parser.error("写入或恢复时必须指定 --backup-dir")
    if args.restore:
        record = json.loads((args.backup_dir / "state.json").read_text())
        if str(target) != record["target"]:
            raise SystemExit("配置路径已改变，拒绝恢复到其他位置")
        if sha256(target) != record["after"]:
            raise SystemExit("默认程序后来又有修改，拒绝覆盖；请手动合并备份")
        if record["before"] is None:
            target.unlink(missing_ok=True)
        else:
            source = args.backup_dir / "mimeapps.before"
            if sha256(source) != record["before"]:
                raise SystemExit("备份内容已改变，拒绝恢复")
            shutil.copy2(source, target)
        print("已恢复默认程序关联")
        return
    for mime, application in associations.items():
        previous = run(["xdg-mime", "query", "default", mime]).stdout.strip()
        print(f"{mime}\n  {previous or '未设置'} → {application}")
    if not args.apply:
        print("仅显示计划。加 --apply 和 --backup-dir 才会修改默认程序。")
        return
    for application in set(associations.values()):
        if not desktop_exists(application):
            raise SystemExit(f"缺少桌面程序：{application}；未修改")
    if target.is_symlink():
        raise SystemExit("mimeapps.list 为符号链接，请先人工检查")
    args.backup_dir.mkdir(parents=True, mode=0o700, exist_ok=False)
    before = sha256(target)
    if before is not None:
        shutil.copy2(target, args.backup_dir / "mimeapps.before")
        os.chmod(args.backup_dir / "mimeapps.before", 0o600)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        for mime, application in associations.items():
            run(["xdg-mime", "default", application, mime])
    finally:
        record = {"target": str(target), "before": before,
                  "after": sha256(target)}
        state = args.backup_dir / "state.json"
        state.write_text(json.dumps(record, ensure_ascii=False, indent=2))
        os.chmod(state, 0o600)
    print("已应用；恢复时请使用同一 --backup-dir")


if __name__ == "__main__":
    main()
