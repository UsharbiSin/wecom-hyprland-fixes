#!/usr/bin/env python3
"""Install only the verified native picker into an existing WeCom launcher."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil


BEGIN = "# BEGIN WECOM NATIVE FILE PICKER"
END = "# END WECOM NATIVE FILE PICKER"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_write(path, text, mode):
    temporary = path.with_name(path.name + ".new")
    with temporary.open("x", encoding="utf-8") as stream:
        stream.write(text)
    temporary.chmod(mode)
    os.replace(temporary, path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--launcher", type=Path, required=True)
    parser.add_argument("--prefix", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--build", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    launcher = args.launcher.resolve()
    text = launcher.read_text()
    if BEGIN in text or END in text:
        raise ValueError("现有启动器已包含文件选择桥接，请先核对安装记录")
    anchor = 'app_dir="$WINEPREFIX/drive_c/Program Files (x86)/WXWork"'
    if text.count(anchor) != 1:
        raise ValueError("企业微信启动器结构已变化")
    if not (args.prefix / "user.reg").is_file():
        raise ValueError("企业微信前缀不存在")
    destination = args.destination.absolute()
    destination.mkdir(mode=0o700)
    for name in ("guard.exe", "bridge.py"):
        source = args.build / name
        if not source.is_file() or source.is_symlink():
            raise ValueError("缺少经过验证的构建产物")
        shutil.copy2(source, destination / name)
        (destination / name).chmod(0o600)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = launcher.with_name(launcher.name + ".backup-picker-" + stamp)
    shutil.copy2(launcher, backup)
    block = "\n".join([
        BEGIN,
        "# GTK chooser; original WeCom dialog retains its validation hooks.",
        "(umask 077; /usr/bin/python3 " +
        shlex.quote(str(destination / "bridge.py")) + " \\",
        "    --prefix " + shlex.quote(str(args.prefix.resolve())) + " \\",
        "    >>" + shlex.quote(str(destination / "session.log")) +
        " 2>&1) &",
        END, "", "",
    ])
    record = {
        "schema": 1,
        "wine_version": "11.22",
        "files": {name: digest(destination / name)
                  for name in ("guard.exe", "bridge.py")},
        "launcher": str(launcher),
        "backup": str(backup),
        "previous_launcher_sha256": digest(launcher),
    }
    atomic_write(destination / "installed.json",
                 json.dumps(record, ensure_ascii=False, indent=2) + "\n",
                 0o600)
    atomic_write(launcher, text.replace(anchor, block + anchor),
                 launcher.stat().st_mode & 0o777)
    print(json.dumps({"destination": str(destination),
                      "backup": str(backup)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
