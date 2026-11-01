from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from gt_automation.session_store import (
    SessionFileError,
    SessionMetadata,
    TASK_DAILY,
    TASK_STAR,
    account_file,
    clear_configuration,
    import_detected_tasks,
    load_gt_session,
    load_gt_metadata,
    merge_star_session,
    save_import,
    task_file,
)


def make_session() -> SimpleNamespace:
    request = SimpleNamespace(
        url="https://example.invalid/login",
        headers={"Content-Type": "application/json"},
        body=b"{}",
    )
    state = SimpleNamespace(
        activity_id=1593223,
        astrology_result=0,
        star_level=2,
        unknown_4=None,
        protection_progress=0,
        unknown_6=0,
    )
    return SimpleNamespace(
        har_path=Path("source.har"),
        websocket_url="wss://example.invalid/ws",
        auth_frame=b"auth-frame",
        context_id=102011554,
        activity_id=1593223,
        current_state=state,
        init_frames=[(0.0, b"frame-1"), (0.5, b"frame-2")],
        origin="https://example.invalid",
        user_agent="test-agent",
        login_info_request=request,
        login_do_request=request,
    )


class SessionStoreTests(unittest.TestCase):
    def test_round_trip_preserves_capabilities(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "gt.json"
            session = make_session()
            metadata = SessionMetadata(
                source_har="source.har",
                daily_ready=True,
                star_ready=True,
                account={"role_name": "测试角色"},
            )
            base_dir = output.parent
            save_import(session, base_dir, metadata, replace=True)
            loaded, loaded_metadata = load_gt_session(base_dir, task=TASK_DAILY)

            self.assertEqual(loaded.context_id, 102011554)
            self.assertEqual(loaded.activity_id, 1593223)
            self.assertEqual(loaded.current_state.star_level, 2)
            self.assertEqual(len(loaded.init_frames), 2)
            self.assertTrue(account_file(base_dir).exists())
            self.assertTrue(task_file(TASK_DAILY, base_dir).exists())
            self.assertTrue(task_file(TASK_STAR, base_dir).exists())
            self.assertTrue(loaded_metadata.star_ready)
            self.assertEqual(loaded_metadata.account["role_name"], "测试角色")
            if os.name != "nt":
                self.assertEqual(
                    task_file(TASK_DAILY, base_dir).stat().st_mode & 0o777,
                    0o600,
                )

    def test_missing_file_has_clear_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing.json"
            with self.assertRaisesRegex(
                SessionFileError,
                "没有每日福利任务配置文件",
            ):
                load_gt_session(missing.parent, task=TASK_DAILY)

    def test_star_merge_preserves_daily_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "gt.json"
            daily = make_session()
            metadata = SessionMetadata(
                source_har="daily.har",
                daily_ready=True,
                star_ready=False,
                account={"uid": "same-user"},
                last_reward={
                    "reward_id": "daily-reward",
                    "status": 5,
                    "server_time": 123,
                },
            )
            base_dir = output.parent
            save_import(daily, base_dir, metadata, replace=True)
            daily_bytes = task_file(TASK_DAILY, base_dir).read_bytes()

            star = make_session()
            star.context_id = 105011554
            star_metadata = SessionMetadata(
                source_har="star.har",
                daily_ready=True,
                star_ready=True,
                account={"uid": "same-user"},
            )
            merge_star_session(
                star,
                base_dir,
                star_metadata,
            )

            loaded_metadata = load_gt_metadata(base_dir)
            loaded_daily, _ = load_gt_session(base_dir, task=TASK_DAILY)
            loaded_star, _ = load_gt_session(base_dir, task=TASK_STAR)
            self.assertTrue(loaded_metadata.daily_ready)
            self.assertTrue(loaded_metadata.star_ready)
            self.assertEqual(
                loaded_metadata.last_reward["reward_id"],
                "daily-reward",
            )
            self.assertEqual(loaded_daily.context_id, 102011554)
            self.assertEqual(loaded_star.context_id, 105011554)
            self.assertEqual(
                task_file(TASK_DAILY, base_dir).read_bytes(),
                daily_bytes,
            )

    def test_import_detected_tasks_does_not_block_or_delete(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base_dir = Path(directory)
            daily = make_session()
            daily_meta = SessionMetadata(
                source_har="daily.har",
                daily_ready=True,
                star_ready=False,
                account={"uid": "same-user"},
            )
            save_import(daily, base_dir, daily_meta, replace=True)
            daily_bytes = task_file(TASK_DAILY, base_dir).read_bytes()

            star = make_session()
            star.context_id = 105011554
            star_meta = SessionMetadata(
                source_har="star.har",
                daily_ready=True,
                star_ready=True,
                account={"uid": "same-user"},
            )
            metadata, imported = import_detected_tasks(
                star,
                base_dir,
                star_meta,
            )
            self.assertEqual(imported, [TASK_STAR])
            self.assertTrue(metadata.daily_ready)
            self.assertTrue(metadata.star_ready)
            self.assertEqual(
                task_file(TASK_DAILY, base_dir).read_bytes(),
                daily_bytes,
            )

    def test_clear_configuration_removes_only_config_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base_dir = Path(directory)
            metadata = SessionMetadata(
                daily_ready=True,
                star_ready=True,
                account={"uid": "same-user"},
            )
            save_import(make_session(), base_dir, metadata, replace=True)
            log_file = base_dir / "logs" / "test.log"
            log_file.parent.mkdir()
            log_file.write_text("keep", encoding="utf-8")
            zhaoxian = base_dir / "zhaoxian.json"
            zhaoxian.write_text("{}", encoding="utf-8")
            qiluo = base_dir / "qiluo_jingyi.json"
            qiluo.write_text("{}", encoding="utf-8")

            removed = clear_configuration(base_dir)

            self.assertEqual(len(removed), 5)
            self.assertFalse(account_file(base_dir).exists())
            self.assertFalse(task_file(TASK_DAILY, base_dir).exists())
            self.assertFalse(task_file(TASK_STAR, base_dir).exists())
            self.assertFalse(zhaoxian.exists())
            self.assertFalse(qiluo.exists())
            self.assertTrue(log_file.exists())

    def test_zhaoxian_import_preserves_existing_daily(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base_dir = Path(directory)
            daily_meta = SessionMetadata(
                daily_ready=True,
                star_ready=False,
                account={"uid": "same-user"},
            )
            save_import(make_session(), base_dir, daily_meta, replace=True)
            daily_bytes = task_file(TASK_DAILY, base_dir).read_bytes()

            zhaoxian_meta = SessionMetadata(
                daily_ready=True,
                star_ready=False,
                zhaoxian_ready=True,
                account={"uid": "same-user"},
            )
            _metadata, imported = import_detected_tasks(
                make_session(),
                base_dir,
                zhaoxian_meta,
            )
            self.assertEqual(imported, [])
            self.assertEqual(
                task_file(TASK_DAILY, base_dir).read_bytes(),
                daily_bytes,
            )


if __name__ == "__main__":
    unittest.main()
