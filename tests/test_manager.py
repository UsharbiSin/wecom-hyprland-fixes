"""验证安装事务的冲突保护、产物完整性与版本边界。"""
import argparse
import json
import os
import subprocess
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import manager as m
import session


class ManagerTests(unittest.TestCase):
    def test_preload_separator_in_state_is_rejected(self):
        for directory in ("/tmp/a b", "/tmp/a:b", "/tmp/a\tb"):
            with self.subTest(directory=directory):
                with self.assertRaisesRegex(ValueError, "LD_PRELOAD"):
                    m.check_state_path({"preload": "lib.so"}, {
                        "module_dir": directory,
                    })

    def test_path_colon_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "PATH"):
            m.check_state_path({"path": "bin"}, {"module_dir": "/tmp/a:b"})

    def test_plain_chinese_state_path_is_allowed(self):
        m.check_state_path({"preload": "lib.so"}, {
            "module_dir": "/tmp/企业微信",
        })
        m.check_state_path({}, {"module_dir": "/tmp/a b"})

    def test_registry_dword_normalization(self):
        self.assertTrue(m.equivalent(
            {"type": "REG_DWORD", "value": "96"},
            {"type": "REG_DWORD", "value": "0x60"},
        ))
        self.assertFalse(m.equivalent(None, {"type": "REG_SZ", "value": ""}))

    def test_registry_split_key(self):
        self.assertEqual(
            m.normalize_entry({"key": ["HKCU\\", "Software"]})["key"],
            "HKCU\\Software",
        )

    def test_target_hash_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "original.dll"
            target.write_bytes(b"new version")
            with self.assertRaisesRegex(ValueError, "版本哈希"):
                m.check_targets({"bindings": [{
                    "target": str(target), "sha256": "0" * 64,
                }]}, {})

    def test_target_profile_requires_matching_patch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "original.dll"
            artifact = root / "patched.dll"
            pairs = {}
            for version in (b"old", b"new"):
                target.write_bytes(version)
                artifact.write_bytes(version + b" patch")
                pairs[m.digest(target)] = m.digest(artifact)
            module = {"bindings": [{
                "target": str(target), "source": artifact.name,
                "sha256_pairs": pairs,
            }]}
            for version in (b"old", b"new"):
                target.write_bytes(version)
                artifact.write_bytes(version + b" patch")
                m.check_targets(module, {}, root)
            for version in (b"old", b"unknown"):
                target.write_bytes(version)
                with self.subTest(version=version):
                    with self.assertRaisesRegex(ValueError, "版本不匹配"):
                        m.check_targets(module, {}, root)
            target.write_bytes(b"new")
            with self.assertRaisesRegex(ValueError, "版本不匹配"):
                m.check_targets(module, {})

    def test_artifact_tampering_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / "helper.exe"
            artifact.write_bytes(b"expected")
            m.write_json(root / "installed.json", {
                "ready": True, "files": {"helper.exe": m.digest(artifact)},
                "targets": {},
            })
            artifact.write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "产物校验失败"):
                m.verify_record(root / "installed.json")

    def test_incomplete_install_is_not_launched(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "installed.json"
            m.write_json(path, {"ready": False})
            with self.assertRaisesRegex(ValueError, "未完成"):
                m.verify_record(path)

    def test_rollback_conflict_does_not_write(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            folder = state / "modules/demo"
            folder.mkdir(parents=True)
            entry = {
                "key": "HKCU\\Example", "name": "test",
                "type": "REG_SZ", "value": "installed",
            }
            m.write_json(folder / "installed.json", {"registry": [{
                "entry": entry, "previous": None,
            }]})
            args = argparse.Namespace(prefix=directory, module="demo")
            with patch.object(m, "prefix_path", return_value=state), \
                    patch.object(m, "stopped"), \
                    patch.object(m, "state_path", return_value=state), \
                    patch.object(m, "reg_read", return_value={
                        "type": "REG_SZ", "value": "user edit",
                    }), patch.object(m, "reg_write") as write, \
                    patch.object(m, "finish_registry") as finish:
                with self.assertRaisesRegex(ValueError, "后续修改"):
                    m.remove(args)
                write.assert_not_called()
                finish.assert_called_once_with(state)
                self.assertTrue(folder.exists())


class ProcessAndLockTests(unittest.TestCase):
    def test_alias_prefix_and_dll_host_are_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prefix = root / "prefix"
            prefix.mkdir()
            alias = root / "alias"
            alias.symlink_to(prefix)
            process = root / "proc" / "123"
            process.mkdir(parents=True)
            (process / "environ").write_bytes(
                os.fsencode(f"WINEPREFIX={alias}/") + b"\0"
            )
            (process / "cmdline").write_bytes(
                b"--module=C:/WXWork/WeMeet/wemeet.dll\0"
            )
            with patch.object(m, "PROC", root / "proc"):
                with self.assertRaisesRegex(ValueError, "仍有 Wine"):
                    m.stopped(prefix)

    def test_missing_and_empty_prefix_use_default(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            for extra in ([], [b"WINEPREFIX="]):
                env = [os.fsencode(f"HOME={home}")] + extra
                self.assertEqual(m.process_prefix(env), home / ".wine")

    def test_second_lock_fails_without_waiting(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            with m.locked(state):
                with self.assertRaisesRegex(ValueError, "正在运行或修改"):
                    with m.locked(state):
                        self.fail("重复取得了同一前缀锁")
            with m.locked(state):
                pass

    def test_launch_keeps_lock_until_session_returns(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)

            def session(args, prefix):
                with self.assertRaisesRegex(ValueError, "正在运行或修改"):
                    with m.locked(state):
                        self.fail("会话结束前锁已释放")

            args = argparse.Namespace(prefix=directory)
            with patch.object(m, "prefix_path", return_value=state), \
                    patch.object(m, "state_path", return_value=state), \
                    patch.object(m, "stopped"), \
                    patch.object(m, "launch_locked", side_effect=session):
                m.launch(args)
            with m.locked(state):
                pass

    def test_registry_cleanup_waits_without_killing(self):
        with patch.object(m.subprocess, "run") as run:
            m.finish_registry(Path("/example"))
        self.assertEqual(run.call_args.args[0], ["wineserver", "-w"])
        self.assertEqual(run.call_args.kwargs["timeout"], 30)


class CopyTransactionTests(unittest.TestCase):
    def test_target_change_during_copy_keeps_verified_hash(self):
        for changed in (b"next-supported", b"unknown"):
            with self.subTest(changed=changed), \
                    tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                prefix = root / "prefix"
                prefix.mkdir()
                state = root / "state"
                source = root / "build/demo"
                source.mkdir(parents=True)
                target = root / "original.dll"
                artifact = source / "patched.dll"
                pairs = {}
                for content in (b"next-supported", b"original"):
                    target.write_bytes(content)
                    artifact.write_bytes(content + b" patched")
                    pairs[m.digest(target)] = m.digest(artifact)
                verified = m.digest(target)
                module = {
                    "id": "demo", "title": "测试", "bindings": [{
                        "target": str(target), "source": artifact.name,
                        "sha256_pairs": pairs,
                    }],
                }
                m.write_json(source / "build.json", {
                    "prefix": str(prefix), "app_version": "1",
                    "files": {artifact.name: m.digest(artifact)},
                    "module": module,
                })
                copytree = m.shutil.copytree

                def update_during_copy(src, dst):
                    copytree(src, dst)
                    target.write_bytes(changed)

                args = argparse.Namespace(prefix=str(prefix), app_version="1",
                                          dry_run=False)
                with patch.object(m, "ROOT", root), \
                        patch.object(m, "prefix_path", return_value=prefix), \
                        patch.object(m, "state_path", return_value=state), \
                        patch.object(m, "stopped"), \
                        patch.object(m.shutil, "copytree",
                                     side_effect=update_during_copy):
                    with self.assertRaisesRegex(ValueError, "目标版本已改变"):
                        m.install(args, module)
                path = state / "modules/demo/installed.json"
                record = m.read_json(path)
                self.assertEqual(record["targets"], {str(target): verified})
                self.assertFalse(record["ready"])
                with self.assertRaisesRegex(ValueError, "未完成"):
                    m.verify_record(path)
                # 即使外部把记录标成完成，原始验证哈希仍会拒绝新目标。
                record["ready"] = True
                m.write_json(path, record)
                with self.assertRaisesRegex(ValueError, "目标版本已改变"):
                    m.verify_record(path)

    def test_interrupted_copy_preserves_original(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, target = root / "source", root / "target"
            source.write_bytes(b"new")
            target.write_bytes(b"original")

            def interrupted(src, dst):
                Path(dst).write_bytes(b"partial")
                raise OSError("模拟复制中断")

            with patch.object(m.shutil, "copy2", side_effect=interrupted):
                with self.assertRaises(OSError):
                    m.atomic_copy(source, target)
            self.assertEqual(target.read_bytes(), b"original")
            self.assertEqual(sorted(p.name for p in root.iterdir()),
                             ["source", "target"])

    def test_symlink_rollback_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            real = root / "real.dll"
            real.write_bytes(b"installed")
            target = root / "target.dll"
            target.symlink_to(real)
            record = {"copies": [{
                "target": str(target), "previous": None,
                "sha256": m.digest(real), "previous_sha256": None,
            }]}
            with self.assertRaisesRegex(ValueError, "符号链接"):
                m.check_copies_for_rollback(record, root)
            self.assertEqual(real.read_bytes(), b"installed")

    def test_known_previous_copy_is_restored(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            folder = state / "modules/demo"
            folder.mkdir(parents=True)
            target = state / "shim.dll"
            target.write_bytes(b"installed")
            backup = folder / "backup.dll"
            backup.write_bytes(b"old-known-shim")
            m.write_json(folder / "installed.json", {"copies": [{
                "target": str(target), "previous": backup.name,
                "sha256": m.digest(target),
                "previous_sha256": m.digest(backup),
            }]})
            args = argparse.Namespace(prefix=directory, module="demo")
            with patch.object(m, "prefix_path", return_value=state), \
                    patch.object(m, "state_path", return_value=state), \
                    patch.object(m, "stopped"):
                m.remove(args)
            self.assertEqual(target.read_bytes(), b"old-known-shim")
            self.assertFalse(folder.exists())

    def test_registry_read_failure_keeps_recovery_record_and_waits(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            folder = state / "modules/demo"
            folder.mkdir(parents=True)
            m.write_json(folder / "installed.json", {"registry": [{
                "entry": {"key": "HKCU", "name": "test"},
                "previous": None,
            }]})
            args = argparse.Namespace(prefix=directory, module="demo")
            with patch.object(m, "prefix_path", return_value=state), \
                    patch.object(m, "state_path", return_value=state), \
                    patch.object(m, "stopped"), \
                    patch.object(m, "reg_read", side_effect=ValueError), \
                    patch.object(m, "reg_write") as write, \
                    patch.object(m, "finish_registry") as finish:
                with self.assertRaises(ValueError):
                    m.remove(args)
                write.assert_not_called()
                finish.assert_called_once_with(state)
                self.assertTrue(folder.exists())


    def test_install_read_failure_retains_recoverable_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prefix = root / "prefix"
            prefix.mkdir()
            state = root / "state"
            source = root / "build/demo"
            source.mkdir(parents=True)
            module = {
                "id": "demo", "title": "测试", "artifacts": [],
                "registry": [{
                    "key": ["HKCU", "\\Example"], "name": "test",
                    "type": "REG_DWORD", "value": "1",
                }],
            }
            m.write_json(source / "build.json", {
                "prefix": str(prefix), "app_version": "1",
                "files": {}, "module": module,
            })
            args = argparse.Namespace(
                prefix=str(prefix), app_version="1", dry_run=False,
            )
            with patch.object(m, "ROOT", root), \
                    patch.object(m, "prefix_path", return_value=prefix), \
                    patch.object(m, "state_path", return_value=state), \
                    patch.object(m, "stopped"), \
                    patch.object(m, "reg_read", side_effect=ValueError), \
                    patch.object(m, "reg_write") as write, \
                    patch.object(m, "finish_registry") as finish:
                with self.assertRaises(ValueError):
                    m.install(args, module)
                write.assert_not_called()
                finish.assert_called_once_with(prefix)
            record = m.read_json(state / "modules/demo/installed.json")
            self.assertFalse(record["ready"])
            self.assertEqual(record["registry"], [])


class SessionTests(unittest.TestCase):
    def test_partial_helper_startup_is_cleaned_up(self):
        helper = Mock()
        helper.poll.return_value = None
        specification = {"helpers": [["helper-a"], ["helper-b"]]}
        with patch.object(session.subprocess, "Popen", side_effect=[
                helper, OSError("模拟第二个辅助进程启动失败")]):
            with self.assertRaises(OSError):
                session.run(specification)
        helper.terminate.assert_called_once()
        helper.wait.assert_called_once_with(timeout=5)

    def test_helper_timeout_does_not_kill_prefix(self):
        helper = Mock()
        helper.poll.return_value = None
        helper.wait.side_effect = [
            subprocess.TimeoutExpired("helper", 5), 0,
        ]
        with patch.object(session.subprocess, "run") as run:
            session.stop_helpers([helper])
        helper.kill.assert_called_once()
        self.assertEqual(helper.wait.call_count, 2)
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
