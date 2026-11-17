#!/usr/bin/env python3
"""Select a complete verified Wine runtime for an existing WeCom launcher."""
import argparse
import ctypes
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys


MARKER = "WECOM_WINE_RUNTIME_VIEW"
IDENTITY = ("usr/bin/wine", "usr/bin/wineserver",
            "usr/lib/wine/x86_64-unix/ntdll.so")
TREES = ("usr/lib/wine", "usr/share/wine", "usr/include/wine")


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


class Flock(ctypes.Structure):
    _fields_ = [("type", ctypes.c_short), ("whence", ctypes.c_short),
                ("start", ctypes.c_longlong), ("length", ctypes.c_longlong),
                ("pid", ctypes.c_int)]


def server_hash(prefix):
    info = prefix.stat()
    name = f"server-{info.st_dev:x}-{info.st_ino:x}"
    lock = Path("/tmp") / f".wine-{os.getuid()}" / name / "lock"
    try:
        fd = os.open(lock, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise ValueError("专用前缀的 Wine 锁文件无效")
        def owner_now():
            query = Flock(fcntl.F_WRLCK, os.SEEK_SET, 0, 1, 0)
            owner = Flock.from_buffer_copy(
                fcntl.fcntl(fd, fcntl.F_GETLK, bytes(query)))
            if owner.type == fcntl.F_UNLCK:
                return None
            if owner.type != fcntl.F_WRLCK or owner.pid <= 0:
                raise ValueError("无法识别正在运行的企业微信服务")
            return owner.pid

        for attempt in range(3):
            owner = owner_now()
            if owner is None:
                return None
            try:
                actual = digest(Path("/proc") / str(owner) / "exe")
            except FileNotFoundError:
                if owner_now() != owner:
                    continue
                raise ValueError("无法校验仍在运行的企业微信服务")
            if owner_now() == owner:
                return actual
        raise ValueError("企业微信服务正在切换，请稍后重试")
    finally:
        os.close(fd)


def choose(policy, system, running=None):
    profiles = policy["profiles"]
    if running is not None:
        matches = [key for key, value in profiles.items()
                   if value["server_sha256"] == running]
        if not matches:
            raise ValueError("已有不配套的企业微信服务，请完整退出后重试")
        return matches[0], "existing-session"
    for key, value in profiles.items():
        if value["system_identity"] == system:
            return key, "system-matched"
    return policy["default"], "verified-fallback"


def verify_bundle(profile):
    root = Path(profile["root"])
    info = root.lstat()
    if (root.is_symlink() or not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid() or info.st_mode & 0o022):
        raise ValueError("Wine 运行环境目录权限或归属不正确")
    actual_files, actual_links = set(), set()
    for name in (*TREES, "usr/bin"):
        tree = root / name
        if tree.is_symlink() or not tree.is_dir():
            raise ValueError(f"Wine 运行环境缺少完整目录：{name}")
        for path in tree.rglob("*"):
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                if path.is_dir():
                    raise ValueError("Wine 运行环境目录不能使用链接")
                actual_links.add(relative)
            elif path.is_file():
                actual_files.add(relative)
    actual_files.add("patched-wineserver")
    if (actual_files != set(profile["files"])
            or actual_links != set(profile["links"])):
        raise ValueError("Wine 运行环境文件集合与安装记录不一致")
    for name, expected in profile["files"].items():
        path = root / name
        if (Path(name).is_absolute() or ".." in Path(name).parts
                or path.is_symlink()
                or not path.resolve().is_relative_to(root.resolve())
                or digest(path) != expected):
            raise ValueError(f"Wine 运行环境文件校验失败：{name}")
    for name, target in profile["links"].items():
        path = root / name
        if (not path.is_symlink() or os.readlink(path) != target
                or not path.resolve().is_relative_to(root.resolve())):
            raise ValueError(f"Wine 运行环境链接校验失败：{name}")
    for name in IDENTITY:
        if profile["files"].get(name) != profile["system_identity"][name]:
            raise ValueError("Wine 运行环境版本记录不一致")
    if profile["files"].get("patched-wineserver") != \
            profile["server_sha256"]:
        raise ValueError("Wine 服务修复记录不一致")


def command_for(profile, original):
    root = Path(profile["root"])
    command = ["/usr/bin/bwrap", "--bind", "/", "/",
               "--dev-bind", "/dev", "/dev"]
    for name in TREES:
        command += ["--ro-bind", str(root / name), "/" + name]
    for path in sorted((root / "usr/bin").iterdir()):
        target = Path("/usr/bin") / path.name
        if path.is_symlink():
            if not target.is_symlink() or \
                    os.readlink(target) != os.readlink(path):
                raise ValueError(f"Wine 命令链接已改变：{target.name}")
        else:
            command += ["--ro-bind", str(path), str(target)]
    command += ["--ro-bind", str(root / "patched-wineserver"),
                "/usr/bin/wineserver", *original]
    return command


def check_view(policy, environment):
    name = environment.get(MARKER)
    profile = policy["profiles"].get(name)
    if profile is None:
        raise ValueError("企业微信运行环境标记无效")
    expected = dict(profile["system_identity"])
    expected["usr/bin/wineserver"] = profile["server_sha256"]
    if any(digest("/" + path) != sha for path, sha in expected.items()):
        raise ValueError("企业微信运行环境混用或文件校验失败")
    if (environment.get("WINEPREFIX") != policy["prefix"]
            or environment.get("WINELOADER") != "/usr/bin/wine"
            or environment.get("WINESERVER") != "/usr/bin/wineserver"):
        raise ValueError("企业微信运行环境变量不完整")
    running = server_hash(Path(policy["prefix"]))
    if running is not None and running != profile["server_sha256"]:
        raise ValueError("企业微信会话版本已变化，请完整退出后重试")


def notify(message):
    try:
        subprocess.Popen(
            ["notify-send", "企业微信兼容运行环境", message],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path,
                        default=Path(__file__).with_name("installed.json"))
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--check-view", action="store_true")
    modes.add_argument("--check-only", action="store_true")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    try:
        policy = json.loads(args.config.read_text())
        if policy["schema"] != 1:
            raise ValueError("Wine 运行环境配置版本无效")
        prefix = Path(policy["prefix"])
        if prefix.is_symlink() or not (prefix / "user.reg").is_file():
            raise ValueError("企业微信专用前缀不存在或路径已改变")
        inherited = os.environ.get("WINEPREFIX")
        if inherited and Path(inherited).resolve() != prefix.resolve():
            raise ValueError("WINEPREFIX 指向其他环境")
        if args.check_view:
            check_view(policy, os.environ)
            return 0
        if os.environ.get(MARKER):
            raise ValueError("拒绝重复包装企业微信运行环境")
        original = args.command or [policy["launcher"]]
        if original[:1] == ["--"]:
            original = original[1:]
        if not original or Path(original[0]).resolve() != \
                Path(policy["launcher"]).resolve():
            raise ValueError("必须通过现有企业微信启动器使用运行环境")
        system = {name: digest("/" + name) for name in IDENTITY}
        name, reason = choose(policy, system, server_hash(prefix))
        profile = policy["profiles"][name]
        verify_bundle(profile)
        running = server_hash(prefix)
        if running is not None and running != profile["server_sha256"]:
            raise ValueError("企业微信会话版本已变化，请稍后重试")
        command = command_for(profile, original)
        if args.check_only:
            print(json.dumps({"profile": name, "reason": reason,
                              "command": command}, ensure_ascii=False))
            return 0
        if reason == "verified-fallback":
            message = (f"系统 Wine 已变化；本次继续使用已验证的 {name}，"
                       "保留现有兼容修复。")
            print(message, file=sys.stderr)
            notify(message)
        environment = dict(os.environ, WINEPREFIX=str(prefix),
                           WINELOADER="/usr/bin/wine",
                           WINESERVER="/usr/bin/wineserver")
        environment[MARKER] = name
        environment.pop("WECOM_WINESERVER_VIEW", None)
        os.execvpe(command[0], command, environment)
    except (OSError, ValueError, KeyError, TypeError) as error:
        message = f"企业微信未启动：{error}"
        print(message, file=sys.stderr)
        if not args.check_only and not args.check_view:
            notify(message)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
