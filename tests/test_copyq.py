"""CopyQ history policy and reversible command installation."""
import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import copyq as c
import manager


class FakeClient:
    def __init__(self):
        self.unrelated = "[Command]\nName=existing\nMatch=hello\\d+\n"
        self.rules = []
        self.names = ["existing"]
        self.writes = 0

    def snapshot(self):
        return {
            "exported": json.dumps([self.unrelated, self.rules]),
            "rule_exports": self.rules[:],
            "names": self.names[:], "unrelated_export": self.unrelated,
        }

    def exported_rule(self, rule):
        return "[Command]\n" + json.dumps(rule, ensure_ascii=False)

    def apply(self, before, operation, rule=None):
        if before != self.snapshot():
            raise ValueError("commands changed")
        self.writes += 1
        self.rules = [self.exported_rule(rule)] if rule else []
        return self.snapshot()


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.client = FakeClient()
        self.output = contextlib.redirect_stdout(io.StringIO())
        self.output.__enter__()
        self.addCleanup(self.output.__exit__, None, None, None)

    def install(self, dry_run=False):
        c.install(self.directory, self.client, dry_run)

    def test_install_idempotency_private_backup_and_doctor(self):
        original = self.client.unrelated
        self.install()
        self.install()
        c.doctor(self.directory, self.client)
        self.assertEqual(self.client.writes, 1)
        record = c.read_record(self.directory)
        backup = self.directory / record["backup"]
        self.assertIn(original, json.loads(backup.read_text()))
        self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
        self.assertEqual(record["status"], "installed")
        self.assertEqual(len(list(self.directory.glob("commands-*.ini"))), 1)

    def test_dry_run_does_not_write_config_or_state(self):
        self.install(dry_run=True)
        self.assertEqual(self.client.writes, 0)
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_remove_preserves_later_unrelated_changes_and_backup(self):
        self.install()
        self.client.unrelated += "Command=changed after installation\n"
        current = self.client.unrelated
        c.remove(self.directory, self.client)
        c.remove(self.directory, self.client)
        self.assertEqual(self.client.unrelated, current)
        self.assertEqual(self.client.rules, [])
        self.assertEqual(self.client.writes, 2)
        self.assertEqual(c.read_record(self.directory)["status"], "removed")
        self.assertEqual(len(list(self.directory.glob("commands-*.ini"))), 1)

    def test_same_name_and_foreign_matching_rule_are_not_adopted(self):
        self.client.names += [c.RULE_NAME]
        with self.assertRaisesRegex(ValueError, "同名"):
            self.install()
        self.client.names = []
        self.client.rules = [self.client.exported_rule(c.rule_definition())]
        with self.assertRaisesRegex(ValueError, "原安装方式"):
            self.install()
        self.assertEqual(self.client.writes, 0)
        self.assertFalse((self.directory / "installed.json").exists())

    def test_modified_or_duplicate_owned_rule_blocks_remove_and_doctor(self):
        self.install()
        original = self.client.rules[:]
        for rules in (["modified"], original * 2):
            self.client.rules = rules
            with self.assertRaisesRegex(ValueError, "已被修改或重复"):
                c.remove(self.directory, self.client)
            with self.assertRaisesRegex(ValueError, "缺失、重复或已修改"):
                c.doctor(self.directory, self.client)
        self.assertEqual(self.client.writes, 1)
        self.assertEqual(c.read_record(self.directory)["status"], "installed")

    def test_missing_rule_can_be_removed_but_is_not_silently_reinstalled(self):
        self.install()
        self.client.rules = []
        with self.assertRaisesRegex(ValueError, "丢失"):
            self.install()
        c.remove(self.directory, self.client)
        self.install()
        self.assertEqual(self.client.writes, 2)

    def test_race_leaves_prepared_record_and_backup(self):
        with patch.object(self.client, "apply", side_effect=ValueError("race")):
            with self.assertRaisesRegex(ValueError, "race"):
                self.install()
        record = c.read_record(self.directory)
        self.assertEqual(record["status"], "prepared")
        self.assertTrue((self.directory / record["backup"]).exists())
        self.assertEqual(self.client.rules, [])
        self.install()
        self.assertEqual(c.read_record(self.directory)["status"], "installed")

    def test_successful_write_with_interrupted_record_can_resume(self):
        self.install()
        record = c.read_record(self.directory)
        record["status"] = "prepared"
        c.write_record(self.directory, record)
        with self.assertRaisesRegex(ValueError, "未完成"):
            c.doctor(self.directory, self.client)
        self.install()
        self.assertEqual(self.client.writes, 1)
        c.doctor(self.directory, self.client)

    def test_post_write_mismatch_keeps_recovery_record(self):
        after = {"unrelated_export": "different", "rule_exports": []}
        with patch.object(self.client, "apply", return_value=after):
            with self.assertRaisesRegex(ValueError, "保存后"):
                self.install()
        self.assertEqual(c.read_record(self.directory)["status"], "prepared")

    def test_doctor_rejects_modified_installed_source(self):
        self.install()
        (self.directory / "wecom-image-history.js").write_text("changed")
        with self.assertRaisesRegex(ValueError, "源码副本"):
            c.doctor(self.directory, self.client)

    def test_uninstalled_doctor_does_not_need_copyq_running(self):
        with patch.object(self.client, "snapshot") as snapshot:
            c.doctor(self.directory, self.client)
        snapshot.assert_not_called()

    def test_client_preserves_javascript_arguments(self):
        with patch.object(c.subprocess, "run") as run:
            run.return_value.stdout = "{}"
            c.Client().call("var re = /\\d+/;", {"test": True})
        args, kwargs = run.call_args
        self.assertEqual(args[0], ["copyq", "eval", "--", "var re = /\\d+/;"])
        self.assertEqual(json.loads(kwargs["input"]), {"test": True})

    def test_manager_routes_without_wine_prefix_or_stop(self):
        args = argparse.Namespace(prefix=None, module="copyq", dry_run=True)
        with patch.object(manager, "copyq_manager", return_value=c), \
                patch.object(c, "manage") as manage, \
                patch.object(manager, "prefix_path") as prefix, \
                patch.object(manager, "stopped") as stopped:
            manager.build(args, {"id": "copyq"})
            manager.install(args, {"id": "copyq"})
            manager.remove(args)
        self.assertEqual(manage.call_count, 3)
        prefix.assert_not_called()
        stopped.assert_not_called()

    @unittest.skipUnless(shutil.which("node"), "需要 Node.js 运行规则回归")
    def test_history_policy_in_node(self):
        subprocess.run(["node", str(c.ROOT / "tests/copyq-history.js")],
                       check=True, capture_output=True, text=True)


@unittest.skipUnless(
    os.environ.get("WECOM_TEST_COPYQ") == "1" and shutil.which("copyq"),
    "设置 WECOM_TEST_COPYQ=1 运行隔离 CopyQ 原生接口验证",
)
class NativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="copyq-test-")
        cls.root = Path(cls.temporary.name)
        env = os.environ.copy()
        env.update(XDG_CONFIG_HOME=str(cls.root / "config"),
                   XDG_DATA_HOME=str(cls.root / "data"),
                   QT_QPA_PLATFORM="offscreen")
        cls.command = ["copyq", "--session", "wcf-" + uuid.uuid4().hex[:12]]
        cls.client = c.Client(cls.command, env)
        cls.log = (cls.root / "server.log").open("w+")
        cls.server = subprocess.Popen(cls.command, env=env,
                                      stdout=subprocess.DEVNULL,
                                      stderr=cls.log)
        try:
            for _ in range(50):
                if cls.server.poll() is not None:
                    cls.log.seek(0)
                    raise RuntimeError(cls.log.read())
                try:
                    cls.client.snapshot()
                    return
                except ValueError:
                    time.sleep(0.1)
            raise RuntimeError("隔离 CopyQ 服务未就绪")
        except Exception:
            cls.tearDownClass()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.server.terminate()
        try:
            cls.server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            cls.server.kill()
            cls.server.wait(timeout=5)
        cls.log.close()
        cls.temporary.cleanup()

    def setUp(self):
        self.directory = self.root / uuid.uuid4().hex
        self.directory.mkdir()
        self.client.call("setCommands([]); JSON.stringify(true);", {})
        output = contextlib.redirect_stdout(io.StringIO())
        output.__enter__()
        self.addCleanup(output.__exit__, None, None, None)

    def test_native_regex_and_later_changes_survive_install_remove(self):
        self.client.call(r"""
var c = [{name: "regex", cmd: "copyq: print(1)",
          re: /^hello\d+$/i, wndre: /Window.*/},
         {name: "empty", cmd: "copyq: print(2)"}];
setCommands(c); JSON.stringify(true);
""", {})
        original = self.client.snapshot()["unrelated_export"]
        self.assertIn("Match=", original)
        self.assertIn("Window=", original)
        c.install(self.directory, self.client)
        c.install(self.directory, self.client)
        self.assertEqual(self.client.snapshot()["unrelated_export"], original)
        c.doctor(self.directory, self.client)
        self.client.call(r"""
var c = commands(); c[1].name = "changed later";
setCommands(c); JSON.stringify(true);
""", {})
        changed = self.client.snapshot()["unrelated_export"]
        c.remove(self.directory, self.client)
        self.assertEqual(self.client.snapshot()["exported"], changed)
        self.assertNotIn("[object Object]", changed)

    def test_single_rule_export_is_unnumbered_and_round_trips(self):
        self.client.call(
            'setCommands([{name:"single", cmd:"copyq: print(1)"}]);'
            'JSON.stringify(true);', {},
        )
        original = self.client.snapshot()["exported"]
        exported = self.client.exported_rule(c.rule_definition())
        self.assertIn("[Command]", exported)
        self.assertNotIn("1\\Name=", exported)
        c.install(self.directory, self.client)
        self.assertEqual(self.client.snapshot()["rule_exports"], [exported])
        c.remove(self.directory, self.client)
        self.assertEqual(self.client.snapshot()["exported"], original)
        self.assertNotIn("[object Object]", original)

    def test_native_compare_and_set_rejects_concurrent_edit(self):
        before = self.client.snapshot()
        self.client.call('setCommands([{name:"concurrent"}]);'
                         'JSON.stringify(true);', {})
        changed = self.client.snapshot()["exported"]
        with self.assertRaisesRegex(ValueError, "commands changed"):
            self.client.apply(before, "install", c.rule_definition())
        self.assertEqual(self.client.snapshot()["exported"], changed)


if __name__ == "__main__":
    unittest.main()
