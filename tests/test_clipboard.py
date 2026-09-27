"""剪贴板模块测试只处理合成路径与字节，不读取桌面选择。"""

import importlib.util
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "modules/clipboard"


def load_script(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


BUILD = load_script("clipboard_build", MODULE / "riched20/build.py")
VERIFY = load_script("clipboard_verify", MODULE / "riched20/verify.py")
CONFIGURE = load_script("clipboard_config", MODULE / "outbound/configure.py")


class ClipboardGuards(unittest.TestCase):
    def test_unknown_dll_is_rejected_before_output(self):
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            source = folder / "unknown.dll"
            source.write_bytes(b"MZ" + bytes(400))
            with self.assertRaisesRegex(ValueError, "哈希不匹配"):
                BUILD.build(source, folder / "output")
            self.assertFalse((folder / "output").exists())

    def test_unknown_machine_code_is_never_mapped(self):
        with self.assertRaisesRegex(ValueError, "哈希不匹配"):
            VERIFY.map_pe(b"MZ" + bytes(400), 0x7AC00000)

    def test_source_gate_survives_python_optimization(self):
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            source = folder / "unknown.dll"
            source.write_bytes(b"not a supported DLL")
            result = subprocess.run([
                "python3", "-O", str(MODULE / "riched20/build.py"),
                "--source", str(source), "--output", str(folder / "output"),
            ], capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((folder / "output").exists())

    def test_prefix_requires_absolute_path_and_no_controls(self):
        for prefix in ("relative", "/tmp/line\nbreak", "/tmp/null\0byte"):
            with self.subTest(prefix=repr(prefix)):
                with self.assertRaises(ValueError):
                    CONFIGURE.render_prefix(prefix)

    @unittest.skipUnless(shutil.which("g++"), "需要 g++ 编译纯策略测试")
    def test_outbound_policy_without_clipboard(self):
        with tempfile.TemporaryDirectory() as name:
            executable = Path(name) / "policy-test"
            subprocess.run([
                "g++", "-std=c++17", "-Wall", "-Wextra", "-Werror",
                str(MODULE / "outbound/check-policy.cpp"),
                "-o", str(executable),
            ], check=True, capture_output=True)
            subprocess.run([str(executable)], check=True)

    @unittest.skipUnless(shutil.which("g++"), "需要 g++ 校验路径转义")
    def test_generated_header_cannot_inject_cpp(self):
        # 路径同时含中文、空格、引号、反斜杠及代码形状，仍须是完整字符串。
        prefix = '/tmp/企业 微信/";#error 注入/\\🙂'
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            header = CONFIGURE.render_prefix(prefix)
            self.assertTrue(all(len(line) <= 80
                                for line in header.splitlines()))
            (folder / "prefix-config.h").write_text(header)
            (folder / "test.cpp").write_text(
                '#include "prefix-config.h"\n'
                '#include <cstdio>\n'
                'int main() { for (wchar_t c : kPrefix) '
                'if (c) std::printf("%x\\n", (unsigned)c); }\n'
            )
            subprocess.run([
                "g++", "-std=c++17", str(folder / "test.cpp"),
                "-o", str(folder / "test"),
            ], check=True, capture_output=True)
            result = subprocess.run([str(folder / "test")],
                                    check=True, capture_output=True, text=True)
            decoded = "".join(chr(int(line, 16))
                              for line in result.stdout.splitlines())
            self.assertEqual(decoded, str(Path(prefix).resolve()))


if __name__ == "__main__":
    unittest.main()
