#!/usr/bin/env python3
"""绮罗锦衣特惠直购任务。"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from gt_automation.network import format_user_error
from gt_automation.paths import DEFAULT_DATA_DIR
from gt_automation.qiluo_jingyi import (
    CMD_QILUO_PURCHASE,
    CMD_QILUO_QUERY,
    CMD_QILUO_RESULT,
    QiluoJingyiError,
    load_qiluo_jingyi_config,
    parse_qiluo_jingyi_result,
    update_qiluo_jingyi_result,
)
from siyi_star_auto import (
    PacketClient,
    ProtocolError,
    build_frame,
    create_ssl_context,
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

    config = load_qiluo_jingyi_config(args.data_dir)
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

            print("[查询] 绮罗锦衣活动 ID=%d" % config.activity_id)
            await client.request(
                CMD_QILUO_QUERY,
                pb_uint(1, config.activity_id),
            )
            print(
                "[直购] 提交命令 5515，活动=%d 商品=%d"
                % (config.activity_id, config.product_id)
            )
            response = await client.request(
                CMD_QILUO_PURCHASE,
                config.purchase_payload,
            )
            result = parse_qiluo_jingyi_result(response)
            if not result.rewards or result.status != 1:
                raise ProtocolError(
                    "命令 5515 未返回购买成功结果。可能原因：商品已购买、"
                    "货币或代币不足、活动状态变化、会话过期或服务端拒绝。"
                )
            update_qiluo_jingyi_result(config, result, args.data_dir)
            payload = {
                "activity_id": result.activity_id,
                "product_id": result.product_id,
                "status": result.status,
                "rewards": result.rewards,
            }
            print(
                "[绮罗锦衣结果] %s"
                % json.dumps(payload, ensure_ascii=False)
            )
            for index, reward in enumerate(result.rewards, start=1):
                print("[奖励] %d. %s" % (index, reward))
            print("[完成] 绮罗锦衣特惠直购完成")
            return 0
        finally:
            await client.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="绮罗锦衣特惠直购任务"
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help="account.json 与 qiluo_jingyi.json 所在目录",
    )
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
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
        config = load_qiluo_jingyi_config(args.data_dir)
    except (OSError, ValueError, json.JSONDecodeError, QiluoJingyiError) as exc:
        print("qiluo_jingyi.json 读取失败：%s" % exc, file=sys.stderr)
        return 2

    print(
        "[配置文件] %s"
        % (args.data_dir / "qiluo_jingyi.json")
    )
    print("[活动] activity_id=%d" % config.activity_id)
    print("[商品] product_id=%d" % config.product_id)
    print("[请求帧] %s" % build_frame(
        CMD_QILUO_PURCHASE,
        config.purchase_payload,
        context_id=config.session.context_id,
    ).hex())
    print("[上次奖励] %s" % "&".join(config.last_rewards))
    print("[响应命令] %d" % CMD_QILUO_RESULT)
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
