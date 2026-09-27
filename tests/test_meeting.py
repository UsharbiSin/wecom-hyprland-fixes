"""会议运行库安装器保护测试，不运行 Wine 或微软安装器。"""
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "install_vc_runtime", ROOT / "modules/meeting/install-vc-runtime.py",
)
runtime = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runtime)


class MeetingTests(unittest.TestCase):
    def test_uninitialized_prefix_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ValueError, "未初始化|不是已初始化"):
                runtime.validate(Path(temporary), Path(temporary) / "a.exe")

    def test_unknown_installer_hash_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "user.reg").touch()
            installer = root / "a.exe"
            installer.write_bytes(b"not a trusted installer")
            with self.assertRaisesRegex(ValueError, "版本不匹配"):
                runtime.validate(root, installer)

    def test_running_target_prefix_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            process = root / "123"
            process.mkdir()
            (process / "environ").write_bytes(b"WINEPREFIX=/test/prefix\0")
            (process / "cmdline").write_bytes(b"WXWork.exe\0")
            with self.assertRaisesRegex(ValueError, "仍有 Wine 进程"):
                runtime.ensure_stopped(Path("/test/prefix"), root)
            runtime.ensure_stopped(Path("/different/prefix"), root)

    def test_alias_and_default_prefix_with_dll_host_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prefix = root / ".wine"
            prefix.mkdir()
            alias = root / "alias"
            alias.symlink_to(prefix, target_is_directory=True)
            process = root / "123"
            process.mkdir()
            (process / "cmdline").write_bytes(b"--module=meeting.dll\0")
            for environment in (
                os.fsencode("WINEPREFIX=" + str(alias)) + b"\0",
                os.fsencode("HOME=" + str(root)) + b"\0",
            ):
                (process / "environ").write_bytes(environment)
                with self.assertRaisesRegex(ValueError, "仍有 Wine 进程"):
                    runtime.ensure_stopped(prefix, root)

    def test_default_mode_only_plans(self):
        arguments = ["install-vc-runtime.py", "input.exe",
                     "--prefix", "/test/prefix"]
        with mock.patch("sys.argv", arguments), \
                mock.patch.object(runtime, "validate") as validate, \
                mock.patch.object(runtime, "ensure_stopped") as stopped, \
                mock.patch.object(runtime.subprocess, "run") as run, \
                contextlib.redirect_stdout(io.StringIO()):
            validate.return_value = (Path("/test/prefix"), Path("/input.exe"))
            runtime.main()
        stopped.assert_not_called()
        run.assert_not_called()

    def test_apply_checks_processes_before_execution(self):
        arguments = ["install-vc-runtime.py", "input.exe", "--apply",
                     "--prefix", "/test/prefix"]
        with mock.patch("sys.argv", arguments), \
                mock.patch.object(runtime, "validate") as validate, \
                mock.patch.object(runtime, "ensure_stopped") as stopped, \
                mock.patch.object(runtime.subprocess, "run") as run, \
                contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            validate.return_value = (Path("/test/prefix"), Path("/input.exe"))
            stopped.side_effect = ValueError("仍有 Wine 进程")
            with self.assertRaises(SystemExit):
                runtime.main()
        run.assert_not_called()


@unittest.skipUnless(os.environ.get("WECOM_TEST_NATIVE") == "1",
                     "设置 WECOM_TEST_NATIVE=1 才运行隔离 Wine 测试")
class MeetingNativeTests(unittest.TestCase):
    def test_32bit_export_and_failure_results(self):
        harness = r"""
#include <windows.h>
#include <stdio.h>
typedef HRESULT (WINAPI *query_fn)(const GUID *, int *, void *);
int main(void) {
    HMODULE module = LoadLibraryA("SLWGA.dll");
    if (!module) return 10;
    query_fn query = (query_fn)(void *)GetProcAddress(
        module, "SLIsGenuineLocal");
    if (!query) return 11;
    GUID application = {0};
    int state = 123;
    if (query(NULL, &state, NULL) != E_INVALIDARG || state != 123)
        return 12;
    if (query(&application, NULL, NULL) != E_INVALIDARG) return 13;
    if (query(&application, &state, NULL) != E_NOTIMPL || state != 4)
        return 14;
    FreeLibrary(module);
    puts("32 位导出和错误返回验证通过");
    return 0;
}
"""
        with tempfile.TemporaryDirectory(prefix="wecom-meeting-") as raw:
            root = Path(raw)
            build = root / "build"
            environment = dict(os.environ)
            environment.update(WECOM_BUILD_DIR=str(build),
                               WECOM_APP_VERSION="5.0.11.6018")
            subprocess.run(["bash", "build.sh"], env=environment,
                           cwd=ROOT / "modules/meeting", check=True)
            source = build / "harness.c"
            source.write_text(harness)
            subprocess.run(
                ["i686-w64-mingw32-gcc", "-Wall", "-Wextra", "-Werror",
                 "-o", str(build / "harness.exe"), str(source)],
                check=True,
            )
            environment.update(
                WINEPREFIX=str(root / "prefix"), WINEDEBUG="-all",
                WINEDLLOVERRIDES=("winemenubuilder.exe=d;winewayland.drv=d"),
                DISPLAY="", WAYLAND_DISPLAY="",
            )
            try:
                result = subprocess.run(
                    ["wine", str(build / "harness.exe")], cwd=build,
                    env=environment, capture_output=True, text=True,
                    timeout=90,
                )
                self.assertEqual(result.returncode, 0,
                                 result.stdout + result.stderr)
            finally:
                # 进程自行退出时，-k 可返回“没有活动服务”的非零状态。
                subprocess.run(["wineserver", "-k"], env=environment,
                               timeout=10, check=False)
                subprocess.run(["wineserver", "-w"], env=environment,
                               timeout=10, check=True)


if __name__ == "__main__":
    unittest.main()
