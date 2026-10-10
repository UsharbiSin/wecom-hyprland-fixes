"""双版本输入桥和统一管理器必须信任同一套完整组件，不能逐项混用。"""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "modules/docs-ime"
sys.path.insert(0, str(ROOT / "tools"))
import manager


class DocumentImeProfileTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location(
            "docs_ime_profile_start", MODULE / "start.py")
        self.start = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.start)
        temporary = tempfile.TemporaryDirectory(prefix="wecom-ime-profiles-")
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.version = self.start.VERSION
        self.prefix = self.folder / "prefix"
        self.context = manager.variables(self.prefix, self.version, "docs-ime")
        self.paths = [self.folder / name for name in
                      ("system-imm32.dll", "prefix-imm32.dll", "win32u.so")]
        self.manifest = {"guards": []}
        for path in self.paths:
            values = {profile: self.content(path, profile) for profile in
                      ("wine-11.18-1", "wine-11.19-1")}
            self.manifest["guards"].append({
                "target": str(path), "sha256_by_profile": {
                    profile: hashlib.sha256(value).hexdigest()
                    for profile, value in values.items()
                },
            })
        self.host = self.folder / "WXWorkWeb.exe"
        self.host.write_bytes(b"verified application host")
        self.manifest["guards"].append({
            "target": str(self.host), "sha256": manager.digest(self.host),
        })
        self.write_profile("wine-11.18-1")

    @staticmethod
    def content(path, profile):
        return (path.name + ":" + profile).encode()

    def write_profile(self, profile):
        for path in self.paths:
            path.write_bytes(self.content(path, profile))

    def assert_rejected(self, pattern="哈希|版本混用"):
        for name in ("start", "manager"):
            with self.subTest(validator=name):
                with self.assertRaisesRegex(ValueError, pattern):
                    if name == "start":
                        self.start.verify(self.prefix, self.version,
                                          self.manifest)
                    else:
                        manager.check_targets(self.manifest, self.context)

    def test_complete_old_and_new_profiles_are_accepted(self):
        for profile in ("wine-11.18-1", "wine-11.19-1"):
            with self.subTest(profile=profile):
                self.write_profile(profile)
                self.assertEqual(self.start.verify(
                    self.prefix, self.version, self.manifest), profile)
                targets = manager.check_targets(self.manifest, self.context)
                self.assertEqual(len(targets), len(self.paths) + 1)

    def test_each_mixed_system_or_prefix_component_is_rejected(self):
        for profile, other in (("wine-11.18-1", "wine-11.19-1"),
                               ("wine-11.19-1", "wine-11.18-1")):
            for path in self.paths:
                with self.subTest(profile=profile, component=path.name):
                    self.write_profile(profile)
                    path.write_bytes(self.content(path, other))
                    self.assert_rejected("版本混用")

    def test_pairwise_overlap_cannot_replace_one_common_profile(self):
        keys = (("a", "b"), ("b", "c"), ("a", "c"))
        for path, item, profiles in zip(
                self.paths, self.manifest["guards"], keys):
            item["sha256_by_profile"] = {
                profile: manager.digest(path) for profile in profiles}
        self.assert_rejected("版本混用")

    def test_unknown_component_is_rejected_without_rewriting(self):
        for path in self.paths:
            with self.subTest(component=path.name):
                self.write_profile("wine-11.19-1")
                path.write_bytes(b"unknown Wine build")
                self.assert_rejected()
                self.assertEqual(path.read_bytes(), b"unknown Wine build")

    def test_fixed_application_hash_is_still_required(self):
        self.write_profile("wine-11.19-1")
        self.host.write_bytes(b"changed application")
        self.assert_rejected()

    def test_invalid_or_empty_profile_configuration_is_rejected(self):
        for value in ({}, [], None):
            with self.subTest(value=value):
                self.manifest["guards"][0]["sha256_by_profile"] = value
                self.assert_rejected()

    def test_empty_fixed_hash_never_silently_passes(self):
        for value in (None, "", 0):
            with self.subTest(value=value):
                self.manifest["guards"][-1]["sha256"] = value
                self.assert_rejected()

    def test_guard_without_hash_never_silently_passes(self):
        self.manifest["guards"][0].pop("sha256_by_profile")
        self.assert_rejected("缺少哈希")

    def test_missing_profile_file_is_rejected(self):
        self.paths[0].unlink()
        with self.assertRaises(OSError):
            self.start.verify(self.prefix, self.version, self.manifest)
        with self.assertRaisesRegex(ValueError, "缺少目标"):
            manager.check_targets(self.manifest, self.context)

    def test_verify_only_reports_profile_without_launching(self):
        arguments = ["start.py", "--prefix", str(self.prefix), "--verify-only"]
        with mock.patch.object(sys, "argv", arguments), \
                mock.patch.object(self.start, "verify",
                                  return_value="wine-11.19-1"), \
                mock.patch.object(self.start.os, "execvpe") as execute, \
                mock.patch("builtins.print") as output:
            self.assertEqual(self.start.main(), 0)
        execute.assert_not_called()
        output.assert_called_once_with(
            "输入法桥组件校验通过：wine-11.19-1", flush=True)

    def test_stop_remains_available_after_wine_upgrade(self):
        arguments = ["start.py", "--prefix", str(self.prefix), "--stop"]
        with mock.patch.object(sys, "argv", arguments), \
                mock.patch.object(self.start, "verify") as verify, \
                mock.patch.object(self.start.os, "execvpe") as execute:
            self.start.main()
        verify.assert_not_called()
        self.assertEqual(execute.call_args.args[1][-1], "--stop")

    def test_production_manifest_keeps_seven_complete_wine_pairs(self):
        manifest = json.loads((MODULE / "module.json").read_text())
        profiles = [item for item in manifest["guards"]
                    if "sha256_by_profile" in item]
        fixed = [item for item in manifest["guards"] if "sha256" in item]
        self.assertEqual(len(profiles), 7)
        self.assertEqual(len(fixed), 3)
        for item in profiles:
            allowed = item["sha256_by_profile"]
            self.assertEqual(set(allowed), {"wine-11.18-1", "wine-11.19-1"})
            for value in allowed.values():
                self.assertRegex(value, r"^[a-f0-9]{64}$")
        for item in fixed:
            self.assertTrue(item["target"].startswith("{app_dir}/"))
            self.assertRegex(item["sha256"], r"^[a-f0-9]{64}$")


if __name__ == "__main__":
    unittest.main()
