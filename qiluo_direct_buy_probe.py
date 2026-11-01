#!/usr/bin/env python3
"""绮罗锦衣特惠直购的一次性受控测试工具。

默认只解析 HAR 并生成请求，不连接服务器。实际连接需要显式
``--live``、授权确认和 WebSocket 目标片段；真正发送购买命令还必须
额外提供 ``--confirm-purchase`` 和 ``--confirm-one-shot``。

安全边界：
1. 默认不联网。
2. 每次进程最多发送一次购买命令 5515，不重试。
3. 不发送广告状态帧，不伪造支付或广告完成事件。
4. 可使用 ``--preflight-only`` 只做活动状态查询，不发送购买命令。

示例：
    # 离线检查 HAR 中的购买请求和响应
    python3 qiluo_direct_buy_probe.py \
        --har "data/har/WeChatAppEx Helper_10-31-2026-19-28-53.har"

    # 只验证登录、WebSocket 和活动状态，不购买
    python3 qiluo_direct_buy_probe.py --har sample.har --live \
        --preflight-only --authorized-account \
        --expected-ws-fragment "<server-id>"

    # 一次性真实购买测试
    python3 qiluo_direct_buy_probe.py --har sample.har --live \
        --authorized-account --confirm-purchase --confirm-one-shot \
        --expected-ws-fragment "<server-id>"
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from daily_welfare_claim import extract_welfare_session
from siyi_star_auto import (
    HarFormatError,
    HarSession,
    PacketClient,
    ProtocolError,
    build_frame,
    create_ssl_context,
    field_map,
    find_default_har,
    first_value,
    parse_frame,
    pb_uint,
    refresh_login_session,
)


CMD_QUERY_ACTIVITY = 5634
CMD_QUERY_DIRECT_BUY = 1173
CMD_DIRECT_BUY = 5515
CMD_DIRECT_BUY_RESULT = 3372

DEFAULT_EXPECTED_REWARD = "6_12000003_10&6_1071038_1"
CHINA_TZ = timezone(timedelta(hours=8))


@dataclass(frozen=True)
class DirectBuySample:
    timestamp: float
    status_activity_id: Optional[int]
    activity_id: int
    offer_id: int
    status: Optional[int]
    reward_id: str
    request_payload: bytes
    response_payload: bytes


@dataclass(frozen=True)
class DirectBuyResult:
    activity_id: Optional[int]
    offer_id: Optional[int]
    status: Optional[int]
    reward_id: str


def _decode_text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    return str(value)


def decode_direct_buy_result(payload: bytes) -> DirectBuyResult:
    fields = field_map(payload)
    activity_id = first_value(fields, 1)
    offer_id = first_value(fields, 2)
    status = first_value(fields, 3)
    return DirectBuyResult(
        activity_id=int(activity_id) if isinstance(activity_id, int) else None,
        offer_id=int(offer_id) if isinstance(offer_id, int) else None,
        status=int(status) if isinstance(status, int) else None,
        reward_id=_decode_text(first_value(fields, 4, b"")),
    )


def decode_direct_buy_status(
    payload: bytes,
) -> Tuple[Optional[int], Optional[int]]:
    """解析 1173 响应中的直购档位和状态。"""
    fields = field_map(payload)
    nested = first_value(fields, 2)
    if not isinstance(nested, bytes):
        return None, None
    nested_fields = field_map(nested)
    offer_id = first_value(nested_fields, 1)
    status = first_value(nested_fields, 2)
    return (
        int(offer_id) if isinstance(offer_id, int) else None,
        int(status) if isinstance(status, int) else None,
    )


def _iter_decoded_messages(
    messages: Iterable[Dict[str, Any]],
) -> Iterable[Tuple[int, Dict[str, Any], Any]]:
    for index, message in enumerate(messages):
        try:
            frame = base64.b64decode(message.get("data", ""))
            packet = parse_frame(frame)
        except Exception:
            continue
        yield index, message, packet


def extract_direct_buy_sample(har_path: Path) -> DirectBuySample:
    """从 HAR 中提取最近的 5515 请求及对应的 3372 响应。"""
    with har_path.open("r", encoding="utf-8") as handle:
        har = json.load(handle)

    candidates: List[DirectBuySample] = []
    for entry in har.get("log", {}).get("entries", []):
        messages = entry.get("_webSocketMessages") or []
        decoded = list(_iter_decoded_messages(messages))
        for position, (index, message, packet) in enumerate(decoded):
            if message.get("type") != "send":
                continue
            if packet.command != CMD_DIRECT_BUY:
                continue

            request_fields = field_map(packet.payload)
            activity_id = first_value(request_fields, 1)
            offer_id = first_value(request_fields, 2)
            if not isinstance(activity_id, int) or not isinstance(offer_id, int):
                continue

            status_activity_id: Optional[int] = None
            for previous_index, previous, previous_packet in reversed(
                decoded[:position]
            ):
                del previous_index, previous
                if previous_packet.command != CMD_QUERY_ACTIVITY:
                    continue
                status_fields = field_map(previous_packet.payload)
                possible_id = first_value(status_fields, 1)
                if isinstance(possible_id, int):
                    status_activity_id = possible_id
                break

            sent_at = float(message.get("time", 0.0))
            matched_response: Optional[DirectBuyResult] = None
            response_payload = b""
            for next_index, next_message, next_packet in decoded[position + 1 :]:
                del next_index
                response_time = float(next_message.get("time", sent_at))
                if response_time - sent_at > 2.0:
                    break
                if next_message.get("type") != "receive":
                    continue
                if next_packet.command != CMD_DIRECT_BUY_RESULT:
                    continue
                result = decode_direct_buy_result(next_packet.payload)
                if (
                    result.activity_id == activity_id
                    and result.offer_id == offer_id
                ):
                    matched_response = result
                    response_payload = next_packet.payload
                    break

            if matched_response is None:
                continue
            candidates.append(
                DirectBuySample(
                    timestamp=sent_at,
                    status_activity_id=status_activity_id,
                    activity_id=activity_id,
                    offer_id=offer_id,
                    status=matched_response.status,
                    reward_id=matched_response.reward_id,
                    request_payload=packet.payload,
                    response_payload=response_payload,
                )
            )

    if not candidates:
        raise HarFormatError("HAR 中没有找到命令 5515 的购买响应 3372")
    return max(candidates, key=lambda item: item.timestamp)


def format_sample_time(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, CHINA_TZ).isoformat(
        timespec="milliseconds"
    )


def print_dry_run(
    har_path: Path,
    session: HarSession,
    sample: DirectBuySample,
    activity_id: int,
    offer_id: int,
) -> None:
    payload = pb_uint(1, activity_id) + pb_uint(2, offer_id)
    frame = build_frame(CMD_DIRECT_BUY, payload, context_id=session.context_id)

    print("[HAR] %s" % har_path)
    print("[样本时间] %s" % format_sample_time(sample.timestamp))
    print("[WebSocket] %s" % session.websocket_url)
    print("[上下文] field8=%d" % session.context_id)
    print("[活动] status=%s buy=%d" % (sample.status_activity_id, activity_id))
    print("[直购档位] %d" % offer_id)
    print("[请求] cmd=%d payload=%s" % (CMD_DIRECT_BUY, payload.hex()))
    print("[帧] %s" % frame.hex())
    print("[HAR 样本] status=%s" % sample.status)
    print("[HAR 样本] rewards=%s" % sample.reward_id)
    print("[离线] 未连接服务器，也未发送购买命令。")


def validate_live_scope(
    args: argparse.Namespace, session: HarSession
) -> None:
    required = (("--authorized-account", args.authorized_account),)
    if not args.preflight_only:
        required += (
            ("--confirm-purchase", args.confirm_purchase),
            ("--confirm-one-shot", args.confirm_one_shot),
        )
    missing = [name for name, enabled in required if not enabled]
    if missing:
        raise ProtocolError("实际连接缺少确认参数：%s" % ", ".join(missing))

    fragment = args.expected_ws_fragment
    if not fragment:
        raise ProtocolError(
            "实际连接必须提供 --expected-ws-fragment，"
            "用于防止误连其他服务器"
        )
    if fragment not in session.websocket_url:
        raise ProtocolError(
            "WebSocket 地址不匹配：expected=%s actual=%s"
            % (fragment, session.websocket_url)
        )


async def query_status(
    client: PacketClient,
    command: int,
    payload: bytes,
    label: str,
) -> bytes:
    response = await client.request(command, payload)
    if not response:
        raise ProtocolError("%s 返回空响应" % label)
    print("[%s] 响应=%s" % (label, response.hex()))
    return response


async def run_live(
    args: argparse.Namespace,
    session: HarSession,
    activity_id: int,
    offer_id: int,
) -> int:
    validate_live_scope(args, session)

    try:
        import websockets
    except ImportError:
        print(
            "缺少依赖 websockets，请执行：pip install -r requirements.txt",
            file=sys.stderr,
        )
        return 2

    ssl_context = create_ssl_context(args.ca_file)
    if not args.reuse_session:
        print("[登录] 刷新 HAR 中的微信和游戏会话")
        session = refresh_login_session(session, args.timeout, ssl_context)
        validate_live_scope(args, session)

    extra_headers = {
        "User-Agent": session.user_agent,
        "Pragma": "no-cache",
        "Cache-Control": "no-cache",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }

    print("[连接] %s" % session.websocket_url)
    async with websockets.connect(
        session.websocket_url,
        ssl=ssl_context,
        origin=session.origin,
        extra_headers=extra_headers,
        max_size=None,
        ping_interval=20,
        ping_timeout=20,
    ) as websocket:
        client = PacketClient(
            websocket=websocket,
            context_id=session.context_id,
            activity_id=activity_id,
            timeout=args.timeout,
        )
        client.start()
        try:
            await client.authenticate(session.auth_frame)
            if not args.no_bootstrap:
                await client.send_bootstrap(session.init_frames)

            if args.status_activity_id > 0:
                await query_status(
                    client,
                    CMD_QUERY_ACTIVITY,
                    pb_uint(1, args.status_activity_id),
                    "活动状态",
                )

            status_response = await query_status(
                client,
                CMD_QUERY_DIRECT_BUY,
                pb_uint(1, activity_id),
                "直购状态",
            )
            status_offer_id, status_value = decode_direct_buy_status(
                status_response
            )

            if args.preflight_only:
                print("[预检完成] 未发送购买命令 5515")
                if status_offer_id == offer_id and status_value is not None:
                    print(
                        "[预检状态] offer=%d status=%d"
                        % (status_offer_id, status_value)
                    )
                return 0

            if status_offer_id == offer_id and status_value == 1:
                print(
                    "[停止] 服务端已将该直购档位标记为"
                    "已购买/已领取，"
                    "未发送重复购买命令"
                )
                return 0

            payload = pb_uint(1, activity_id) + pb_uint(2, offer_id)
            print(
                "[购买] 即将发送一次 cmd=%d activity=%d offer=%d"
                % (CMD_DIRECT_BUY, activity_id, offer_id)
            )
            print("[购买] payload=%s" % payload.hex())
            started = time.monotonic()
            try:
                response = await client.request(CMD_DIRECT_BUY, payload)
            except TimeoutError:
                print(
                    "[警告] 未在超时时间内收到 3372，"
                    "正在执行只读状态复查",
                    file=sys.stderr,
                )
                await asyncio.sleep(1.0)
                post_response = await query_status(
                    client,
                    CMD_QUERY_DIRECT_BUY,
                    pb_uint(1, activity_id),
                    "直购状态复查",
                )
                post_offer_id, post_status = decode_direct_buy_status(
                    post_response
                )
                if (
                    post_offer_id == offer_id
                    and post_status == 1
                    and status_value != 1
                ):
                    print(
                        "[完成] 未收到 3372，但直购状态已变为 1，"
                        "购买请求已被服务端处理"
                    )
                    return 0
                print(
                    "[失败] 未收到 3372，状态也未确认变为已购买",
                    file=sys.stderr,
                )
                return 1
            elapsed = time.monotonic() - started
            result = decode_direct_buy_result(response)

            print("[响应] payload=%s" % response.hex())
            print("[响应] status=%s" % result.status)
            print("[响应] rewards=%s" % (result.reward_id or "<empty>"))
            print("[耗时] %.3fs" % elapsed)

            if result.activity_id != activity_id or result.offer_id != offer_id:
                print(
                    "[失败] 响应中的活动或档位与请求不一致",
                    file=sys.stderr,
                )
                return 1
            if result.status != 1:
                print("[失败] 服务端未返回成功状态 1", file=sys.stderr)
                return 1

            expected = args.expect_reward or DEFAULT_EXPECTED_REWARD
            if expected and result.reward_id != expected:
                print(
                    "[警告] 奖励与样本不一致：expected=%s actual=%s"
                    % (expected, result.reward_id),
                    file=sys.stderr,
                )
                return 1

            await asyncio.sleep(0.3)
            print("[完成] 服务端返回购买成功")
            return 0
        finally:
            await client.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="解析并受控测试绮罗锦衣特惠直购 WebSocket 命令"
    )
    parser.add_argument(
        "--har",
        type=Path,
        help="包含命令 5515/3372 样本的 HAR；省略时取最新 HAR",
    )
    parser.add_argument(
        "--activity-id",
        type=int,
        help="购买活动 ID；省略时使用 HAR 样本",
    )
    parser.add_argument(
        "--offer-id",
        type=int,
        help="特惠直购商品/档位 ID；省略时使用 HAR 样本",
    )
    parser.add_argument(
        "--status-activity-id",
        type=int,
        help="只读活动状态 ID；省略时使用 HAR 样本",
    )
    parser.add_argument(
        "--expect-reward",
        help="预期奖励字符串；省略时使用默认样本",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="实际连接 WebSocket；默认只做离线检查",
    )
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="实际连接但只查询活动状态，不发送购买命令",
    )
    parser.add_argument(
        "--authorized-account",
        action="store_true",
        help="确认账号已授权用于该测试",
    )
    parser.add_argument(
        "--confirm-purchase",
        action="store_true",
        help="确认接受本次真实游戏内购买及其消耗",
    )
    parser.add_argument(
        "--confirm-one-shot",
        action="store_true",
        help="确认本进程最多发送一次购买命令且不自动重试",
    )
    parser.add_argument(
        "--expected-ws-fragment",
        help="必须出现在 WebSocket 地址中的目标片段",
    )
    parser.add_argument(
        "--reuse-session",
        action="store_true",
        help="复用 HAR 登录态；默认重新执行登录接口",
    )
    parser.add_argument(
        "--no-bootstrap",
        action="store_true",
        help="登录后不发送 HAR 中的初始化帧",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="单次网络响应超时，默认 10 秒",
    )
    parser.add_argument(
        "--ca-file",
        type=Path,
        help="额外信任的 PEM CA 证书",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout 必须大于 0")
    if args.preflight_only and not args.live:
        parser.error("--preflight-only 必须与 --live 一起使用")
    if args.ca_file is not None and not args.ca_file.exists():
        parser.error("--ca-file 不存在：%s" % args.ca_file)

    har_path = args.har or find_default_har()
    if not har_path.exists():
        print("HAR 不存在：%s" % har_path, file=sys.stderr)
        return 2

    try:
        sample = extract_direct_buy_sample(har_path)
        session = extract_welfare_session(har_path)
    except (
        OSError,
        ValueError,
        json.JSONDecodeError,
        HarFormatError,
        ProtocolError,
    ) as exc:
        print("HAR 解析失败：%s" % exc, file=sys.stderr)
        return 2

    activity_id = args.activity_id or sample.activity_id
    offer_id = args.offer_id or sample.offer_id
    args.status_activity_id = (
        args.status_activity_id
        if args.status_activity_id is not None
        else sample.status_activity_id or 0
    )

    print_dry_run(har_path, session, sample, activity_id, offer_id)
    if not args.live:
        return 0

    try:
        return asyncio.run(run_live(args, session, activity_id, offer_id))
    except KeyboardInterrupt:
        print("\n[停止] 用户中断", file=sys.stderr)
        return 130
    except Exception as exc:
        print("[失败] %s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
