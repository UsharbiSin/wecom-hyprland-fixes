"""验证版本门禁与发版说明不会跨版本泄漏内容。"""

import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools/release-notes.py"
SPEC = importlib.util.spec_from_file_location("release_notes", SCRIPT)
RELEASE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RELEASE)


class ReleaseNotesTests(unittest.TestCase):
    def test_only_requested_version_including_subsections(self):
        changelog = ("# 记录\n\n## 未发布\n未来\n\n"
                     "## v0.2.0 (2026-10-08)\n新功能\n\n"
                     "### 限制\n源码发行\n\n"
                     "## v0.1.0 (2026-10-08)\n旧版\n")
        self.assertEqual(RELEASE.release_notes(changelog, "v0.2.0"),
                         "新功能\n\n### 限制\n源码发行\n\n")
        self.assertEqual(RELEASE.release_notes(changelog, "v0.1.0"),
                         "旧版\n\n")

    def test_rejects_ambiguous_or_unsupported_tags(self):
        for tag in ("0.2.0", "v01.2.0", "v0.2", "v0.2.0-rc.1",
                    "v0.2.0+build", "v0.2.0\n", "v0.2.0;echo bad"):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                RELEASE.release_notes(f"## {tag}\n内容\n", tag)

    def test_requires_exact_unique_nonempty_section(self):
        for changelog in ("## v0.2.00\n错误版本\n",
                          "## v0.2.0\n\n## v0.1.0\n旧版\n",
                          "## v0.2.0\n一\n## v0.2.0 (日期)\n二\n"):
            with self.subTest(changelog=changelog):
                with self.assertRaises(ValueError):
                    RELEASE.release_notes(changelog, "v0.2.0")

    def test_cli_failure_leaves_existing_output_untouched(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "notes.md"
            output.write_text("已有说明", encoding="utf-8")
            result = subprocess.run([
                "python3", str(SCRIPT), "--tag=v999.0.0",
                f"--output={output}",
            ], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("有且仅有一个", result.stderr)
            self.assertEqual(output.read_text(encoding="utf-8"), "已有说明")


if __name__ == "__main__":
    unittest.main()
