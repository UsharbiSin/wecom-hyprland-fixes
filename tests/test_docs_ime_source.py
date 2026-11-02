"""源码核验拒绝缺失、改动与目录外文件，不能修改安装信任清单。"""
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest

MODULE = Path(__file__).resolve().parents[1] / "modules/docs-ime"


class SourceAuditTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location(
            "docs_ime_audit", MODULE / "audit-source.py")
        self.audit = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.audit)
        temporary = tempfile.TemporaryDirectory(prefix="wecom-ime-source-")
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.source = self.folder / "source"
        self.source.mkdir()
        self.header = self.source / "ntuser.h"
        self.header.write_bytes(b"verified private call layout")
        self.evidence = {"source_sha256": {"ntuser.h": hashlib.sha256(
            self.header.read_bytes()).hexdigest()}}

    def test_exact_source_matches_without_mutating_it(self):
        before = self.header.read_bytes()
        self.assertEqual(self.audit.audit(self.source, self.evidence),
                         {"ntuser.h": True})
        self.assertEqual(self.header.read_bytes(), before)

    def test_changed_and_missing_sources_require_review(self):
        self.header.write_bytes(b"changed call layout")
        self.assertEqual(self.audit.audit(self.source, self.evidence),
                         {"ntuser.h": False})
        self.header.unlink()
        self.assertEqual(self.audit.audit(self.source, self.evidence),
                         {"ntuser.h": False})

    def test_source_symlink_cannot_escape_audited_checkout(self):
        outside = self.folder / "outside.h"
        self.header.rename(outside)
        self.header.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "超出目录"):
            self.audit.audit(self.source, self.evidence)

    def test_source_evidence_preserves_each_reviewed_version(self):
        old = self.audit.source_evidence("11.20")
        self.assertEqual(old, self.audit.source_evidence("11.19"))
        new = self.audit.source_evidence("11.21")
        self.assertEqual(old["versions"], ["11.19", "11.20"])
        self.assertEqual(new["versions"], ["11.21"])
        self.assertNotEqual(old["source_sha256"]["include/ntuser.h"],
                            new["source_sha256"]["include/ntuser.h"])
        self.assertEqual(old["source_sha256"]["dlls/win32u/imm.c"],
                         new["source_sha256"]["dlls/win32u/imm.c"])
        for version in ("11.19", "11.20", "11.21"):
            evidence = self.audit.source_evidence(version)
            self.assertIn(version, evidence["versions"])
            for digest in evidence["source_sha256"].values():
                self.assertRegex(digest, r"^[a-f0-9]{64}$")

    def test_unknown_source_version_never_falls_back_to_old_evidence(self):
        for version in ("11.18", "11.22", "11.21-staging", "../11.21"):
            with self.subTest(version=version):
                with self.assertRaisesRegex(ValueError, "没有"):
                    self.audit.audit(self.source, version=version)


if __name__ == "__main__":
    unittest.main()
