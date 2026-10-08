"""拒绝没有已验证签名或偏离检出提交的发布标签。"""

from copy import deepcopy
import importlib.util
from pathlib import Path
import subprocess
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "verify_release_tag", ROOT / "tools/verify-release-tag.py",
)
VERIFY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFY)


class ReleaseTagTests(unittest.TestCase):
    def setUp(self):
        self.commit = "a" * 40
        self.tag_sha = "b" * 40
        self.reference = {
            "ref": "refs/tags/v0.2.0",
            "object": {"type": "tag", "sha": self.tag_sha},
        }
        self.annotated = {
            "sha": self.tag_sha, "tag": "v0.2.0",
            "verification": {"verified": True, "reason": "valid"},
            "object": {"type": "commit", "sha": self.commit},
        }

    def verify(self, reference=None, annotated=None):
        self.api = Mock(side_effect=[
            reference if reference is not None else self.reference,
            annotated if annotated is not None else self.annotated,
        ])
        VERIFY.verify_tag("owner/repo", "v0.2.0", self.commit, self.api)

    def test_signed_annotated_tag_points_to_checkout(self):
        self.verify()
        endpoints = [call.args[0] for call in self.api.call_args_list]
        self.assertEqual(endpoints, [
            "repos/owner/repo/git/ref/tags/v0.2.0",
            "repos/owner/repo/git/tags/" + self.tag_sha,
        ])

    def test_lightweight_tag_rejected_before_tag_object_request(self):
        self.reference["object"] = {"type": "commit", "sha": self.commit}
        with self.assertRaisesRegex(ValueError, "轻量标签"):
            self.verify()
        self.assertEqual(self.api.call_count, 1)

    def test_ref_name_and_object_sha_are_checked(self):
        variants = [
            {"ref": "refs/tags/v0.1.0", "object": self.reference["object"]},
            {"ref": self.reference["ref"],
             "object": {"type": "tag", "sha": "../../unexpected"}},
        ]
        for reference in variants:
            with self.subTest(reference=reference):
                with self.assertRaises(ValueError):
                    self.verify(reference=reference)
                self.assertEqual(self.api.call_count, 1)

    def test_unsigned_invalid_and_unverified_signatures_are_rejected(self):
        variants = [
            {}, {"verified": False, "reason": "unsigned"},
            {"verified": False, "reason": "invalid"},
            {"verified": True, "reason": "unknown_key"},
            {"verified": False, "reason": "valid"},
            {"verified": "true", "reason": "valid"},
        ]
        for verification in variants:
            with self.subTest(verification=verification):
                annotated = deepcopy(self.annotated)
                annotated["verification"] = verification
                with self.assertRaisesRegex(ValueError, "尚未验证"):
                    self.verify(annotated=annotated)

    def test_annotated_object_and_tag_name_must_match_reference(self):
        for field, value in (("sha", "c" * 40), ("tag", "v0.1.0")):
            with self.subTest(field=field):
                annotated = deepcopy(self.annotated)
                annotated[field] = value
                with self.assertRaisesRegex(ValueError, "对象或名称不一致"):
                    self.verify(annotated=annotated)

    def test_nested_tag_and_wrong_commit_target_are_rejected(self):
        for target in ({"type": "tag", "sha": self.commit},
                       {"type": "tree", "sha": self.commit},
                       {"type": "commit", "sha": "c" * 40}):
            with self.subTest(target=target):
                annotated = deepcopy(self.annotated)
                annotated["object"] = target
                with self.assertRaisesRegex(ValueError, "当前检出"):
                    self.verify(annotated=annotated)

    def test_invalid_inputs_fail_before_network(self):
        api = Mock()
        variants = [
            ("owner/repo", "v0.2.0;echo bad", self.commit),
            ("owner/repo", "v00.2.0", self.commit),
            ("owner/repo", "v0.2.0-rc.1", self.commit),
            ("owner/repo?unexpected", "v0.2.0", self.commit),
            ("owner/repo", "v0.2.0", "--help"),
        ]
        for args in variants:
            with self.subTest(args=args), self.assertRaises(ValueError):
                VERIFY.verify_tag(*args, api=api)
        api.assert_not_called()

    def test_api_failure_never_returns_success(self):
        api = Mock(side_effect=subprocess.CalledProcessError(1, ["gh"]))
        with self.assertRaises(subprocess.CalledProcessError):
            VERIFY.verify_tag("owner/repo", "v0.2.0", self.commit, api)


if __name__ == "__main__":
    unittest.main()
