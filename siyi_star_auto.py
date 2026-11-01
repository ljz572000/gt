#!/usr/bin/env python3
"""四艺星盘自动升星工具。

默认只做离线分析，不连接游戏服务器。确认账号风险并需要实际执行时，
必须显式添加 --live 参数。

依赖：
    pip install -r requirements.txt

示例：
    # 从拆分任务文件离线检查协议字段和待执行步骤
    python3 siyi_star_auto.py --data-dir . --dry-run

    # 使用 task/siyi_star.json 中的登录请求刷新会话
    python3 siyi_star_auto.py --data-dir . --live

说明：
1. 账号和任务文件由桌面应用从 HAR 导入生成。
2. 如果刷新登录失败，请重新导入当前版本 HAR。
3. 自动购买会消耗游戏货币，默认关闭，必须同时指定
   --auto-buy --allow-purchase；或使用 --pre-buy 提前购买。
4. 游戏协议可能更新，命令号和字段含义可能随版本变化。
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import ssl
import struct
import sys
import time
import urllib.parse
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from gt_automation.paths import DEFAULT_DATA_DIR
from gt_automation.network import format_user_error


CMD_QUERY = 6589
CMD_UPGRADE = 6590
CMD_CLAIM = 6591
CMD_PUSH = 3504
CMD_ACK = 2527
CMD_BUY = 1163

MODE_NORMAL = 1
MODE_PROTECTED = 2
BUY_UNIVERSAL_ITEM = 999

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/144.0.0.0 Safari/537.36 "
    "MicroMessenger/7.0.20.1781(0x6700143B) "
    "NetType/WIFI MiniProgramEnv/Mac "
    "MacWechat/WMPF MacWechat/3.8.7(0x13080712) "
    "UnifiedPCMacWechat(0xf2641f50) XWEB/25755"
)


class HarFormatError(RuntimeError):
    pass


class ProtocolError(RuntimeError):
    pass


def encode_varint(value: int) -> bytes:
    if value < 0:
        raise ValueError("varint cannot be negative")
    result = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            result.append(byte | 0x80)
        else:
            result.append(byte)
            return bytes(result)


def decode_varint(data: bytes, offset: int = 0) -> Tuple[int, int]:
    value = 0
    shift = 0
    pos = offset
    while pos < len(data):
        byte = data[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, pos
        shift += 7
        if shift > 63:
            raise ProtocolError("varint overflow")
    raise ProtocolError("truncated varint")


def pb_key(field_number: int, wire_type: int) -> bytes:
    return encode_varint((field_number << 3) | wire_type)


def pb_uint(field_number: int, value: int) -> bytes:
    return pb_key(field_number, 0) + encode_varint(value)


def pb_bytes(field_number: int, value: bytes) -> bytes:
    return pb_key(field_number, 2) + encode_varint(len(value)) + value


def pb_string(field_number: int, value: str) -> bytes:
    return pb_bytes(field_number, value.encode("utf-8"))


def parse_fields(data: bytes) -> List[Tuple[int, int, Any]]:
    """解析 protobuf 风格字段，保留 wire type 和原始值。"""
    fields: List[Tuple[int, int, Any]] = []
    pos = 0
    while pos < len(data):
        key, pos = decode_varint(data, pos)
        field_number = key >> 3
        wire_type = key & 0x07
        if field_number == 0:
            raise ProtocolError("invalid field number 0")

        if wire_type == 0:
            value, pos = decode_varint(data, pos)
        elif wire_type == 1:
            end = pos + 8
            if end > len(data):
                raise ProtocolError("truncated fixed64 field")
            value = data[pos:end]
            pos = end
        elif wire_type == 2:
            length, pos = decode_varint(data, pos)
            end = pos + length
            if end > len(data):
                raise ProtocolError("truncated length-delimited field")
            value = data[pos:end]
            pos = end
        elif wire_type == 5:
            end = pos + 4
            if end > len(data):
                raise ProtocolError("truncated fixed32 field")
            value = data[pos:end]
            pos = end
        else:
            raise ProtocolError("unsupported wire type: %d" % wire_type)

        fields.append((field_number, wire_type, value))
    return fields


def field_map(data: bytes) -> Dict[int, List[Any]]:
    result: Dict[int, List[Any]] = {}
    for field_number, _wire_type, value in parse_fields(data):
        result.setdefault(field_number, []).append(value)
    return result


def first_value(fields: Dict[int, List[Any]], number: int, default: Any = None) -> Any:
    values = fields.get(number)
    return values[0] if values else default


@dataclass
class Packet:
    command: int
    payload: bytes
    context_id: Optional[int] = None


def parse_frame(frame: bytes) -> Packet:
    if len(frame) < 4:
        raise ProtocolError("frame is shorter than 4 bytes")
    declared_length = struct.unpack(">I", frame[:4])[0]
    if declared_length != len(frame) - 4:
        raise ProtocolError(
            "frame length mismatch: declared=%d actual=%d"
            % (declared_length, len(frame) - 4)
        )

    fields = field_map(frame[4:])
    command = first_value(fields, 1)
    if not isinstance(command, int):
        raise ProtocolError("frame has no command field")
    payload = first_value(fields, 15, b"")
    if not isinstance(payload, bytes):
        raise ProtocolError("invalid business payload")
    context_id = first_value(fields, 8)
    if not isinstance(context_id, int):
        context_id = None
    return Packet(command=command, payload=payload, context_id=context_id)


def build_frame(command: int, payload: bytes, context_id: Optional[int] = None) -> bytes:
    body = bytearray()
    body.extend(pb_uint(1, command))
    body.extend(pb_bytes(15, payload))
    if context_id is not None:
        body.extend(pb_uint(8, context_id))
    body.extend(pb_string(2, "nil"))
    return struct.pack(">I", len(body)) + bytes(body)


def header_value(headers: Iterable[Dict[str, Any]], name: str) -> Optional[str]:
    wanted = name.lower()
    for header in headers:
        if str(header.get("name", "")).lower() == wanted:
            return str(header.get("value", ""))
    return None


@dataclass
class StarState:
    activity_id: int
    astrology_result: int
    star_level: int
    unknown_4: Optional[int]
    protection_progress: int
    unknown_6: int

    @classmethod
    def from_payload(cls, payload: bytes) -> "StarState":
        fields = field_map(payload)
        activity_id = first_value(fields, 1)
        star_level = first_value(fields, 3)
        if not isinstance(activity_id, int) or not isinstance(star_level, int):
            raise ProtocolError("query response missing activity_id or star_level")
        return cls(
            activity_id=activity_id,
            astrology_result=int(first_value(fields, 2, 0)),
            star_level=star_level,
            unknown_4=first_value(fields, 4),
            protection_progress=int(first_value(fields, 5, 0)),
            unknown_6=int(first_value(fields, 6, 0)),
        )


@dataclass
class UpgradeResult:
    activity_id: int
    mode: int
    roll_result: int
    result_code: Optional[int]
    stars_gained: int
    success: bool
    empty_response: bool = False

    @classmethod
    def from_payload(cls, payload: bytes) -> "UpgradeResult":
        fields = field_map(payload)
        activity_id = first_value(fields, 1)
        mode = first_value(fields, 2)
        if not isinstance(activity_id, int) or not isinstance(mode, int):
            raise ProtocolError("upgrade response missing activity_id or mode")
        result_code = first_value(fields, 4)
        stars_gained = int(first_value(fields, 5, 0))
        return cls(
            activity_id=activity_id,
            mode=mode,
            roll_result=int(first_value(fields, 3, 0)),
            result_code=int(result_code) if isinstance(result_code, int) else None,
            stars_gained=stars_gained,
            success=stars_gained > 0,
        )


@dataclass
class HttpRequestSpec:
    url: str
    headers: Dict[str, str]
    body: bytes


@dataclass
class HarSession:
    har_path: Path
    websocket_url: str
    auth_frame: bytes
    context_id: int
    activity_id: int
    current_state: StarState
    init_frames: List[Tuple[float, bytes]]
    origin: str
    user_agent: str
    login_info_request: Optional[HttpRequestSpec]
    login_do_request: Optional[HttpRequestSpec]


def iter_websocket_entries(har: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    for entry in har.get("log", {}).get("entries", []):
        messages = entry.get("_webSocketMessages")
        if messages:
            yield entry


def extract_http_request(
    har: Dict[str, Any], url_fragment: str
) -> Optional[HttpRequestSpec]:
    candidates = []
    for entry in har.get("log", {}).get("entries", []):
        request = entry.get("request", {})
        if url_fragment not in str(request.get("url", "")):
            continue
        post_data = request.get("postData") or {}
        if not post_data.get("text"):
            continue
        candidates.append((entry.get("startedDateTime", ""), request, post_data))
    if not candidates:
        return None

    _timestamp, request, post_data = max(candidates, key=lambda item: item[0])
    blocked_headers = {
        "host",
        "content-length",
        "connection",
        "accept-encoding",
    }
    headers = {
        str(header["name"]): str(header["value"])
        for header in request.get("headers", [])
        if str(header.get("name", "")).lower() not in blocked_headers
    }
    return HttpRequestSpec(
        url=str(request["url"]),
        headers=headers,
        body=str(post_data["text"]).encode("utf-8"),
    )


def create_ssl_context(ca_file: Optional[Path] = None) -> ssl.SSLContext:
    """同时信任系统 CA 和可选的代理 CA。

    SSL_CERT_FILE 会替换 Python 的默认 CA 路径，而不是追加信任。
    因此先临时移除该变量加载系统 CA，再追加 Proxyman 等自定义 CA，
    避免 HTTP 代理证书和 WebSocket 上游证书只能信任其中一种。
    """
    env_ca_file = os.environ.get("SSL_CERT_FILE")
    env_ca_dir = os.environ.get("SSL_CERT_DIR")

    old_ca_file = os.environ.pop("SSL_CERT_FILE", None)
    old_ca_dir = os.environ.pop("SSL_CERT_DIR", None)
    try:
        context = ssl.create_default_context()
    finally:
        if old_ca_file is not None:
            os.environ["SSL_CERT_FILE"] = old_ca_file
        if old_ca_dir is not None:
            os.environ["SSL_CERT_DIR"] = old_ca_dir

    custom_ca = ca_file
    if custom_ca is None and env_ca_file:
        custom_ca = Path(env_ca_file)
    if custom_ca is not None:
        context.load_verify_locations(cafile=str(custom_ca))
    if env_ca_dir:
        context.load_verify_locations(capath=env_ca_dir)

    try:
        import certifi

        context.load_verify_locations(cafile=certifi.where())
    except ImportError:
        pass
    return context


def http_post_json(
    spec: HttpRequestSpec,
    body: bytes,
    timeout: float,
    ssl_context: Optional[ssl.SSLContext] = None,
) -> Dict[str, Any]:
    request = urllib.request.Request(
        spec.url,
        data=body,
        headers=spec.headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(
            request,
            timeout=timeout,
            context=ssl_context,
        ) as response:
            raw = response.read()
    except (urllib.error.URLError, ssl.SSLError, OSError) as exc:
        raise ProtocolError(format_user_error(exc)) from exc
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError("登录接口返回了非 JSON 内容") from exc
    if str(payload.get("code")) != "0":
        raise ProtocolError(
            "登录接口失败：code=%s msg=%s"
            % (payload.get("code"), payload.get("msg"))
        )
    return payload


def replace_protobuf_field(
    data: bytes, field_number: int, new_value: bytes
) -> bytes:
    result = bytearray()
    found = False
    for number, wire_type, value in parse_fields(data):
        if number == field_number and wire_type == 2:
            result.extend(pb_bytes(number, new_value))
            found = True
        elif wire_type == 0:
            result.extend(pb_uint(number, int(value)))
        elif wire_type == 2:
            result.extend(pb_bytes(number, bytes(value)))
        elif wire_type == 1:
            result.extend(pb_key(number, 1) + bytes(value))
        elif wire_type == 5:
            result.extend(pb_key(number, 5) + bytes(value))
        else:
            raise ProtocolError("无法重建 protobuf wire type %d" % wire_type)
    if not found:
        raise ProtocolError("登录帧中未找到待替换字段 %d" % field_number)
    return bytes(result)


def refresh_login_session(
    session: HarSession,
    timeout: float,
    ssl_context: Optional[ssl.SSLContext] = None,
) -> HarSession:
    if session.login_info_request is None or session.login_do_request is None:
        raise ProtocolError(
            "HAR 中没有完整的 login/info 与 login.do 请求，"
            "无法刷新会话；可使用 --reuse-session"
        )

    print("[登录] 使用 HAR 中的微信登录请求换取新令牌")
    info_payload = http_post_json(
        session.login_info_request,
        session.login_info_request.body,
        timeout,
        ssl_context,
    )
    info_obj = info_payload.get("obj") or {}
    token = info_obj.get("token")
    if not isinstance(token, str) or not token:
        raise ProtocolError("login/info 响应中没有 token")

    print("[登录] 换取新的游戏会话")
    form = dict(
        urllib.parse.parse_qsl(
            session.login_do_request.body.decode("utf-8", "replace"),
            keep_blank_values=True,
        )
    )
    form["pWord"] = token
    login_body = urllib.parse.urlencode(form).encode("utf-8")
    login_payload = http_post_json(
        session.login_do_request,
        login_body,
        timeout,
        ssl_context,
    )
    login_obj = login_payload.get("obj") or {}
    game_session = login_obj.get("g_session")
    if not isinstance(game_session, str) or not game_session:
        raise ProtocolError("login.do 响应中没有 g_session")

    auth_packet = parse_frame(session.auth_frame)
    new_auth_payload = replace_protobuf_field(
        auth_packet.payload,
        1,
        game_session.encode("utf-8"),
    )
    client_version = form.get("pVer")
    if client_version:
        try:
            new_auth_payload = replace_protobuf_field(
                new_auth_payload,
                3,
                client_version.encode("utf-8"),
            )
        except ProtocolError:
            pass
    new_auth_frame = build_frame(1000, new_auth_payload, auth_packet.context_id)
    websocket_url = str(login_obj.get("conn_gate") or session.websocket_url)
    print("[登录] 已刷新 WebSocket 会话")
    return HarSession(
        har_path=session.har_path,
        websocket_url=websocket_url,
        auth_frame=new_auth_frame,
        context_id=session.context_id,
        activity_id=session.activity_id,
        current_state=session.current_state,
        init_frames=session.init_frames,
        origin=session.origin,
        user_agent=session.user_agent,
        login_info_request=session.login_info_request,
        login_do_request=session.login_do_request,
    )


def extract_session(har_path: Path) -> HarSession:
    with har_path.open("r", encoding="utf-8") as handle:
        har = json.load(handle)

    candidates: List[Tuple[str, Dict[str, Any]]] = []
    for entry in iter_websocket_entries(har):
        entries = entry.get("_webSocketMessages", [])
        has_login = False
        for message in entries:
            if message.get("type") != "send":
                continue
            try:
                packet = parse_frame(base64.b64decode(message["data"]))
            except Exception:
                continue
            if packet.command == 1000:
                has_login = True
                break
        if has_login:
            candidates.append((entry.get("startedDateTime", ""), entry))

    if not candidates:
        raise HarFormatError("没有找到包含命令 1000 登录帧的 WebSocket 会话")

    _timestamp, entry = max(candidates, key=lambda item: item[0])
    messages = entry.get("_webSocketMessages", [])
    auth_frame: Optional[bytes] = None
    auth_index = -1
    for index, message in enumerate(messages):
        if message.get("type") != "send":
            continue
        try:
            packet = parse_frame(base64.b64decode(message["data"]))
        except Exception:
            continue
        if packet.command == 1000:
            auth_frame = base64.b64decode(message["data"])
            auth_index = index
            break

    if auth_frame is None:
        raise HarFormatError("无法提取 WebSocket 登录帧")

    auth_time = float(messages[auth_index].get("time", 0.0))
    context_id: Optional[int] = None
    activity_id: Optional[int] = None
    init_frames: List[Tuple[float, bytes]] = []
    last_state: Optional[StarState] = None
    first_query_seen = False

    for message in messages[auth_index + 1 :]:
        message_type = message.get("type")
        try:
            frame = base64.b64decode(message["data"])
            packet = parse_frame(frame)
        except Exception:
            continue

        if message_type == "send":
            if packet.command == CMD_QUERY:
                first_query_seen = True
            elif not first_query_seen:
                offset = max(0.0, float(message.get("time", 0.0)) - auth_time)
                init_frames.append((offset, frame))
            if packet.command in (CMD_QUERY, CMD_UPGRADE):
                fields = field_map(packet.payload)
                possible_activity_id = first_value(fields, 1)
                if isinstance(possible_activity_id, int):
                    activity_id = possible_activity_id
                if packet.context_id is not None:
                    context_id = packet.context_id
        elif message_type == "receive" and packet.command == CMD_QUERY:
            try:
                state = StarState.from_payload(packet.payload)
            except ProtocolError:
                continue
            if activity_id is None:
                activity_id = state.activity_id
            last_state = state

    if context_id is None:
        raise HarFormatError("无法提取命令信封中的上下文 ID")
    if last_state is None:
        raise HarFormatError("没有找到命令 6589 的有效响应")
    if activity_id is None:
        activity_id = last_state.activity_id

    request = entry.get("request", {})
    headers = request.get("headers", [])
    origin = header_value(headers, "origin") or "https://p2ph5-gate.9917.com"
    user_agent = header_value(headers, "user-agent") or DEFAULT_USER_AGENT

    return HarSession(
        har_path=har_path,
        websocket_url=str(request["url"]),
        auth_frame=auth_frame,
        context_id=context_id,
        activity_id=activity_id,
        current_state=last_state,
        init_frames=init_frames,
        origin=origin,
        user_agent=user_agent,
        login_info_request=extract_http_request(har, "login/info"),
        login_do_request=extract_http_request(har, "login.do"),
    )


class PacketClient:
    def __init__(
        self,
        websocket: Any,
        context_id: int,
        activity_id: int,
        timeout: float,
    ) -> None:
        self.websocket = websocket
        self.context_id = context_id
        self.activity_id = activity_id
        self.timeout = timeout
        self.queues: Dict[int, asyncio.Queue[Optional[bytes]]] = {}
        self.reader_task: Optional[asyncio.Task[Any]] = None
        self.heartbeat_task: Optional[asyncio.Task[Any]] = None

    def start(self) -> None:
        self.reader_task = asyncio.create_task(self._reader())

    async def close(self) -> None:
        for task in (self.heartbeat_task, self.reader_task):
            if task is not None:
                task.cancel()
        if self.heartbeat_task is not None:
            await asyncio.gather(self.heartbeat_task, return_exceptions=True)
        if self.reader_task is not None:
            await asyncio.gather(self.reader_task, return_exceptions=True)

    async def send_raw(self, frame: bytes) -> None:
        await self.websocket.send(frame)

    async def _reader(self) -> None:
        try:
            async for raw_message in self.websocket:
                if not isinstance(raw_message, (bytes, bytearray)):
                    continue
                try:
                    packet = parse_frame(bytes(raw_message))
                except ProtocolError as exc:
                    print("[协议] 忽略无法解析的帧：%s" % exc, file=sys.stderr)
                    continue

                if packet.command == CMD_PUSH:
                    await self._ack_push(packet.payload)
                    continue

                queue = self.queues.get(packet.command)
                if queue is not None:
                    await queue.put(packet.payload)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            for queue in self.queues.values():
                await queue.put(None)
            print("[连接] WebSocket 读取结束：%s" % exc, file=sys.stderr)
        else:
            for queue in self.queues.values():
                await queue.put(None)

    async def _ack_push(self, payload: bytes) -> None:
        fields = field_map(payload)
        sequence = first_value(fields, 2)
        if isinstance(sequence, int):
            ack_payload = pb_uint(1, sequence)
            await self.send_raw(
                build_frame(CMD_ACK, ack_payload, context_id=self.context_id)
            )

    async def request(self, command: int, payload: bytes) -> bytes:
        queue: asyncio.Queue[Optional[bytes]] = asyncio.Queue()
        self.queues[command] = queue
        while not queue.empty():
            queue.get_nowait()
        await self.send_raw(
            build_frame(command, payload, context_id=self.context_id)
        )
        try:
            response = await asyncio.wait_for(queue.get(), timeout=self.timeout)
        except asyncio.TimeoutError as exc:
            raise TimeoutError(
                "命令 %d 在 %.1f 秒内没有响应" % (command, self.timeout)
            ) from exc
        if response is None:
            raise ConnectionError("等待命令 %d 时连接已结束" % command)
        return response

    async def authenticate(self, auth_frame: bytes) -> None:
        queue: asyncio.Queue[Optional[bytes]] = asyncio.Queue()
        self.queues[1000] = queue
        await self.send_raw(auth_frame)
        response = await asyncio.wait_for(queue.get(), timeout=self.timeout)
        if response is None:
            raise ConnectionError("WebSocket 登录失败")
        print("[登录] 命令 1000 登录成功")

    async def send_bootstrap(self, frames: Sequence[Tuple[float, bytes]]) -> None:
        if not frames:
            return
        heartbeat_frame: Optional[bytes] = None
        started_at = time.monotonic()
        print("[初始化] 回放登录后、首次查询前的 %d 个初始化帧" % len(frames))
        for offset, frame in frames:
            delay = offset - (time.monotonic() - started_at)
            if delay > 0:
                await asyncio.sleep(delay)
            await self.send_raw(frame)
            if heartbeat_frame is None:
                try:
                    if parse_frame(frame).command == 1004:
                        heartbeat_frame = frame
                except ProtocolError:
                    pass
        if heartbeat_frame is not None:
            self.heartbeat_task = asyncio.create_task(
                self._heartbeat(heartbeat_frame)
            )
        await asyncio.sleep(0.2)

    async def _heartbeat(self, frame: bytes) -> None:
        try:
            while True:
                await asyncio.sleep(15.0)
                await self.send_raw(frame)
        except asyncio.CancelledError:
            raise
        except Exception:
            return

    async def query(self) -> StarState:
        payload = pb_uint(1, self.activity_id)
        response = await self.request(CMD_QUERY, payload)
        return StarState.from_payload(response)

    async def upgrade(self, mode: int) -> UpgradeResult:
        payload = pb_uint(1, self.activity_id) + pb_uint(2, mode)
        response = await self.request(CMD_UPGRADE, payload)
        if not response:
            return UpgradeResult(
                activity_id=self.activity_id,
                mode=mode,
                roll_result=0,
                result_code=0,
                stars_gained=0,
                success=False,
                empty_response=True,
            )
        return UpgradeResult.from_payload(response)

    async def claim(self) -> str:
        payload = pb_uint(1, self.activity_id)
        response = await self.request(CMD_CLAIM, payload)
        fields = field_map(response)
        reward = first_value(fields, 2, b"")
        if isinstance(reward, bytes):
            return reward.decode("utf-8", "replace")
        return str(reward)

    async def buy(self, quantity: int, item_type: int) -> Dict[int, List[Any]]:
        payload = (
            pb_uint(1, self.activity_id)
            + pb_uint(2, quantity)
            + pb_uint(3, item_type)
        )
        response = await self.request(CMD_BUY, payload)
        if not response:
            raise ProtocolError("命令 1163 返回空响应，购买未生效")
        return field_map(response)


def print_state(state: StarState, prefix: str = "[状态]") -> None:
    print(
        "%s 星象=%d 星级=%d 保护进度=%d 未知4=%s 未知6=%d"
        % (
            prefix,
            state.astrology_result,
            state.star_level,
            state.protection_progress,
            state.unknown_4,
            state.unknown_6,
        )
    )


def choose_auto_buy_type(
    _astrology_result: int, protect_override: int
) -> int:
    if protect_override > 0:
        return protect_override
    # 类型 999 在完整 0→7 星样本中一次购买即可覆盖整轮消耗。
    return BUY_UNIVERSAL_ITEM


async def auto_upgrade(args: argparse.Namespace, session: HarSession) -> int:
    if args.live:
        try:
            import websockets
        except ImportError:
            print(
                "缺少依赖 websockets，请执行：pip install -r requirements.txt",
                file=sys.stderr,
            )
            return 2
    else:
        raise RuntimeError("auto_upgrade 只应在 --live 模式下调用")

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

            state = await client.query()
            print_state(state, "[初始]")
            if state.activity_id != session.activity_id:
                raise ProtocolError("响应中的活动 ID 与请求不一致")

            if args.pre_buy > 0:
                if not args.allow_purchase:
                    raise RuntimeError(
                        "指定了 --pre-buy，但没有 --allow-purchase；"
                        "为避免意外消费，已停止"
                    )
                print(
                    "[购买] 警告：即将购买 %d 个单位，item_type=%d，"
                    "可能消耗游戏货币" % (args.pre_buy, args.buy_type)
                )
                buy_fields = await client.buy(args.pre_buy, args.buy_type)
                product = first_value(buy_fields, 4, b"")
                if isinstance(product, bytes):
                    product = product.decode("utf-8", "replace")
                print("[购买] 成功，商品=%s" % product)

            attempts = 0
            failures = 0
            empty_responses = 0
            auto_buys = 0
            auto_bought_types = set()
            while state.star_level < 7:
                if attempts >= args.max_attempts:
                    raise RuntimeError(
                        "达到最大尝试次数 %d，当前仍为 %d 星"
                        % (args.max_attempts, state.star_level)
                    )
                await asyncio.sleep(args.delay)
                attempts += 1
                previous_level = state.star_level
                mode = MODE_NORMAL if previous_level == 0 else MODE_PROTECTED
                mode_name = "占星/普通升星" if mode == MODE_NORMAL else "保护升星"
                print(
                    "[操作] %s（mode=%d，第 %d 次，当前 %d 星）"
                    % (mode_name, mode, attempts, previous_level)
                )
                result = await client.upgrade(mode)
                print(
                    "[结果] roll=%d result_code=%s stars_gained=%d "
                    "success=%s empty_response=%s"
                    % (
                        result.roll_result,
                        result.result_code,
                        result.stars_gained,
                        result.success,
                        result.empty_response,
                    )
                )
                state = await client.query()
                print_state(state)

                if state.star_level > previous_level:
                    failures = 0
                    empty_responses = 0
                    auto_buys = 0
                    auto_bought_types.clear()
                else:
                    failures += 1
                    print("[进度] 本次未升星，连续失败 %d 次" % failures)
                    if result.empty_response:
                        buy_type = (
                            args.draw_buy_type
                            if previous_level == 0
                            else choose_auto_buy_type(
                                state.astrology_result,
                                args.protect_buy_type,
                            )
                        )
                        if (
                            args.auto_buy
                            and auto_buys < args.max_auto_buys
                            and buy_type not in auto_bought_types
                        ):
                            if not args.allow_purchase:
                                raise RuntimeError(
                                    "--auto-buy 需要同时提供 "
                                    "--allow-purchase"
                                )
                            print(
                                "[购买] 检测到空响应，购买 %d 个类型 %d 道具"
                                % (args.auto_buy_qty, buy_type)
                            )
                            buy_fields = await client.buy(
                                args.auto_buy_qty,
                                buy_type,
                            )
                            product = first_value(buy_fields, 4, b"")
                            if isinstance(product, bytes):
                                product = product.decode("utf-8", "replace")
                            print("[购买] 成功，商品=%s" % product)
                            auto_buys += 1
                            auto_bought_types.add(buy_type)
                            empty_responses = 0
                        else:
                            empty_responses += 1
                    else:
                        empty_responses = 0
                    if empty_responses >= args.max_empty_responses:
                        raise RuntimeError(
                            "连续 %d 次收到空业务响应，操作未生效。"
                            "通常表示登录会话、活动次数或道具不足；"
                            "请重新抓取 HAR 后重试"
                            % empty_responses
                        )

            print("[完成] 已升至 7 星")
            if args.claim:
                reward = await client.claim()
                print("[领奖] 成功，奖励=%s" % reward)
                await asyncio.sleep(args.delay)
                state = await client.query()
                print_state(state, "[领奖后]")
            return 0
        finally:
            await client.close()


def find_default_har() -> Path:
    candidates = sorted(
        Path(".").glob("*.har"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise FileNotFoundError("当前目录没有 .har 文件，请使用 --har 指定")
    return candidates[0]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="四艺星盘自动升至 7 星（默认仅离线检查）"
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help="account.json 与 task/ 所在目录，默认程序根目录",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="实际连接游戏服务器；不加此参数时只做离线检查",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="显式指定离线模式（默认行为）",
    )
    parser.add_argument(
        "--no-bootstrap",
        action="store_true",
        help="登录后不发送任务文件中保存的初始化帧",
    )
    parser.add_argument(
        "--reuse-session",
        action="store_true",
        help="直接复用任务文件的 WebSocket 登录帧，不重新执行 HTTP 登录",
    )
    parser.add_argument(
        "--claim",
        action="store_true",
        help="达到 7 星后自动领奖",
    )
    parser.add_argument(
        "--pre-buy",
        type=int,
        default=0,
        metavar="COUNT",
        help="开始前购买 COUNT 个保护道具；默认 0，且需要 --allow-purchase",
    )
    parser.add_argument(
        "--buy-type",
        type=int,
        default=BUY_UNIVERSAL_ITEM,
        help="--pre-buy 使用的通用购买类型，默认 999",
    )
    parser.add_argument(
        "--draw-buy-type",
        type=int,
        default=BUY_UNIVERSAL_ITEM,
        help="0 星缺少占星材料时自动购买的类型，默认 999",
    )
    parser.add_argument(
        "--protect-buy-type",
        type=int,
        default=0,
        help="强制指定自动购买保护道具的 item_type；"
        "0 表示使用通用类型 999",
    )
    parser.add_argument(
        "--allow-purchase",
        action="store_true",
        help="允许实际购买，可能消耗游戏货币",
    )
    parser.add_argument(
        "--auto-buy",
        action="store_true",
        help="遇到空响应时尝试购买对应道具；需要 --allow-purchase",
    )
    parser.add_argument(
        "--auto-buy-qty",
        type=int,
        default=18,
        help="自动购买数量，默认 18",
    )
    parser.add_argument(
        "--max-auto-buys",
        type=int,
        default=2,
        help="自动购买最多执行次数，默认 2",
    )
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=100,
        help="最多升星请求次数，默认 100",
    )
    parser.add_argument(
        "--max-empty-responses",
        type=int,
        default=5,
        help="连续空业务响应达到该次数后停止，默认 5",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.8,
        help="每次操作后的等待秒数，默认 0.8",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="单次 WebSocket 响应超时，默认 10 秒",
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
    if args.max_attempts <= 0:
        parser.error("--max-attempts 必须大于 0")
    if args.max_empty_responses <= 0:
        parser.error("--max-empty-responses 必须大于 0")
    if args.pre_buy < 0:
        parser.error("--pre-buy 不能为负数")
    if args.auto_buy_qty <= 0 or args.max_auto_buys <= 0:
        parser.error("--auto-buy-qty 和 --max-auto-buys 必须大于 0")
    if args.protect_buy_type < 0 or args.draw_buy_type <= 0:
        parser.error("--protect-buy-type 不能为负数，--draw-buy-type 必须大于 0")
    if args.delay < 0 or args.timeout <= 0:
        parser.error("--delay 必须非负，--timeout 必须大于 0")
    if args.ca_file is not None and not args.ca_file.exists():
        parser.error("--ca-file 不存在：%s" % args.ca_file)
    if (
        args.live
        and (args.pre_buy > 0 or args.auto_buy)
        and not args.allow_purchase
    ):
        parser.error("--pre-buy/--auto-buy 需要同时提供 --allow-purchase")

    from gt_automation.session_store import (
        SessionFileError,
        TASK_STAR,
        load_gt_session,
    )
    try:
        session, metadata = load_gt_session(args.data_dir, task=TASK_STAR)
    except (
        OSError,
        SessionFileError,
        HarFormatError,
        ProtocolError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        print("账号或星盘任务文件读取失败：%s" % exc, file=sys.stderr)
        return 2
    if not metadata.star_ready:
        print(
            "[失败] 缺少四艺星盘任务数据，无法运行升星。"
            "请重新导入包含命令 6589 的当前版本 HAR。",
            file=sys.stderr,
        )
        return 2

    print("[数据目录] %s" % args.data_dir)
    print("[会话] %s" % session.websocket_url)
    print("[活动] activity_id=%d" % session.activity_id)
    print_state(session.current_state, "[任务文件最后状态]")
    print(
        "[计划] 0 星先执行 mode=1，之后反复执行 mode=2，"
        "直到查询结果为 7 星"
    )
    if args.pre_buy:
        print(
            "[计划] 开始前购买 %d 个保护道具，类型=%d"
            % (args.pre_buy, args.buy_type)
        )
    if args.auto_buy:
        if args.protect_buy_type > 0:
            protect_plan = "保护材料固定类型 %d" % args.protect_buy_type
        else:
            protect_plan = "保护材料使用通用类型 999"
        print(
            "[计划] 空响应时自动购买：0 星买类型 %d，%s，每次 %d 个"
            % (
                args.draw_buy_type,
                protect_plan,
                args.auto_buy_qty,
            )
        )
    if args.claim:
        print("[计划] 达到 7 星后执行命令 6591 领奖")

    if not args.live:
        print("[离线] 未连接服务器。确认风险后添加 --live 实际执行。")
        return 0

    try:
        return asyncio.run(auto_upgrade(args, session))
    except KeyboardInterrupt:
        print("\n[停止] 用户中断", file=sys.stderr)
        return 130
    except Exception as exc:
        message = format_user_error(exc)
        if "Bye" in message:
            print(
                "[失败] 游戏会话被同一账号的另一个客户端替换。"
                "请完全退出微信小游戏并等待数秒后重试；"
                "如果登录令牌已过期，请重新导入当前版本 HAR。",
                file=sys.stderr,
            )
        else:
            print("[失败] %s" % message, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
