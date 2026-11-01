from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from daily_welfare_claim import extract_welfare_session
from gt_automation.zhaoxian import (
    extract_zhaoxian_config,
    load_zhaoxian_config,
    parse_zhaoxian_result,
    save_zhaoxian_config,
)


HAR_PATH = Path("data/har/WeChatAppEx Helper_10-31-2026-18-49-32.har")
RESULT_PAYLOAD = bytes.fromhex(
    "0a810133325f3133323030315f33302633325f3133393030315f33302633325f3133313030315f33302633325f3134333030315f33302633325f3133363030315f33302633325f3133313030315f33302633325f3134343030315f33302633325f3134333030315f33302633325f3133373030315f33302633325f3134373030315f3330"
    "100218890720002a0d365f31323030303030325f3130320131"
)


class ZhaoxianTests(unittest.TestCase):
    def test_parse_ten_draw_result(self) -> None:
        result = parse_zhaoxian_result(RESULT_PAYLOAD)
        self.assertEqual(len(result.results), 10)
        self.assertEqual(result.results[0], "32_132001_30")
        self.assertEqual(result.results[-1], "32_147001_30")
        self.assertEqual(result.item_change, "6_12000002_10")

    @unittest.skipUnless(HAR_PATH.exists(), "招贤 HAR 不存在")
    def test_extract_and_round_trip_config(self) -> None:
        session = extract_welfare_session(HAR_PATH)
        config = extract_zhaoxian_config(
            HAR_PATH,
            session=session,
            account={"uid": "test-user"},
        )
        self.assertIsNotNone(config)
        assert config is not None
        self.assertEqual(config.activity_id, 506)
        self.assertEqual(config.draw_payload.hex(), "180208021000")
        self.assertEqual(len(config.last_results), 10)

        with tempfile.TemporaryDirectory() as directory:
            save_zhaoxian_config(config, Path(directory))
            loaded = load_zhaoxian_config(Path(directory))
            self.assertEqual(loaded.activity_id, 506)
            self.assertEqual(loaded.draw_payload, config.draw_payload)
            self.assertEqual(len(loaded.last_results), 10)


if __name__ == "__main__":
    unittest.main()
