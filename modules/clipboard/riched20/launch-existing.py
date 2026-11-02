#!/usr/bin/env python3
"""在企业微信私有视图中使用与当前原件配对的 RichEdit 副本。"""

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import importlib.util
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent
WINE = "/usr/bin/wine"
WINESERVER = "/usr/bin/wineserver"
SPEC = importlib.util.spec_from_file_location(
    "riched20_build", ROOT / "build.py")
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def candidate_matches(original, candidate):
    if not candidate.is_file():
        return False
    data = candidate.read_bytes()
    try:
        expected = BUILDER.apply_stub(
            original, data[BUILDER.STUB:BUILDER.STUB + 111])
    except (ValueError, IndexError):
        return False
    return data == expected


def prepare(source, cache):
    """缓存按原件完整哈希分开，绝不复用其他 Wine 原件的 DLL。"""
    original = source.read_bytes()
    BUILDER.validate_source(original)
    original_sha = hashlib.sha256(original).hexdigest()
    cache.mkdir(parents=True, exist_ok=True, mode=0o700)
    folder = cache / original_sha
    candidate = folder / "riched20.dll"
    with (cache / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not candidate_matches(original, candidate):
            with tempfile.TemporaryDirectory(prefix="build-", dir=cache) as tmp:
                temporary = Path(tmp)
                # 锁定读取快照；安装过程中系统升级也不会混合两个原件。
                snapshot = temporary / "source.dll"
                snapshot.write_bytes(original)
                output = temporary / "output"
                BUILDER.build(snapshot, output)
                subprocess.run([
                    sys.executable, str(ROOT / "verify.py"),
                    "--build-dir", str(output),
                ], check=True, timeout=60, stdout=sys.stderr)
                if digest(source) != original_sha:
                    raise ValueError("构建时系统 DLL 改变，请重新启动")
                folder.mkdir(exist_ok=True, mode=0o700)
                # 该哈希目录不会服务其他原件；最后替换 DLL，避免半写文件。
                for name in ("riched20-manifest.json",
                             "riched20-verification.json", "riched20.dll"):
                    os.replace(output / name, folder / name)
    return candidate, original_sha


class PrefixUpgradeError(ValueError):
    """升级未完成时不能继续启动 Wine，也不能退回无补丁启动。"""


@contextmanager
def prefix_lock(prefix, timeout=240):
    """同一专用前缀的升级与构建串行进行；不锁住其他 Wine 前缀。"""
    flags = os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW
    fd = os.open(prefix / ".wecom-clipboard-startup.lock", flags, 0o600)
    with os.fdopen(fd, "a") as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_nlink != 1):
            raise PrefixUpgradeError("启动锁不是当前用户的独立普通文件")
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise PrefixUpgradeError("其他启动器仍在准备此专用前缀")
                time.sleep(0.1)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def update_environment(prefix):
    env = dict(os.environ, WINEPREFIX=str(prefix), WINELOADER=WINE,
               WINESERVER=WINESERVER, WINEDEBUG="-all")
    for name in ("WINEARCH", "WINEDLLPATH", "LD_PRELOAD"):
        env.pop(name, None)
    # 显式覆盖注册表中的 native 设置；只作用于更新，不改用户注册表。
    env["WINEDLLOVERRIDES"] = (
        "explorer.exe=b;wineboot.exe=b;winewayland.drv=d")
    return env


def synchronize_prefix(source, prefix):
    """在安装任何 Wine 文件视图前完成空闲前缀的标准 Wine 更新。"""
    prefix_dll = prefix / "drive_c/windows/syswow64/riched20.dll"
    original_sha = digest(source)
    if digest(prefix_dll) == original_sha:
        return
    views = ("WECOM_CAMERA_VIEW", "WECOM_DESKTOP_VIEW",
             "WECOM_DOCS_V8_VIEW", "WECOM_CLIPBOARD_RICHEDIT_VIEW")
    if any(os.environ.get(name) for name in views):
        raise PrefixUpgradeError("须在其他 Wine 私有视图之前运行图片入口")
    env = update_environment(prefix)
    try:
        # -w 只等待既有 server，不启动或终止 Wine 会话。
        subprocess.run([WINESERVER, "-w"], env=env, check=True,
                       timeout=0.5, stdout=sys.stderr)
    except subprocess.TimeoutExpired as error:
        raise PrefixUpgradeError(
            "Wine 已升级，但专用前缀仍有会话；请完全退出企业微信后重试") \
            from error
    except (OSError, subprocess.CalledProcessError) as error:
        raise PrefixUpgradeError("不能确认专用前缀已空闲，未执行更新") \
            from error
    print("正在更新企业微信专用 Wine 前缀…", file=sys.stderr)
    try:
        process = subprocess.Popen([WINE, "wineboot", "--update"],
                                   env=env, stdout=sys.stderr)
        result = process.wait(timeout=120)
        if result:
            raise subprocess.CalledProcessError(result, process.args)
        subprocess.run([WINESERVER, "-w"], env=env, check=True,
                       timeout=60, stdout=sys.stderr)
    except (OSError, subprocess.SubprocessError) as error:
        # 超时只停止本次启动流程，不杀前缀或仍在执行更新的 Wine 进程。
        raise PrefixUpgradeError("前缀更新未完成，请等待更新结束后重试") \
            from error
    if (digest(source) != original_sha or
            digest(prefix_dll) != original_sha):
        raise PrefixUpgradeError("更新后原件仍不配对或系统又有更新，未启动")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", required=True, type=Path)
    parser.add_argument("--source", type=Path, default=BUILDER.SOURCE)
    parser.add_argument("--cache", type=Path, default=ROOT / "cache")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.command[:1] == ["--"]:
        args.command = args.command[1:]
    if not args.command:
        parser.error("缺少现有启动器路径")
    environment = dict(os.environ, WECOM_CLIPBOARD_COMPAT_READY="v1")
    environment.pop("WECOM_CLIPBOARD_RICHEDIT_VIEW", None)
    command = args.command
    prefix_ready = False
    try:
        if (not args.prefix.is_absolute() or
                not (args.prefix / "user.reg").is_file()):
            raise PrefixUpgradeError("必须指定现有专用前缀的绝对路径")
        prefix = args.prefix.resolve(strict=True)
        prefix_dll = prefix / "drive_c/windows/syswow64/riched20.dll"
        if not prefix_dll.resolve(strict=True).is_relative_to(prefix):
            raise PrefixUpgradeError("前缀 RichEdit 路径不能指向其他环境")
        with prefix_lock(prefix):
            synchronize_prefix(args.source, prefix)
            prefix_ready = True
            if not os.access("/usr/bin/bwrap", os.X_OK):
                raise ValueError("缺少可执行的 bubblewrap")
            candidate, original_sha = prepare(args.source, args.cache)
            if (digest(args.source) != original_sha or
                    digest(prefix_dll) != original_sha):
                raise PrefixUpgradeError("启动前原件已改变，请重新启动")
            command = ["/usr/bin/bwrap", "--bind", "/", "/", "--dev-bind",
                       "/dev", "/dev", "--ro-bind", str(candidate),
                       str(args.source), "--ro-bind", str(candidate),
                       str(prefix_dll), *command]
            environment["WECOM_CLIPBOARD_RICHEDIT_VIEW"] = "v1"
    except PrefixUpgradeError as error:
        print("企业微信未启动：" + str(error), file=sys.stderr)
        return 1
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        if not prefix_ready:
            print("企业微信未启动：" + str(error), file=sys.stderr)
            return 1
        try:
            still_paired = digest(args.source) == digest(prefix_dll)
        except OSError:
            still_paired = False
        if not still_paired:
            print("企业微信未启动：原件已改变，请重新启动", file=sys.stderr)
            return 1
        print("企业微信图片输入补丁未加载：" + str(error), file=sys.stderr)
    os.execvpe(command[0], command, environment)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
