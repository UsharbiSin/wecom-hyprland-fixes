"""剪贴板模块测试只处理合成路径与字节，不读取桌面选择。"""

import importlib.util
import json
import contextlib
import io
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "modules/clipboard"


def load_script(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


BUILD = load_script("clipboard_build", MODULE / "riched20/build.py")
VERIFY = load_script("clipboard_verify", MODULE / "riched20/verify.py")
LAUNCHER = load_script("clipboard_launcher",
                       MODULE / "riched20/launch-existing.py")
CONFIGURE = load_script("clipboard_config", MODULE / "outbound/configure.py")


GOLDEN_OLD_PROFILES = {
    "11.18-1": (
        "18117302c2dbc043e0b35aea4f6e40120dd95ffe2eb5720a80f0d7f522eb0cc3",
        "bbd98cd94d20d6858f99c7f5d5228bd76317b86dc0879e3f8f09780f4c7dfb12",
        "4060f8d64fdcab2b6a28cacde2632d904d15f9776594ae0c15796a5aec2c7c98",
    ),
    "11.19-1": (
        "3b903f943ca5f65215d78fedaf30260b8e7ce8bb7626325a4c21db0f57a40295",
        "4f8a275302cfc4cfbbf739d91615805fde85dda22b4bfaaae5f7ce40595f593a",
        "1460eafeff050df82db74d6e5dd193a241b487454621ffd9a5a1492acc373e24",
    ),
    "11.20-1": (
        "183c94c2f8f004acdd60455ea9199417ace0cb4ca3b1b4f4c46213487d778eec",
        "0dc20f8b28ca5e1e3e4d323b2ac18f395042f97494e2839a733deb54ebbbd677",
        "e657039330c470485f189706b48480f68420c43257c91f25fe3bcdb089978dcf",
    ),
}


class ClipboardGuards(unittest.TestCase):
    def test_old_profiles_preserve_independent_golden_fingerprints(self):
        # 固定此前发布的配对，不由当前配置互相推导，防止同步误改漏检。
        profiles = {p["wine_build"]: p for p in BUILD.PROFILES}
        compatible = {p["wine_build"]: p
                      for p in BUILD.COMPATIBILITY["profiles"]}
        module = json.loads((MODULE / "module.json").read_text())
        self.assertEqual(len(module["bindings"]), 2)
        for version, expected in GOLDEN_OLD_PROFILES.items():
            source, candidate, normalized = expected
            with self.subTest(version=version):
                self.assertEqual(profiles[version]["source_sha256"], source)
                self.assertEqual(profiles[version]["candidate_sha256"],
                                 candidate)
                self.assertEqual(compatible[version]["source_sha256"], source)
                self.assertEqual(compatible[version]["normalized_sha256"],
                                 normalized)
                for binding in module["bindings"]:
                    self.assertEqual(binding["sha256_pairs"][source],
                                     candidate)

    def test_builder_verifier_and_bindings_share_exact_profiles(self):
        module = json.loads((MODULE / "module.json").read_text())
        pairs = {p["source_sha256"]: p["candidate_sha256"]
                 for p in BUILD.PROFILES}
        self.assertEqual(len(pairs), 4)
        self.assertEqual(set(pairs.values()), VERIFY.FIXED)
        self.assertTrue(set(pairs).isdisjoint(pairs.values()))
        for binding in module["bindings"]:
            self.assertEqual(binding["sha256_pairs"], pairs)

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

    def test_metadata_fingerprint_does_not_trust_pe_supplied_ranges(self):
        data = bytearray(BUILD.COMPATIBILITY["file_size"])
        before = BUILD.normalized_digest(data)
        for offset, _ in BUILD.COMPATIBILITY["metadata_ranges"]:
            changed = bytearray(data)
            changed[offset] = 123
            self.assertEqual(before, BUILD.normalized_digest(changed))
        for offset in (0x3C, 0x178, 0x180, 0x32CB5, 0x3E400,
                       0x3F160, 0x68000, 0x6AB32 - 1, 0x694DC - 0x3000,
                       0x78010):
            changed = bytearray(data)
            changed[offset] = 123
            self.assertNotEqual(before, BUILD.normalized_digest(changed))

    def test_truncation_and_extra_data_cannot_match_metadata_profile(self):
        for size in (BUILD.COMPATIBILITY["file_size"] - 1,
                     BUILD.COMPATIBILITY["file_size"] + 1):
            with self.assertRaisesRegex(ValueError, "文件尺寸"):
                BUILD.normalized_digest(bytes(size))

    def test_metadata_profiles_correspond_to_exact_known_baselines(self):
        self.assertEqual(
            {p["source_sha256"] for p in BUILD.PROFILES},
            {p["source_sha256"] for p in BUILD.COMPATIBILITY["profiles"]})
        self.assertEqual(len(BUILD.STUB_SHA256), 64)

    @unittest.skipUnless(os.environ.get("WECOM_TEST_RICHEDIT") == "1",
                         "设置 WECOM_TEST_RICHEDIT=1 验证本机 DLL 的兼容变体")
    def test_native_metadata_variants_and_sensitive_changes(self):
        original = BUILD.SOURCE.read_bytes()
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            for index, (offset, _) in enumerate(
                    BUILD.COMPATIBILITY["metadata_ranges"]):
                changed = bytearray(original)
                changed[offset] ^= 1
                source = folder / f"variant-{index}.dll"
                source.write_bytes(changed)
                output = folder / f"output-{index}"
                BUILD.build(source, output)
                VERIFY.verify(output)
                data = (output / "riched20.dll").read_bytes()
                self.assertTrue(LAUNCHER.candidate_matches(
                    changed, output / "riched20.dll"))
                corrupted = bytearray(data)
                corrupted[BUILD.STUB + 3] ^= 1
                with self.assertRaises(ValueError):
                    VERIFY.map_pe(corrupted, 0x7AC00000)
            for offset in (0x178, 0x32CB5, 0x32D18, 0x2AEB8,
                           0x3E400, 0x3F160, 0x66000, 0x68000, 0x78010):
                changed = bytearray(original)
                changed[offset] ^= 1
                with self.assertRaises(ValueError):
                    BUILD.validate_source(changed)
            source = folder / "current.dll"
            source.write_bytes(original)
            candidate, _ = LAUNCHER.prepare(source, folder / "cache")
            with mock.patch.object(LAUNCHER.BUILDER, "build") as rebuild:
                cached, _ = LAUNCHER.prepare(source, folder / "cache")
            rebuild.assert_not_called()
            self.assertEqual(candidate, cached)

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
