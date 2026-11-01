#!/usr/bin/env python3
"""激励广告校验的一次性受控探测工具。

用途：
    在明确授权的测试服和专用测试账号上，于“每日福利奖励尚未领取”
    且“本周期没有产生广告 Load/Show/Finish 事件”的条件下，只发送
    一次 WebSocket 命令 5960，观察服务端是否返回奖励。

安全边界：
1. 默认只做离线检查，不连接服务器。
2. 不发送 5377 状态帧。
3. 不发送任何广告 Load/Show/Finish 事件。
4. 每次进程最多发送一次 5960，不自动重试。
5. 实际执行必须显式确认使用测试服、未领取状态、无广告事件，
   并允许奖励真实发放。

示例：
    python3 ad_validation_probe.py --har test.har --dry-run

    python3 ad_validation_probe.py --har test.har --live \
        --authorized-test \
        --confirm-fresh-claim-state \
        --confirm-no-ad-events \
        --confirm-reward-may-be-granted \
        --expected-ws-fragment "<test-server-id>"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from daily_welfare_claim import (
    CMD_WELFARE_CLAIM,
    decode_claim_result,
    find_last_har_reward,
)
from siyi_star_auto import (
    HarFormatError,
    HarSession,
    PacketClient,
    ProtocolError,
    build_frame,
    create_ssl_context,
    extract_http_request,
    extract_session,
    find_default_har,
    refresh_login_session,
)


def print_dry_run(har_path: Path, session: HarSession) -> None:
    frame = build_frame(
        CMD_WELFARE_CLAIM,
        b"",
        context_id=session.context_id,
    )
    print("[HAR] %s" % har_path)
    print("[WebSocket] %s" % session.websocket_url)
    print("[上下文] field8=%d" % session.context_id)
    print("[请求] cmd=%d payload=empty" % CMD_WELFARE_CLAIM)
    print("[帧] %s" % frame.hex())
    print("[广告事件] 本次探测不会发送")
    print("[状态命令] 本次探测不会发送 5377")

    previous = find_last_har_reward(har_path)
    if previous is None:
        print("[HAR 历史] 未找到命令 5960 的奖励响应")
    else:
        print(
            "[HAR 历史] 最近奖励=%s status=%s"
            % (previous.reward_id, previous.status)
        )


def validate_live_scope(args: argparse.Namespace, session: HarSession) -> None:
    required_flags = (
        ("--authorized-test", args.authorized_test),
        ("--confirm-fresh-claim-state", args.confirm_fresh_claim_state),
        ("--confirm-no-ad-events", args.confirm_no_ad_events),
        (
            "--confirm-reward-may-be-granted",
            args.confirm_reward_may_be_granted,
        ),
    )
    missing = [name for name, enabled in required_flags if not enabled]
    if missing:
        raise ProtocolError("实际探测缺少确认参数：%s" % ", ".join(missing))

    fragment = args.expected_ws_fragment
    if not fragment:
        raise ProtocolError(
            "实际探测必须提供 --expected-ws-fragment，"
            "用于防止误连正式服"
        )
    if fragment not in session.websocket_url:
        raise ProtocolError(
            "WebSocket 地址不匹配预期测试服：expected=%s actual=%s"
            % (fragment, session.websocket_url)
        )


async def run_probe(
    args: argparse.Namespace,
    session: HarSession,
) -> int:
    # 先校验 HAR 中的目标，避免在误连正式服的情况下刷新登录。
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
        session = refresh_login_session(
            session,
            args.timeout,
            ssl_context,
        )
        validate_live_scope(args, session)

    started_at = datetime.now(timezone.utc)
    started_monotonic = time.monotonic()
    print("[开始] %s" % started_at.isoformat())
    print("[目标] %s" % session.websocket_url)
    print("[约束] 仅发送一次 5960；不发送 5377 或广告事件")

    extra_headers = {
        "User-Agent": session.user_agent,
        "Pragma": "no-cache",
        "Cache-Control": "no-cache",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }

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
            activity_id=session.activity_id,
            timeout=args.timeout,
        )
        client.start()
        try:
            await client.authenticate(session.auth_frame)
            if not args.no_bootstrap:
                await client.send_bootstrap(session.init_frames)

            sent_at = datetime.now(timezone.utc)
            print("[发送] cmd=5960 payload=empty at %s" % sent_at.isoformat())
            response = await client.request(CMD_WELFARE_CLAIM, b"")
            received_at = datetime.now(timezone.utc)
            elapsed = time.monotonic() - started_monotonic
            result = decode_claim_result(response)

            print("[响应] %s" % response.hex())
            print("[响应] payload_len=%d" % len(response))
            print("[响应] status=%s server_time=%s" % (result.status, result.server_time))
            print("[响应] reward_id=%s" % (result.reward_id or "<empty>"))
            print("[耗时] %.3fs" % elapsed)
            print("[完成] %s" % received_at.isoformat())

            if result.reward_id:
                print(
                    "[结论] 服务端返回了奖励。在确认本周期没有广告事件、"
                    "且账号确实处于未领取状态的情况下，说明 5960 没有在"
                    "该测试路径上强制阻断无广告领奖。"
                )
            else:
                print(
                    "[结论] 服务端未返回奖励。该结果不能单独证明存在广告校验；"
                    "也可能是奖励已领取、冷却中、会话失效或账号条件不满足。"
                )
            return 0
        finally:
            await client.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="在授权测试环境中一次性探测命令 5960 是否直接返回奖励"
    )
    parser.add_argument(
        "--har",
        type=Path,
        help="测试账号的 HAR；省略时取当前目录最新 .har",
    )
    parser.add_argument(
        "--login-har",
        type=Path,
        help="用于刷新登录的其他 HAR",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只做离线检查（默认行为）",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="实际连接测试服并发送一次 5960",
    )
    parser.add_argument(
        "--authorized-test",
        action="store_true",
        help="确认已获得对目标测试环境的授权",
    )
    parser.add_argument(
        "--confirm-fresh-claim-state",
        action="store_true",
        help="确认测试账号本次奖励尚未领取",
    )
    parser.add_argument(
        "--confirm-no-ad-events",
        action="store_true",
        help="确认本次周期没有产生广告 Load/Show/Finish 事件",
    )
    parser.add_argument(
        "--confirm-reward-may-be-granted",
        action="store_true",
        help="确认接受测试请求可能导致奖励真实发放",
    )
    parser.add_argument(
        "--expected-ws-fragment",
        help="必须出现在 WebSocket 地址中的测试服片段，用于防止误连正式服",
    )
    parser.add_argument(
        "--reuse-session",
        action="store_true",
        help="复用 HAR 的 WebSocket 登录帧，不刷新 HTTP 登录",
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
        help="额外信任的 PEM CA 证书，例如 Proxyman 的 proxyman-ca.pem",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout 必须大于 0")
    if args.ca_file is not None and not args.ca_file.exists():
        parser.error("--ca-file 不存在：%s" % args.ca_file)

    har_path = args.har or find_default_har()
    if not har_path.exists():
        print("HAR 不存在：%s" % har_path, file=sys.stderr)
        return 2

    try:
        session = extract_session(har_path)
    except (
        OSError,
        ValueError,
        json.JSONDecodeError,
        HarFormatError,
        ProtocolError,
    ) as exc:
        print("HAR 解析失败：%s" % exc, file=sys.stderr)
        return 2

    if args.login_har is not None:
        if not args.login_har.exists():
            print("登录 HAR 不存在：%s" % args.login_har, file=sys.stderr)
            return 2
        try:
            login_har: Dict[str, Any] = json.loads(
                args.login_har.read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError) as exc:
            print("登录 HAR 解析失败：%s" % exc, file=sys.stderr)
            return 2
        session.login_info_request = extract_http_request(
            login_har, "login/info"
        )
        session.login_do_request = extract_http_request(login_har, "login.do")

    print_dry_run(har_path, session)
    if not args.live:
        print("[离线] 未连接服务器。")
        return 0

    try:
        return asyncio.run(run_probe(args, session))
    except KeyboardInterrupt:
        print("\n[停止] 用户中断", file=sys.stderr)
        return 130
    except Exception as exc:
        print("[失败] %s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
