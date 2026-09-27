"""共享文档输入法的离线边界回归，不读取账号文档或操作桌面。"""

import contextlib
import hashlib
import io
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "modules/docs-ime"


class DocumentImePolicyTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("gcc"), "需要 gcc 编译候选框策略测试")
    def test_candidate_coordinates_and_stale_replies(self):
        with tempfile.TemporaryDirectory(prefix="wecom-ime-candidate-") as raw:
            executable = Path(raw) / "candidate-policy"
            build = subprocess.run([
                "gcc", "-std=c11", "-Wall", "-Wextra", "-Werror",
                "-fsanitize=undefined", "-fno-sanitize-recover=all",
                str(ROOT / "tests/docs-ime-candidate-policy.c"),
                "-o", str(executable),
            ], capture_output=True, text=True, timeout=30)
            self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
            result = subprocess.run(
                [str(executable)], capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(
                result.returncode, 0, result.stdout + result.stderr,
            )
            self.assertIn("candidate coordinate and routing checks passed",
                          result.stdout)

    @unittest.skipUnless(shutil.which("gcc"), "需要 gcc 编译纯 C 策略测试")
    def test_protocol_and_recipient_boundaries(self):
        with tempfile.TemporaryDirectory(prefix="wecom-ime-policy-") as raw:
            executable = Path(raw) / "policy"
            build = subprocess.run([
                "gcc", "-std=c11", "-Wall", "-Wextra", "-Werror",
                "-fsanitize=undefined", "-fno-sanitize-recover=all",
                str(ROOT / "tests/docs-ime-policy.c"), "-o", str(executable),
            ], capture_output=True, text=True, timeout=30)
            self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
            result = subprocess.run(
                [str(executable)], capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(
                result.returncode, 0, result.stdout + result.stderr,
            )
            self.assertIn("destination checks passed", result.stdout)

    @unittest.skipUnless(shutil.which("i686-w64-mingw32-gcc"),
                         "需要 32 位 MinGW 编译隔离 GUI 复现器")
    def test_standalone_probe_builds_without_app_files(self):
        with tempfile.TemporaryDirectory(prefix="wecom-ime-probe-") as raw:
            output = Path(raw) / "docs-ime-probe.exe"
            result = subprocess.run([
                "i686-w64-mingw32-gcc", "-std=c11", "-Wall", "-Wextra",
                "-Werror", "-municode", str(MODULE / "probe.c"),
                "-o", str(output), "-limm32", "-luser32",
            ], capture_output=True, text=True, timeout=30)
            self.assertEqual(
                result.returncode, 0, result.stdout + result.stderr,
            )
            self.assertEqual(output.read_bytes()[:2], b"MZ")

    @unittest.skipUnless(shutil.which("i686-w64-mingw32-gcc"),
                         "需要 32 位 MinGW 编译输入法桥和守护进程")
    def test_production_bridge_and_guard_build_without_app_files(self):
        with tempfile.TemporaryDirectory(prefix="wecom-ime-build-") as raw:
            folder = Path(raw)
            targets = [
                ("bridge.c", "docs-ime-bridge.dll",
                 ["-shared", "-Wl,--kill-at", "-limm32", "-luser32"]),
                ("guard.c", "docs-ime-guard.exe", ["-municode", "-luser32"]),
            ]
            for source, name, flags in targets:
                with self.subTest(source=source):
                    output = folder / name
                    result = subprocess.run([
                        "i686-w64-mingw32-gcc", "-std=c11", "-O2",
                        "-Wall", "-Wextra", "-Werror",
                        str(MODULE / source), "-o", str(output), *flags,
                    ], cwd=folder, capture_output=True, text=True, timeout=30)
                    self.assertEqual(
                        result.returncode, 0, result.stdout + result.stderr,
                    )
                    self.assertEqual(output.read_bytes()[:2], b"MZ")


class DocumentImeRuntimeGuards(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location(
            "docs_ime_start", MODULE / "start.py",
        )
        self.start = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.start)
        self.temporary = tempfile.TemporaryDirectory(prefix="wecom-ime-guard-")
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.prefix = self.folder / "专用前缀"
        self.app = self.prefix / "drive_c/Program Files (x86)/WXWork"
        self.version = self.start.VERSION
        self.components = [
            ("{prefix}/drive_c/windows/syswow64/imm32.dll", b"known imm32"),
            ("{app_dir}/{app_version}/WXWorkWeb.exe", b"known CEF host"),
            (str(self.folder / "runtime/win32u.so"), b"known Unix ABI"),
        ]
        self.paths = []
        self.manifest = {"guards": []}
        context = {"prefix": str(self.prefix), "app_dir": str(self.app),
                   "app_version": self.version}
        for template, content in self.components:
            path = Path(template.format_map(context))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            self.paths.append(path)
            self.manifest["guards"].append({
                "target": template,
                "sha256": hashlib.sha256(content).hexdigest(),
            })

    def test_all_expected_components_and_version_are_required(self):
        self.start.verify(self.prefix, self.version, self.manifest)
        with mock.patch.object(self.start.hashlib, "file_digest") as digest:
            with self.assertRaisesRegex(ValueError, "版本"):
                self.start.verify(self.prefix, "5.0.11.6019", self.manifest)
        digest.assert_not_called()

    def test_modified_component_is_rejected_without_rewriting_it(self):
        for index, path in enumerate(self.paths):
            with self.subTest(component=path.name):
                original = path.read_bytes()
                changed = original + b" unsupported ABI"
                path.write_bytes(changed)
                with self.assertRaisesRegex(ValueError, "哈希"):
                    self.start.verify(self.prefix, self.version, self.manifest)
                self.assertEqual(path.read_bytes(), changed)
                path.write_bytes(self.components[index][1])

    def test_missing_required_component_is_rejected(self):
        for index, path in enumerate(self.paths):
            with self.subTest(component=path.name):
                path.unlink()
                with self.assertRaises(OSError):
                    self.start.verify(self.prefix, self.version, self.manifest)
                self.assertFalse(path.exists())
                path.write_bytes(self.components[index][1])

    def test_same_named_executable_in_other_prefix_is_not_trusted(self):
        other = self.folder / "other-prefix"
        for path in self.paths[:2]:
            target = other / path.relative_to(self.prefix)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(path.read_bytes())
        self.start.verify(other, self.version, self.manifest)
        copied_host = other / self.paths[1].relative_to(self.prefix)
        copied_host.write_bytes(b"same version directory; different executable")
        with self.assertRaisesRegex(ValueError, "哈希"):
            self.start.verify(other, self.version, self.manifest)

    def test_optimized_cli_rejects_unrecognized_prefix_files(self):
        result = subprocess.run([
            sys.executable, "-O", str(MODULE / "start.py"),
            "--prefix", str(self.prefix), "--app-version", self.version,
            "--verify-only",
        ], capture_output=True, text=True, timeout=20)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("输入法桥未启动", result.stderr)
        self.assertEqual(self.paths[0].read_bytes(), self.components[0][1])

    def test_start_validates_before_exec_and_sets_resolved_environment(self):
        alias = self.folder / "prefix-alias"
        alias.symlink_to(self.prefix, target_is_directory=True)
        arguments = ["start.py", "--prefix", str(alias)]
        original_verify = self.start.verify
        calls = mock.Mock()

        def checked(prefix, version):
            original_verify(prefix, version, self.manifest)

        environment = {
            "KEEP_ME": "sentinel", "WINEPREFIX": "/wrong-prefix",
            "WECOM_DOCS_IME_APP_DIR": "incorrect application directory",
        }
        with mock.patch.object(
                self.start, "verify", side_effect=checked) as v, \
                mock.patch.object(self.start.os, "execvpe") as execute, \
                mock.patch.dict(os.environ, environment, clear=True), \
                mock.patch.object(sys, "argv", arguments):
            calls.attach_mock(v, "verify")
            calls.attach_mock(execute, "execute")
            self.start.main()
        self.assertEqual([call[0] for call in calls.mock_calls],
                         ["verify", "execute"])
        v.assert_called_once_with(str(alias), self.version)
        executable, command, actual_env = execute.call_args.args
        self.assertEqual(executable, "wine")
        self.assertEqual(command, [
            "wine", str(MODULE / "docs-ime-guard.exe"),
            "--app-version", self.version,
        ])
        self.assertEqual(actual_env["WINEPREFIX"], str(self.prefix.resolve()))
        self.assertEqual(actual_env["WECOM_DOCS_IME_APP_DIR"],
                         r"C:\Program Files (x86)\WXWork")
        self.assertEqual(actual_env["KEEP_ME"], "sentinel")

    def test_start_never_executes_after_failed_runtime_verification(self):
        arguments = ["start.py", "--prefix", str(self.prefix)]
        output = io.StringIO()
        with mock.patch.object(self.start, "verify",
                               side_effect=ValueError("unverified ABI")), \
                mock.patch.object(self.start.os, "execvpe") as execute, \
                mock.patch.object(sys, "argv", arguments), \
                contextlib.redirect_stderr(output):
            with self.assertRaises(SystemExit) as result:
                self.start.main()
        self.assertEqual(result.exception.code, 1)
        self.assertIn("unverified ABI", output.getvalue())
        execute.assert_not_called()

    def test_failed_verification_never_reaches_compiler(self):
        build = self.folder / "build"
        build.mkdir()
        sentinel = build / "keep-existing.txt"
        sentinel.write_text("existing build remains untouched")
        commands = self.folder / "commands"
        commands.mkdir()
        compiler = commands / "i686-w64-mingw32-gcc"
        compiler.write_text(
            '#!/bin/sh\n: > "$WECOM_TEST_COMPILER_MARKER"\nexit 99\n',
        )
        compiler.chmod(0o755)
        marker = self.folder / "compiler-was-run"
        environment = dict(os.environ)
        environment.update(
            PATH=str(commands) + os.pathsep + environment.get("PATH", ""),
            WECOM_PREFIX=str(self.prefix), WECOM_BUILD_DIR=str(build),
            WECOM_TEST_COMPILER_MARKER=str(marker), PYTHONOPTIMIZE="1",
        )
        for version in (self.version, "unverified-version"):
            with self.subTest(version=version):
                environment["WECOM_APP_VERSION"] = version
                result = subprocess.run(
                    ["bash", "build.sh"], cwd=MODULE, env=environment,
                    capture_output=True, text=True, timeout=20,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(marker.exists(), result.stdout + result.stderr)
                self.assertEqual(list(build.iterdir()), [sentinel])
                self.assertEqual(sentinel.read_text(),
                                 "existing build remains untouched")


if __name__ == "__main__":
    unittest.main()
