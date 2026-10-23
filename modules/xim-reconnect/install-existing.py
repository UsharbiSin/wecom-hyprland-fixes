#!/usr/bin/env python3
"""Install the scoped XIM fix while preserving an existing launcher."""
import argparse
import hashlib
import json
from pathlib import Path
import shlex
import shutil
import stat
import subprocess
import tempfile
import time

SYSTEM_SHA256 = (
    "8d818805efd9e4c08d44981093f99afa2641f3dbefd70770788a570fb7ef8ecf"
)
BEGIN = "# BEGIN WECOM XIM RECONNECT\n"
END = "# END WECOM XIM RECONNECT\n"


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--launcher", type=Path, required=True)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--library", type=Path)
    parser.add_argument("--remove", action="store_true")
    args = parser.parse_args()
    launcher = args.launcher.resolve(strict=True)
    directory = args.directory.resolve()
    original = launcher.read_text()
    text = original
    if BEGIN in text or END in text:
        if text.count(BEGIN) != 1 or text.count(END) != 1:
            raise SystemExit("修复标记异常，停止修改")
        start = text.index(BEGIN)
        stop = text.index(END, start) + len(END)
        text = text[:start] + text[stop:]
    if not args.remove:
        if not args.library:
            parser.error("安装需要 --library")
        system = Path("/usr/lib/libX11.so.6").resolve(strict=True)
        if digest(system) != SYSTEM_SHA256:
            raise SystemExit("系统libX11版本不匹配，停止安装")
        if text.count("set -eu\n") != 1:
            raise SystemExit("启动器结构变化，停止修改")
        library = args.library.resolve(strict=True)
        subprocess.run([
            "/usr/bin/bwrap", "--bind", "/", "/",
            "--dev-bind", "/dev", "/dev", "/usr/bin/true",
        ], check=True)
        directory.mkdir(parents=True, exist_ok=True)
        for name in ("libX11.so.6.4.0", "launch-existing.py", "installed.json"):
            if (directory / name).is_symlink():
                raise SystemExit("安装目标包含符号链接，停止修改")
        shutil.copy2(library, directory / "libX11.so.6.4.0")
        shutil.copy2(
            Path(__file__).with_name("launch-existing.py"),
            directory / "launch-existing.py",
        )
        policy = {
            "system_library": str(system),
            "system_sha256": SYSTEM_SHA256,
            "patched_sha256": digest(library),
            "installed_at": time.time(),
        }
        (directory / "installed.json").write_text(
            json.dumps(policy, indent=2) + "\n"
        )
        block = (BEGIN + 'if [ "${WECOM_XIM_RECONNECT_VIEW:-}" != v1 ]; then\n'
                 + "    exec /usr/bin/python3 "
                 + shlex.quote(str(directory / "launch-existing.py"))
                 + ' "$0" "$@"\nfi\n' + END)
        text = text.replace("set -eu\n", "set -eu\n" + block, 1)
    if text == original:
        print("启动器无需修改")
        return
    directory.mkdir(parents=True, exist_ok=True)
    backup = directory / ("launcher-before-" + digest(launcher)[:16] + ".sh")
    if backup.is_symlink():
        raise SystemExit("备份路径是符号链接，停止修改")
    if not backup.exists():
        shutil.copy2(launcher, backup)
    with tempfile.NamedTemporaryFile(
        mode="w", prefix=launcher.name + ".xim-",
        dir=launcher.parent, delete=False,
    ) as stream:
        stream.write(text)
        temporary = Path(stream.name)
    temporary.chmod(stat.S_IMODE(launcher.stat().st_mode))
    temporary.replace(launcher)
    print(json.dumps({
        "launcher": str(launcher), "backup": str(backup),
        "removed": args.remove,
    }))


if __name__ == "__main__":
    main()
