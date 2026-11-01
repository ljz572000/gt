import base64
import json
import tempfile
import unittest
from pathlib import Path

from qiluo_direct_buy_probe import (
    DirectBuyResult,
    decode_direct_buy_result,
    decode_direct_buy_status,
    extract_direct_buy_sample,
)
from siyi_star_auto import build_frame, pb_bytes, pb_string, pb_uint


class DirectBuyProbeTests(unittest.TestCase):
    def test_decode_direct_buy_status(self) -> None:
        nested = pb_uint(1, 17613694) + pb_uint(2, 1)
        payload = pb_uint(1, 1596413) + pb_bytes(2, nested)

        self.assertEqual(
            decode_direct_buy_status(payload),
            (17613694, 1),
        )

    def test_decode_success_response(self) -> None:
        payload = (
            pb_uint(1, 1596413)
            + pb_uint(2, 17613694)
            + pb_uint(3, 1)
            + pb_bytes(
                4,
                b"6_12000003_10&6_1071038_1",
            )
        )

        result = decode_direct_buy_result(payload)

        self.assertEqual(
            result,
            DirectBuyResult(
                activity_id=1596413,
                offer_id=17613694,
                status=1,
                reward_id="6_12000003_10&6_1071038_1",
            ),
        )

    def test_extract_latest_purchase_sample(self) -> None:
        context_id = 12345
        query_payload = pb_uint(1, 1596411)
        buy_payload = pb_uint(1, 1596413) + pb_uint(2, 17613694)
        result_payload = (
            pb_uint(1, 1596413)
            + pb_uint(2, 17613694)
            + pb_uint(3, 1)
            + pb_bytes(4, b"6_12000003_10&6_1071038_1")
        )

        def message(message_type: str, frame: bytes, timestamp: float) -> dict:
            return {
                "type": message_type,
                "time": timestamp,
                "data": base64.b64encode(frame).decode("ascii"),
            }

        messages = [
            message(
                "send",
                build_frame(5634, query_payload, context_id),
                100.0,
            ),
            message(
                "receive",
                build_frame(5634, query_payload),
                100.1,
            ),
            message(
                "send",
                build_frame(1173, pb_uint(1, 1596413), context_id),
                100.2,
            ),
            message(
                "receive",
                build_frame(1173, pb_uint(1, 1596413)),
                100.3,
            ),
            message("send", build_frame(5515, buy_payload, context_id), 100.4),
            message(
                "receive",
                build_frame(
                    8603,
                    pb_string(1, "6_12000003_10&6_1071038_1")
                    + pb_string(2, "3098"),
                ),
                100.42,
            ),
            message("receive", build_frame(3372, result_payload), 100.45),
            message(
                "send",
                build_frame(5515, buy_payload, context_id),
                200.0,
            ),
            message(
                "receive",
                build_frame(
                    3372,
                    pb_uint(1, 1596413)
                    + pb_uint(2, 17613694)
                    + pb_uint(3, 0)
                    + pb_bytes(4, b""),
                ),
                200.05,
            ),
        ]
        har = {
            "log": {
                "entries": [
                    {
                        "_webSocketMessages": messages,
                    }
                ]
            }
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "sample.har"
            path.write_text(json.dumps(har), encoding="utf-8")
            sample = extract_direct_buy_sample(path)

        self.assertEqual(sample.timestamp, 200.0)
        self.assertEqual(sample.status, 0)
        self.assertEqual(sample.activity_id, 1596413)
        self.assertEqual(sample.offer_id, 17613694)
        self.assertEqual(sample.reward_id, "")


if __name__ == "__main__":
    unittest.main()
