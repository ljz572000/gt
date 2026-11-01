#!/usr/bin/env python3
"""每日福利奖励的离线检查与显式确认后领奖工具。

安全边界：
1. 默认只读取账号与任务文件，不连接游戏服务器。
2. 不模拟、伪造或重放激励视频广告事件。
3. 只有人工在官方客户端完整看完广告后，才能使用
   ``--live --confirm-ad-watched`` 重试提交领奖命令。

用法：
    # 首次从 HAR 导入拆分账号与任务文件
    python3 daily_welfare_claim.py \
        --har "WeChatAppEx Helper_10-31-2026-09-52-46.har"

    # 之后不再需要 HAR，直接从 task/daily_welfare.json 运行
    python3 daily_welfare_claim.py --live --confirm-ad-watched

账号与任务文件包含登录令牌，文件权限固定为 0600。
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from gt_automation.session_store import (
    SessionFileError,
    SessionMetadata,
    TASK_DAILY,
    extract_gt_session,
    load_gt_metadata,
    load_gt_session as load_gt_session_store,
    save_import,
    save_task_session,
)
from gt_automation.paths import DEFAULT_DATA_DIR
from gt_automation.network import format_user_error
from siyi_star_auto import (
    CMD_ACK,
    CMD_QUERY,
    HarFormatError,
    HarSession,
    PacketClient,
    ProtocolError,
    StarState,
    build_frame,
    create_ssl_context,
    extract_http_request,
    field_map,
    first_value,
    header_value,
    parse_frame,
    refresh_login_session,
)


CMD_WELFARE_CLAIM = 5960
CMD_AD_STATUS = 5377
CMD_INIT_COMPLETE = 1001

@dataclass(frozen=True)
class WelfareClaimResult:
    status: Optional[int]
    server_time: Optional[int]
    reward_id: str


def _empty_star_state(activity_id: int = 0) -> StarState:
    """每日福利模式不依赖四艺星盘状态，用空状态满足共享会话类型。"""
    return StarState(
        activity_id=activity_id,
        astrology_result=0,
        star_level=0,
        unknown_4=None,
        protection_progress=0,
        unknown_6=0,
    )


def extract_welfare_session(har_path: Path) -> HarSession:
    """从 HAR 提取每日福利所需的登录与会话数据。

    与四艺星盘的 extract_session 不同，本函数不要求 HAR 中存在
    命令 6589。上下文 ID 可以从任意带 field8 的发送帧中取得。
    """
    with har_path.open("r", encoding="utf-8") as handle:
        har = json.load(handle)

    candidates: List[Tuple[str, Dict[str, Any], List[Dict[str, Any]]]] = []
    for entry in har.get("log", {}).get("entries", []):
        messages = entry.get("_webSocketMessages") or []
        has_login = False
        for message in messages:
            if message.get("type") != "send":
                continue
            try:
                packet = parse_frame(base64.b64decode(message.get("data", "")))
            except Exception:
                continue
            if packet.command == 1000:
                has_login = True
                break
        if has_login:
            candidates.append(
                (str(entry.get("startedDateTime", "")), entry, messages)
            )

    if not candidates:
        raise HarFormatError("HAR 中没有找到包含命令 1000 登录帧的 WebSocket 会话")

    _timestamp, entry, messages = max(candidates, key=lambda item: item[0])
    auth_index = -1
    auth_frame: Optional[bytes] = None
    for index, message in enumerate(messages):
        if message.get("type") != "send":
            continue
        try:
            packet = parse_frame(base64.b64decode(message.get("data", "")))
        except Exception:
            continue
        if packet.command == 1000:
            auth_index = index
            auth_frame = base64.b64decode(message["data"])
            break
    if auth_frame is None or auth_index < 0:
        raise HarFormatError("无法提取 WebSocket 登录帧")

    context_counts: Counter[int] = Counter()
    activity_id = 0
    target_index: Optional[int] = None
    init_complete_index: Optional[int] = None

    for index, message in enumerate(messages[auth_index + 1 :], start=auth_index + 1):
        try:
            packet = parse_frame(base64.b64decode(message.get("data", "")))
        except Exception:
            continue
        if message.get("type") == "receive":
            if (
                packet.command == CMD_INIT_COMPLETE
                and init_complete_index is None
            ):
                init_complete_index = index
            continue
        if message.get("type") != "send":
            continue

        if packet.context_id is not None:
            context_counts[packet.context_id] += 1
        if packet.command == CMD_QUERY:
            fields = field_map(packet.payload)
            query_activity_id = first_value(fields, 1)
            if isinstance(query_activity_id, int):
                activity_id = query_activity_id
        if (
            packet.command in (CMD_WELFARE_CLAIM, CMD_QUERY)
            and target_index is None
        ):
            target_index = index

    if not context_counts:
        raise HarFormatError("HAR 的发送帧中没有可用的上下文 ID field8")
    context_id = context_counts.most_common(1)[0][0]

    # 优先按活动请求/领奖请求截断；若只是游戏启动 HAR，则停在首个
    # 1001 初始化完成响应，避免回放后续无关业务请求。
    bootstrap_end = (
        target_index
        if target_index is not None
        else init_complete_index
        if init_complete_index is not None
        else len(messages)
    )
    auth_time = float(messages[auth_index].get("time", 0.0))
    init_frames: List[Tuple[float, bytes]] = []
    for message in messages[auth_index + 1 : bootstrap_end]:
        if message.get("type") != "send":
            continue
        try:
            frame = base64.b64decode(message["data"])
            packet = parse_frame(frame)
        except Exception:
            continue
        # ACK 是旧会话推送序号，不能在新连接中重放。
        # 5377 是广告状态帧，本工具的安全边界禁止发送。
        if packet.command in (1000, CMD_ACK, CMD_AD_STATUS):
            continue
        offset = max(0.0, float(message.get("time", 0.0)) - auth_time)
        init_frames.append((offset, frame))

    login_info_request = extract_http_request(har, "login/info")
    login_do_request = extract_http_request(har, "login.do")
    if login_info_request is None or login_do_request is None:
        raise HarFormatError(
            "HAR 缺少完整的 login/info 或 login.do 请求，无法刷新登录"
        )

    request = entry.get("request", {})
    headers = request.get("headers", [])
    origin = header_value(headers, "origin") or "https://p2ph5-gate.9917.com"
    user_agent = header_value(headers, "user-agent")
    if not user_agent:
        raise HarFormatError("HAR 的 WebSocket 请求中没有 User-Agent")

    return HarSession(
        har_path=har_path,
        websocket_url=str(request["url"]),
        auth_frame=auth_frame,
        context_id=context_id,
        activity_id=activity_id,
        current_state=_empty_star_state(activity_id),
        init_frames=init_frames,
        origin=origin,
        user_agent=user_agent,
        login_info_request=login_info_request,
        login_do_request=login_do_request,
    )


def save_session_cache(
    session: HarSession,
    base_dir: Path,
    last_reward: Optional[WelfareClaimResult],
) -> None:
    """兼容旧调用：将每日福利会话写入拆分后的任务文件。"""
    metadata = SessionMetadata(
        source_har=str(session.har_path),
        created_at=datetime.now(timezone.utc).isoformat(),
        star_ready=session.activity_id > 0,
        context_id=session.context_id,
        activity_id=session.activity_id,
        init_frames=len(session.init_frames),
        last_reward=(
            {
                "status": last_reward.status,
                "server_time": last_reward.server_time,
                "reward_id": last_reward.reward_id,
            }
            if last_reward is not None
            else None
        ),
    )
    save_task_session(session, base_dir, metadata, task=TASK_DAILY)


def load_session_cache(base_dir: Path) -> Tuple[HarSession, Optional[WelfareClaimResult]]:
    session, metadata = load_gt_session_store(base_dir, task=TASK_DAILY)
    reward_data = metadata.last_reward
    last_reward: Optional[WelfareClaimResult] = None
    if isinstance(reward_data, dict):
        reward_id = reward_data.get("reward_id")
        if isinstance(reward_id, str) and reward_id:
            status = reward_data.get("status")
            server_time = reward_data.get("server_time")
            last_reward = WelfareClaimResult(
                status=int(status) if isinstance(status, int) else None,
                server_time=(
                    int(server_time) if isinstance(server_time, int) else None
                ),
                reward_id=reward_id,
            )
    return session, last_reward


def decode_claim_result(payload: bytes) -> WelfareClaimResult:
    fields = field_map(payload)
    status = first_value(fields, 1)
    server_time = first_value(fields, 2)
    reward = first_value(fields, 3, b"")
    if isinstance(reward, bytes):
        reward_text = reward.decode("utf-8", "replace")
    elif isinstance(reward, str):
        reward_text = reward
    else:
        reward_text = str(reward)
    return WelfareClaimResult(
        status=int(status) if isinstance(status, int) else None,
        server_time=int(server_time) if isinstance(server_time, int) else None,
        reward_id=reward_text,
    )


def find_last_har_reward(har_path: Path) -> Optional[WelfareClaimResult]:
    """从 HAR 中找出最后一次成功的命令 5960 响应。"""
    with har_path.open("r", encoding="utf-8") as handle:
        har = json.load(handle)

    result: Optional[WelfareClaimResult] = None
    for entry in har.get("log", {}).get("entries", []):
        for message in entry.get("_webSocketMessages") or []:
            if message.get("type") != "receive":
                continue
            try:
                frame = base64.b64decode(message.get("data", ""))
                packet = parse_frame(frame)
            except Exception:
                continue
            if packet.command != CMD_WELFARE_CLAIM:
                continue
            decoded = decode_claim_result(packet.payload)
            if decoded.reward_id:
                result = decoded
    return result


def print_session_summary(
    source: str,
    session: HarSession,
    sample: Optional[WelfareClaimResult],
) -> None:
    print(source)
    print("[会话] %s" % session.websocket_url)
    print("[上下文] field8=%d" % session.context_id)

    claim_frame = build_frame(
        CMD_WELFARE_CLAIM,
        b"",
        context_id=session.context_id,
    )
    print("[领奖帧] %s" % claim_frame.hex())

    if sample is None:
        print("[样本] 没有找到命令 5960 的成功领奖响应")
        return
    print(
        "[样本] 每日福利奖励=%s status=%s server_time=%s"
        % (sample.reward_id, sample.status, sample.server_time)
    )


async def claim_after_manual_ad(
    args: argparse.Namespace,
    session: HarSession,
) -> int:
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
            activity_id=session.activity_id,
            timeout=args.timeout,
        )
        client.start()
        try:
            await client.authenticate(session.auth_frame)
            if not args.no_bootstrap:
                await client.send_bootstrap(session.init_frames)

            print("[领奖] 提交命令 5960；不会发送任何广告事件")
            response = await client.request(CMD_WELFARE_CLAIM, b"")
            result = decode_claim_result(response)
            if not result.reward_id:
                raise ProtocolError(
                    "命令 5960 未返回奖励。可能原因：广告未在官方客户端完成、"
                    "奖励已领取、会话过期或服务端拒绝。"
                )
            print(
                "[完成] reward_id=%s status=%s server_time=%s"
                % (result.reward_id, result.status, result.server_time)
            )
            try:
                metadata = load_gt_metadata(args.data_dir)
                metadata.last_reward = {
                    "reward_id": result.reward_id,
                    "status": result.status,
                    "server_time": result.server_time,
                }
                save_task_session(
                    session,
                    args.data_dir,
                    metadata,
                    task=TASK_DAILY,
                )
                print("[记录] 已更新每日福利任务中的最近奖励")
            except Exception as exc:
                print("[警告] 无法更新每日福利奖励记录：%s" % exc)
            return 0
        finally:
            await client.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "从 HAR 导入一次会话，之后无需 HAR 即可领取每日福利"
        )
    )
    parser.add_argument(
        "--har",
        type=Path,
        help=(
            "首次导入时使用的 HAR；省略时从拆分数据目录加载"
        ),
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help="account.json 与 task/ 所在目录，默认程序根目录",
    )
    parser.add_argument(
        "--login-har",
        type=Path,
        help="高级选项：用另一份 HAR 替换当前内存中的登录请求",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只做离线检查（默认行为）",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="连接游戏服务器；必须同时提供 --confirm-ad-watched",
    )
    parser.add_argument(
        "--confirm-ad-watched",
        action="store_true",
        help="确认已在官方客户端完整看完本次激励视频，且未成功领取",
    )
    parser.add_argument(
        "--reuse-session",
        action="store_true",
        help="直接复用任务文件中的 WebSocket 登录帧，不刷新 HTTP 登录",
    )
    parser.add_argument(
        "--no-bootstrap",
        action="store_true",
        help="登录后不发送任务文件中的初始化帧",
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
    if args.live and not args.confirm_ad_watched:
        parser.error(
            "--live 必须同时提供 --confirm-ad-watched；"
            "本工具不模拟或伪造激励视频广告完成事件"
        )

    sample: Optional[WelfareClaimResult] = None
    if args.har is not None:
        if not args.har.exists():
            print("HAR 不存在：%s" % args.har, file=sys.stderr)
            return 2
        try:
            import_result = extract_gt_session(args.har)
            session = import_result.session
            save_import(
                session,
                args.data_dir,
                import_result.metadata,
                replace=True,
            )
            reward = import_result.metadata.last_reward
            if isinstance(reward, dict) and reward.get("reward_id"):
                sample = WelfareClaimResult(
                    status=reward.get("status"),
                    server_time=reward.get("server_time"),
                    reward_id=str(reward["reward_id"]),
                )
        except (
            OSError,
            ValueError,
            json.JSONDecodeError,
            ProtocolError,
            HarFormatError,
            SessionFileError,
        ) as exc:
            print("HAR 解析失败：%s" % exc, file=sys.stderr)
            return 2
        print("[会话] 已保存拆分配置：%s" % args.data_dir)
        summary_source = "[HAR] %s" % args.har
    else:
        try:
            session, sample = load_session_cache(args.data_dir)
        except (
            OSError,
            ValueError,
            json.JSONDecodeError,
            ProtocolError,
            HarFormatError,
            SessionFileError,
        ) as exc:
            print(
                "账号或每日福利任务文件读取失败：%s\n"
                "首次使用请添加 --har 导入当前版本 HAR。" % exc,
                file=sys.stderr,
            )
            return 2
        summary_source = "[数据目录] %s" % args.data_dir

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
        login_info_request = extract_http_request(
            login_har, "login/info"
        )
        login_do_request = extract_http_request(login_har, "login.do")
        if login_info_request is None or login_do_request is None:
            print(
                "登录 HAR 缺少完整的 login/info 或 login.do 请求",
                file=sys.stderr,
            )
            return 2
        session.login_info_request = login_info_request
        session.login_do_request = login_do_request

    print_session_summary(summary_source, session, sample)

    if not args.live:
        print(
            "[离线] 未连接服务器。请在官方客户端真实看完广告后，"
            "再使用 --live --confirm-ad-watched。"
        )
        return 0

    try:
        return asyncio.run(claim_after_manual_ad(args, session))
    except KeyboardInterrupt:
        print("\n[停止] 用户中断", file=sys.stderr)
        return 130
    except Exception as exc:
        message = format_user_error(exc)
        print("[失败] %s" % message, file=sys.stderr)
        if "code=1626" in message:
            print(
                "[提示] 游戏客户端版本已更新。请使用当前版本生成的 HAR "
                "重新导入：--har <新HAR> --data-dir %s" % args.data_dir,
                file=sys.stderr,
            )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
