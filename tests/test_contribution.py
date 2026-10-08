"""保留原签名任务中的贡献格式检查，不调用签名验证服务。"""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "contribution", ROOT / "tools/check-contribution.py",
)
CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)


class ContributionTests(unittest.TestCase):
    def test_valid_feature_and_merge_commit(self):
        CHECK.validate("fix(clipboard): 修复图片粘贴", "fix/clipboard", [
            "fix(clipboard): 修复图片粘贴\n\nRefs #1\n",
            "chore(merge): 同步主线\n",
        ])

    def test_reject_invalid_metadata(self):
        cases = [
            ("不合规范", "fix/clipboard", ["fix: 修复"]),
            ("fix: 修复", "Clipboard", ["fix: 修复"]),
            ("fix: 修复", "fix/clipboard", ["不合规范"]),
            ("fix: 修复", "fix/clipboard", []),
            ("fix: 修复", "fix/clipboard", ["fix: 修复\n" + "x" * 81]),
        ]
        for title, branch, messages in cases:
            with self.subTest(title=title, branch=branch, messages=messages):
                with self.assertRaises(ValueError):
                    CHECK.validate(title, branch, messages)

    def test_event_checks_only_commits_outside_base(self):
        base, head = "a" * 40, "b" * 40
        event = {"pull_request": {
            "title": "fix: 修复", "base": {"sha": base},
            "head": {"sha": head, "ref": "fix/clipboard"},
        }}
        with patch.object(CHECK.subprocess, "check_output",
                          side_effect=[head + "\n", "fix: 修复\n"]) as run:
            CHECK.check_event(event)
        self.assertEqual(run.call_args_list[0].args[0],
                         ["git", "rev-list", f"{base}..{head}"])
        self.assertEqual(run.call_args_list[1].args[0],
                         ["git", "show", "-s", "--format=%B", head])

    def test_invalid_sha_rejected_before_git(self):
        event = {"pull_request": {
            "base": {"sha": "--help"}, "head": {"sha": "b" * 40},
        }}
        with patch.object(CHECK.subprocess, "check_output") as run:
            with self.assertRaises(ValueError):
                CHECK.check_event(event)
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
