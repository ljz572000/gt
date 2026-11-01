from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from daily_welfare_claim import extract_welfare_session
from gt_automation.qiluo_jingyi import (
    extract_qiluo_jingyi_config,
    load_qiluo_jingyi_config,
    parse_qiluo_jingyi_result,
    save_qiluo_jingyi_config,
)


HAR_PATH = Path("data/har/WeChatAppEx Helper_10-31-2026-19-28-53.har")
RESULT_PAYLOAD = bytes.fromhex(
    "08fdb76110fe86b30818012219"
    "365f31323030303030335f313026365f313037313033385f31"
)


class QiluoJingyiTests(unittest.TestCase):
    def test_parse_purchase_result(self) -> None:
        result = parse_qiluo_jingyi_result(RESULT_PAYLOAD)
        self.assertEqual(result.activity_id, 1596413)
        self.assertEqual(result.product_id, 17613694)
        self.assertEqual(result.status, 1)
        self.assertEqual(
            result.rewards,
            ["6_12000003_10", "6_1071038_1"],
        )

    @unittest.skipUnless(HAR_PATH.exists(), "绮罗锦衣 HAR 不存在")
    def test_extract_and_round_trip_config(self) -> None:
        session = extract_welfare_session(HAR_PATH)
        config = extract_qiluo_jingyi_config(
            HAR_PATH,
            session=session,
            account={"uid": "test-user"},
        )
        self.assertIsNotNone(config)
        assert config is not None
        self.assertEqual(config.activity_id, 1596413)
        self.assertEqual(config.product_id, 17613694)
        self.assertEqual(
            config.purchase_payload.hex(),
            "08fdb76110fe86b308",
        )
        self.assertEqual(len(config.last_rewards), 2)

        with tempfile.TemporaryDirectory() as directory:
            save_qiluo_jingyi_config(config, Path(directory))
            loaded = load_qiluo_jingyi_config(Path(directory))
            self.assertEqual(loaded.activity_id, 1596413)
            self.assertEqual(loaded.product_id, 17613694)
            self.assertEqual(
                loaded.purchase_payload,
                config.purchase_payload,
            )
            self.assertEqual(loaded.last_rewards, config.last_rewards)


if __name__ == "__main__":
    unittest.main()
