#!/usr/bin/env python3
"""招贤十连任务，使用账号和 zhaoxian.json 配置。"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from gt_automation.network import format_user_error
from gt_automation.paths import DEFAULT_DATA_DIR
from gt_automation.zhaoxian import (
    CMD_ACTIVITY_QUERY,
    CMD_ZHAOXIAN_DRAW,
    ZhaoxianError,
    load_zhaoxian_config,
    parse_zhaoxian_result,
    update_zhaoxian_results,
)
from siyi_star_auto import (
    PacketClient,
    ProtocolError,
    build_frame,
    create_ssl_context,
    first_value,
    field_map,
    pb_uint,
    refresh_login_session,
)


async def run_live(args: argparse.Namespace) -> int:
    try:
        import websockets
    except ImportError:
        print(
            "缺少依赖 websockets，请执行：pip install -r requirements.txt",
            file=sys.stderr,
        )
        return 2

    config = load_zhaoxian_config(args.data_dir)
    ssl_context = create_ssl_context(args.ca_file)
    session = config.session
    if not args.reuse_session:
        session = refresh_login_session(session, args.timeout, ssl_context)

    headers = {
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
        extra_headers=headers,
        max_size=None,
        ping_interval=20,
        ping_timeout=20,
    ) as websocket:
        client = PacketClient(
            websocket=websocket,
            context_id=session.context_id,
            activity_id=config.activity_id,
            timeout=args.timeout,
        )
        client.start()
        try:
            await client.authenticate(session.auth_frame)
            if not args.no_bootstrap:
                await client.send_bootstrap(session.init_frames)

            print("[查询] 活动 ID=%d" % config.activity_id)
            state = await client.request(
                CMD_ACTIVITY_QUERY,
                pb_uint(1, config.activity_id),
            )
            state_fields = field_map(state)
            print(
                "[状态] count=%s values=%s"
                % (
                    first_value(state_fields, 2),
                    [
                        value
                        for value in state_fields.get(3, [])
                        if isinstance(value, int)
                    ],
                )
            )

            print(
                "[招贤] 提交十连请求 1627；不发送 5377 广告状态帧"
            )
            response = await client.request(
                CMD_ZHAOXIAN_DRAW,
                config.draw_payload,
            )
            result = parse_zhaoxian_result(response)
            if not result.results:
                raise ProtocolError(
                    "命令 1627 未返回招贤结果。可能原因：广告奖励未生效、"
                    "抽卡次数不足、会话过期或服务端拒绝。"
                )

            payload = {
                "activity_id": config.activity_id,
                "count": len(result.results),
                "results": result.results,
                "field2": result.field2,
                "field3": result.field3,
                "item_change": result.item_change,
                "flag": result.flag,
            }
            update_zhaoxian_results(config, result, args.data_dir)
            print("[招贤结果] %s" % json.dumps(payload, ensure_ascii=False))
            for index, item in enumerate(result.results, start=1):
                print("[结果] %d. %s" % (index, item))
            print("[完成] 招贤十连完成，共 %d 项" % len(result.results))
            return 0
        finally:
            await client.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="招贤十连任务")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help="account.json 与 zhaoxian.json 所在目录",
    )
    parser.add_argument("--live", action="store_true", help="连接服务器执行")
    parser.add_argument("--dry-run", action="store_true", help="只做离线检查")
    parser.add_argument("--reuse-session", action="store_true")
    parser.add_argument("--no-bootstrap", action="store_true")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--ca-file", type=Path)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout 必须大于 0")
    if args.ca_file is not None and not args.ca_file.exists():
        parser.error("--ca-file 不存在：%s" % args.ca_file)
    try:
        config = load_zhaoxian_config(args.data_dir)
    except (OSError, ValueError, json.JSONDecodeError, ZhaoxianError) as exc:
        print("zhaoxian.json 读取失败：%s" % exc, file=sys.stderr)
        return 2

    print("[配置文件] %s" % (args.data_dir / "zhaoxian.json"))
    print("[活动] activity_id=%d" % config.activity_id)
    print("[请求帧] %s" % build_frame(
        CMD_ZHAOXIAN_DRAW,
        config.draw_payload,
        context_id=config.session.context_id,
    ).hex())
    print("[上次结果] %d 项" % len(config.last_results))
    if not args.live:
        print("[离线] 未连接服务器。确认风险后添加 --live 实际执行。")
        return 0

    try:
        return asyncio.run(run_live(args))
    except KeyboardInterrupt:
        print("\n[停止] 用户中断", file=sys.stderr)
        return 130
    except Exception as exc:
        print("[失败] %s" % format_user_error(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
