"""升级启动回归只使用临时前缀和模拟 Wine，不操作用户会话。"""
import contextlib
import hashlib
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "modules/clipboard/riched20/launch-existing.py"
SPEC = importlib.util.spec_from_file_location("clipboard_startup", SCRIPT)
LAUNCHER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LAUNCHER)


class ClipboardStartup(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.prefix = Path(self.folder.name)
        (self.prefix / "user.reg").touch()
        self.target = self.prefix / "drive_c/windows/syswow64/riched20.dll"
        self.target.parent.mkdir(parents=True)
        self.target.write_bytes(b"current")
        self.source = self.prefix / "source.dll"
        self.source.write_bytes(b"current")
        self.candidate = self.prefix / "candidate.dll"
        self.sha = hashlib.sha256(b"current").hexdigest()
        self.env = mock.patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.stderr = contextlib.redirect_stderr(io.StringIO())
        self.stderr.__enter__()
        self.addCleanup(self.stderr.__exit__, None, None, None)
        self.arguments = [str(SCRIPT), "--prefix", str(self.prefix),
                          "--source", str(self.source), "--",
                          "/tmp/existing-launcher", "argument with spaces"]

    def synchronize(self):
        LAUNCHER.synchronize_prefix(self.source, self.prefix)

    def test_current_prefix_does_not_run_wine(self):
        with mock.patch.object(LAUNCHER.subprocess, "run") as wait, \
                mock.patch.object(LAUNCHER.subprocess, "Popen") as boot:
            self.synchronize()
        wait.assert_not_called()
        boot.assert_not_called()

    def test_upgrade_finishes_before_build_and_bind_on_first_launch(self):
        self.target.write_bytes(b"old")
        events = []

        def wait(*args, **kwargs):
            events.append("server-wait")
            self.assertEqual(args[0], [LAUNCHER.WINESERVER, "-w"])
            self.assertEqual(kwargs["env"]["WINEPREFIX"], str(self.prefix))

        def complete_update(**kwargs):
            events.append("boot-complete")
            self.target.write_bytes(self.source.read_bytes())
            return 0

        def prepare(*args):
            events.append("build")
            return self.candidate, self.sha

        process = mock.Mock()
        process.wait.side_effect = complete_update
        with mock.patch.object(LAUNCHER.sys, "argv", self.arguments), \
                mock.patch.dict(os.environ,
                                {"WINEDLLOVERRIDES": "explorer.exe=n"}), \
                mock.patch.object(LAUNCHER.subprocess, "run", wait), \
                mock.patch.object(LAUNCHER.subprocess, "Popen",
                                  return_value=process) as boot, \
                mock.patch.object(LAUNCHER, "prepare", prepare), \
                mock.patch.object(LAUNCHER.os, "access", return_value=True), \
                mock.patch.object(LAUNCHER.os, "execvpe") as execute:
            self.assertEqual(LAUNCHER.main(), 0)
        self.assertEqual(events, ["server-wait", "boot-complete",
                                  "server-wait", "build"])
        self.assertEqual(boot.call_args.args[0],
                         [LAUNCHER.WINE, "wineboot", "--update"])
        self.assertEqual(boot.call_args.kwargs["env"]["WINEDLLOVERRIDES"],
                         "explorer.exe=b;wineboot.exe=b;winewayland.drv=d")
        command = execute.call_args.args[1]
        self.assertEqual(command[0], "/usr/bin/bwrap")
        self.assertEqual(command.count("--ro-bind"), 2)
        self.assertEqual(command[-2:], self.arguments[-2:])
        self.assertEqual(execute.call_args.args[2]
                         ["WECOM_CLIPBOARD_RICHEDIT_VIEW"], "v1")
        self.assertEqual(execute.call_args.args[2]["WINEDLLOVERRIDES"],
                         "explorer.exe=n")

    def test_active_prefix_or_server_error_never_updates_or_launches(self):
        self.target.write_bytes(b"old")
        for error in (subprocess.TimeoutExpired("wineserver", 0.5),
                      subprocess.CalledProcessError(1, "wineserver"),
                      OSError("server unavailable")):
            with self.subTest(error=type(error).__name__), \
                    mock.patch.object(LAUNCHER.sys, "argv", self.arguments), \
                    mock.patch.object(LAUNCHER.subprocess, "run",
                                      side_effect=error), \
                    mock.patch.object(LAUNCHER.subprocess, "Popen") as boot, \
                    mock.patch.object(LAUNCHER, "prepare") as prepare, \
                    mock.patch.object(LAUNCHER.os, "execvpe") as execute:
                self.assertEqual(LAUNCHER.main(), 1)
            boot.assert_not_called()
            prepare.assert_not_called()
            execute.assert_not_called()

    def test_update_failure_or_timeout_never_kills_session_or_launches(self):
        self.target.write_bytes(b"old")
        for result in (1, subprocess.TimeoutExpired("wineboot", 120)):
            process = mock.Mock()
            if isinstance(result, Exception):
                process.wait.side_effect = result
            else:
                process.wait.return_value = result
            with self.subTest(result=repr(result)), \
                    mock.patch.object(LAUNCHER.sys, "argv", self.arguments), \
                    mock.patch.object(LAUNCHER.subprocess, "run"), \
                    mock.patch.object(LAUNCHER.subprocess, "Popen",
                                      return_value=process), \
                    mock.patch.object(LAUNCHER, "prepare") as prepare, \
                    mock.patch.object(LAUNCHER.os, "execvpe") as execute:
                self.assertEqual(LAUNCHER.main(), 1)
            process.kill.assert_not_called()
            process.terminate.assert_not_called()
            prepare.assert_not_called()
            execute.assert_not_called()

    def test_incomplete_update_or_source_change_rejects_all_startup(self):
        self.target.write_bytes(b"old")
        for change_source in (False, True):
            def complete_update(**kwargs):
                if change_source:
                    self.target.write_bytes(b"current")
                    self.source.write_bytes(b"changed during update")
                return 0
            process = mock.Mock()
            process.wait.side_effect = complete_update
            with self.subTest(change_source=change_source), \
                    mock.patch.object(LAUNCHER.subprocess, "run"), \
                    mock.patch.object(LAUNCHER.subprocess, "Popen",
                                      return_value=process):
                with self.assertRaises(LAUNCHER.PrefixUpgradeError):
                    self.synchronize()

    def test_update_after_other_private_views_is_rejected(self):
        self.target.write_bytes(b"old")
        for marker in ("WECOM_CAMERA_VIEW", "WECOM_DESKTOP_VIEW",
                       "WECOM_DOCS_V8_VIEW", "WECOM_CLIPBOARD_RICHEDIT_VIEW"):
            with self.subTest(marker=marker), \
                    mock.patch.dict(os.environ, {marker: "v1"}), \
                    mock.patch.object(LAUNCHER.subprocess, "run") as wait:
                with self.assertRaises(LAUNCHER.PrefixUpgradeError):
                    self.synchronize()
            wait.assert_not_called()

    def test_update_environment_cannot_select_other_prefix_or_loader(self):
        overrides = {key: "unexpected" for key in (
            "WINEPREFIX", "WINELOADER", "WINESERVER", "WINEARCH",
            "WINEDLLPATH", "LD_PRELOAD", "WINEDLLOVERRIDES")}
        with mock.patch.dict(os.environ, overrides):
            env = LAUNCHER.update_environment(self.prefix)
        self.assertEqual(env["WINEPREFIX"], str(self.prefix))
        self.assertEqual(env["WINELOADER"], LAUNCHER.WINE)
        self.assertEqual(env["WINESERVER"], LAUNCHER.WINESERVER)
        self.assertEqual(env["WINEDLLOVERRIDES"],
                         "explorer.exe=b;wineboot.exe=b;winewayland.drv=d")
        for key in ("WINEARCH", "WINEDLLPATH", "LD_PRELOAD"):
            self.assertNotIn(key, env)

    def test_build_failure_clears_stale_success_view_and_does_not_recurse(self):
        with mock.patch.object(LAUNCHER.sys, "argv", self.arguments), \
                mock.patch.dict(os.environ,
                                {"WECOM_CLIPBOARD_RICHEDIT_VIEW": "v1"}), \
                mock.patch.object(LAUNCHER, "prepare",
                                  side_effect=ValueError("unsupported")), \
                mock.patch.object(LAUNCHER.os, "access", return_value=True), \
                mock.patch.object(LAUNCHER.os, "execvpe") as execute:
            self.assertEqual(LAUNCHER.main(), 0)
        self.assertEqual(execute.call_args.args[1], self.arguments[-2:])
        self.assertNotIn("WECOM_CLIPBOARD_RICHEDIT_VIEW",
                         execute.call_args.args[2])
        self.assertEqual(execute.call_args.args[2]
                         ["WECOM_CLIPBOARD_COMPAT_READY"], "v1")

    def test_prefix_lock_serializes_launchers_but_not_other_prefixes(self):
        other = self.prefix / "other"
        other.mkdir()
        with LAUNCHER.prefix_lock(self.prefix):
            with self.assertRaises(LAUNCHER.PrefixUpgradeError):
                with LAUNCHER.prefix_lock(self.prefix, timeout=0):
                    self.fail("same prefix was not locked")
            with LAUNCHER.prefix_lock(other, timeout=0):
                pass
        with LAUNCHER.prefix_lock(self.prefix, timeout=0):
            pass

    def test_invalid_prefix_or_escaped_target_never_launches(self):
        with tempfile.TemporaryDirectory() as outside:
            escaped = Path(outside) / "target.dll"
            escaped.write_bytes(b"current")
            self.target.unlink()
            self.target.symlink_to(escaped)
            with mock.patch.object(LAUNCHER.sys, "argv", self.arguments), \
                    mock.patch.object(LAUNCHER.os, "execvpe") as execute:
                self.assertEqual(LAUNCHER.main(), 1)
            execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
