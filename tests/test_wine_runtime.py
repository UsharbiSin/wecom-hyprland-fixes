import copy
import importlib.util
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "wine_runtime", ROOT / "tools/wine_runtime.py")
RUNTIME = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNTIME)


class RuntimeSelection(unittest.TestCase):
    def setUp(self):
        self.policy = {"default": "new", "profiles": {
            "old": {"system_identity": {"server": "old-original"},
                    "server_sha256": "old-fixed"},
            "new": {"system_identity": {"server": "new-original"},
                    "server_sha256": "new-fixed"}}}

    def test_known_upgrade_and_downgrade(self):
        for version in ("old", "new", "old", "new"):
            profile, reason = RUNTIME.choose(
                self.policy, {"server": version + "-original"})
            self.assertEqual((profile, reason),
                             (version, "system-matched"))

    def test_unknown_upgrade_retains_complete_verified_runtime(self):
        self.assertEqual(RUNTIME.choose(self.policy, {"server": "future"}),
                         ("new", "verified-fallback"))

    def test_system_upgrade_during_session_reuses_active_version(self):
        self.assertEqual(RUNTIME.choose(
            self.policy, {"server": "new-original"}, "old-fixed"),
            ("old", "existing-session"))

    def test_unknown_active_server_is_not_silently_mixed(self):
        with self.assertRaisesRegex(ValueError, "不配套"):
            RUNTIME.choose(self.policy, {}, "unknown-server")


class BundleIntegrity(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        for name in (*RUNTIME.TREES, "usr/bin"):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        names = (*RUNTIME.IDENTITY, "patched-wineserver")
        for name in names:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(name)
        files = {name: RUNTIME.digest(self.root / name) for name in names}
        self.profile = {
            "root": str(self.root), "files": files, "links": {},
            "system_identity": {p: files[p] for p in RUNTIME.IDENTITY},
            "server_sha256": files["patched-wineserver"]}

    def test_valid_bundle(self):
        RUNTIME.verify_bundle(self.profile)

    def test_changed_library_cannot_use_old_identity(self):
        (self.root / RUNTIME.IDENTITY[-1]).write_text("changed")
        with self.assertRaisesRegex(ValueError, "文件校验失败"):
            RUNTIME.verify_bundle(self.profile)

    def test_replaced_file_symlink_is_rejected(self):
        path = self.root / "patched-wineserver"
        path.unlink()
        path.symlink_to("usr/bin/wineserver")
        with self.assertRaisesRegex(ValueError, "文件校验失败"):
            RUNTIME.verify_bundle(self.profile)

    def test_mismatched_server_record_is_rejected(self):
        profile = copy.deepcopy(self.profile)
        profile["server_sha256"] = "old"
        with self.assertRaisesRegex(ValueError, "服务修复记录"):
            RUNTIME.verify_bundle(profile)

    def test_unrecorded_executable_is_rejected(self):
        (self.root / "usr/bin/unrecorded").write_text("untrusted")
        with self.assertRaisesRegex(ValueError, "文件集合"):
            RUNTIME.verify_bundle(self.profile)

    def test_missing_tree_is_rejected(self):
        (self.root / "usr/share/wine").rmdir()
        with self.assertRaisesRegex(ValueError, "缺少完整目录"):
            RUNTIME.verify_bundle(self.profile)

    def test_directory_symlink_is_rejected(self):
        path = self.root / "usr/share/wine"
        path.rmdir()
        path.symlink_to("../../lib/wine", target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "缺少完整目录"):
            RUNTIME.verify_bundle(self.profile)
