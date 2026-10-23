#!/usr/bin/env python3
"""A/B/A test using only a disposable X server and a synthetic XIM server."""
import argparse
import json
import os
from pathlib import Path
import socket
import stat
import shutil
import subprocess
import tempfile
import time

TEST_DISPLAY_NUMBER = 177


def ensure_display_unused(number):
    """Refuse before Xvfb starts; never unlink a socket or a lock file."""
    display_number = str(number)
    current = os.environ.get("DISPLAY", "").rsplit(":", 1)[-1].split(".", 1)[0]
    if current == display_number:
        raise RuntimeError("Refuse to use the current desktop DISPLAY")
    paths = (
        Path(f"/tmp/.X{number}-lock"),
        Path(f"/tmp/.X11-unix/X{number}"),
        Path(f"/tmp/.X11-unix/X{number}_"),
    )
    for path in paths:
        if os.path.lexists(path):
            raise RuntimeError(f"Test display is already reserved: {path}")
    # Abstract UNIX sockets have no filesystem entry.  Reading /proc is
    # mandatory: without it, fail closed instead of guessing that it is free.
    names = {str(path) for path in paths[1:]}
    names |= {"@" + name for name in names}
    for line in Path("/proc/net/unix").read_text().splitlines()[1:]:
        fields = line.split()
        if len(fields) >= 8 and fields[-1] in names:
            raise RuntimeError(
                f"Test display has an existing UNIX listener: {fields[-1]}"
            )


def wait_for_xvfb(process, number, timeout=8):
    """Accept only the requested display with a lock owned by this child."""
    lock = Path(f"/tmp/.X{number}-lock")
    endpoint = Path(f"/tmp/.X11-unix/X{number}")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                f"Xvfb exited before readiness: {process.returncode}"
            )
        try:
            lock_stat = lock.lstat()
            if not stat.S_ISREG(lock_stat.st_mode):
                raise RuntimeError("Xvfb lock is not a regular file")
            owner = int(lock.read_text().strip())
            if owner != process.pid:
                raise RuntimeError(
                    f"Xvfb display lock belongs to another process: {owner}"
                )
            if not stat.S_ISSOCK(endpoint.lstat().st_mode):
                raise RuntimeError("Xvfb endpoint is not a UNIX socket")
            with (
                socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection
            ):
                connection.settimeout(0.2)
                connection.connect(str(endpoint))
            return
        except (
            FileNotFoundError, ConnectionRefusedError,
            socket.timeout, ValueError,
        ):
            time.sleep(0.05)
    raise RuntimeError("Xvfb did not become ready on the reserved test display")


def main():
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser()
    parser.add_argument("--library", required=True, type=Path)
    parser.add_argument("--xvfb", default=shutil.which("Xvfb"))
    parser.add_argument(
        "--output", type=Path, default=root / "test-results.json"
    )
    args = parser.parse_args()
    if not args.xvfb:
        parser.error(
            "Xvfb is required; use --xvfb PATH for a privately unpacked copy"
        )
    library_path = args.library.resolve(strict=True)
    display = f":{TEST_DISPLAY_NUMBER}"
    results = []
    with (
        tempfile.TemporaryDirectory(
            prefix="wecom-xim-reconnect-test-"
        ) as temporary
    ):
        temporary = Path(temporary)
        client = temporary / "test_reconnect"
        server = temporary / "test_server"
        subprocess.run(
            ["cc", "-Wall", "-Wextra", "-Werror",
             str(root / "test_reconnect.c"), "-o", str(client), "-lX11"],
            check=True,
        )
        flags = subprocess.check_output(
            ["pkg-config", "--cflags", "--libs", "xcb-imdkit", "xcb-util"],
            text=True,
        ).split()
        subprocess.run(
            ["cc", "-Wall", "-Wextra", "-Werror",
             str(root / "test_server.c"), "-o", str(server), *flags],
            check=True,
        )
        # Keep this check immediately before launch.  Never use -displayfd:
        # automatic display allocation can select and damage a live :0 entry.
        ensure_display_unused(TEST_DISPLAY_NUMBER)
        with (temporary / "xvfb.log").open("w") as log:
            xvfb = subprocess.Popen([
                args.xvfb, display, "-nolisten", "tcp",
                "-screen", "0", "800x600x24",
            ], stdout=log, stderr=log,
               env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"})
            try:
                wait_for_xvfb(xvfb, TEST_DISPLAY_NUMBER)
                print("ISOLATED_DISPLAY", display, flush=True)
                for library in ["system", "fixed", "system"]:
                    for case in [
                        "first-display-only", "second-display-only",
                        "second-display-remove-first",
                        "same-display-remove-first", "deferred-unregister",
                    ]:
                        env = {
                            "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8",
                            "DISPLAY": display,
                        }
                        if library == "fixed":
                            env["LD_PRELOAD"] = str(library_path)
                        result = subprocess.run(
                            [str(client), case, str(server)],
                            env=env, capture_output=True, text=True, timeout=8,
                        )
                        row = {
                            "library": library, "case": case,
                            "returncode": result.returncode,
                            "stdout": result.stdout.strip(),
                            "stderr": result.stderr.strip(),
                        }
                        results.append(row)
                        print(json.dumps(row), flush=True)
            finally:
                # Only signal the child we created.  Xvfb removes its own
                # entries; this runner never deletes any X socket or lock.
                if xvfb.poll() is None:
                    xvfb.terminate()
                    try:
                        xvfb.wait(3)
                    except subprocess.TimeoutExpired:
                        xvfb.kill()
                        xvfb.wait()
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(json.dumps(results, indent=2) + "\n")
    expected = len(results) == 15 and all(
        row["returncode"] == (0 if row["library"] == "fixed" or
                               row["case"] == "deferred-unregister" else 1)
        for row in results
    )
    return 0 if expected else 1


if __name__ == "__main__":
    raise SystemExit(main())
