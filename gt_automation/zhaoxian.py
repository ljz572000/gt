"""Zhaoxian task configuration and response parsing."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from gt_automation.paths import DEFAULT_DATA_DIR, ZHAOXIAN_FILE
from gt_automation.session_store import (
    deserialize_session,
    serialize_session,
)


ZHAOXIAN_FORMAT = "gt-zhaoxian"
ZHAOXIAN_VERSION = 1
ZHAOXIAN_ACTIVITY_ID = 506
CMD_ZHAOXIAN_DRAW = 1627
CMD_ACTIVITY_QUERY = 5093
DEFAULT_DRAW_PAYLOAD = bytes.fromhex("180210020800")


class ZhaoxianError(RuntimeError):
    """Raised when zhaoxian.json or HAR data is invalid."""


@dataclass
class ZhaoxianResult:
    results: List[str] = field(default_factory=list)
    field2: Optional[int] = None
    field3: Optional[int] = None
    item_change: str = ""
    flag: str = ""


@dataclass
class ZhaoxianConfig:
    source_har: str
    session: Any
    account: Dict[str, Any] = field(default_factory=dict)
    activity_id: int = ZHAOXIAN_ACTIVITY_ID
    draw_payload: bytes = DEFAULT_DRAW_PAYLOAD
    last_results: List[str] = field(default_factory=list)
    last_result_meta: Dict[str, Any] = field(default_factory=dict)
    updated_at: str = ""


def zhaoxian_file(base_dir: Path = DEFAULT_DATA_DIR) -> Path:
    return Path(base_dir) / ZHAOXIAN_FILE.name


def parse_zhaoxian_result(payload: bytes) -> ZhaoxianResult:
    from siyi_star_auto import field_map

    fields = field_map(payload)
    raw_results = fields.get(1, [b""])[0]
    if isinstance(raw_results, bytes):
        result_text = raw_results.decode("utf-8", "replace")
    else:
        result_text = str(raw_results)
    results = [item for item in result_text.split("&") if item]
    field2 = fields.get(2, [None])[0]
    field3 = fields.get(3, [None])[0]
    item_change = fields.get(5, [b""])[0]
    flag = fields.get(6, [b""])[0]
    if isinstance(item_change, bytes):
        item_change = item_change.decode("utf-8", "replace")
    if isinstance(flag, bytes):
        flag = flag.decode("utf-8", "replace")
    return ZhaoxianResult(
        results=results,
        field2=int(field2) if isinstance(field2, int) else None,
        field3=int(field3) if isinstance(field3, int) else None,
        item_change=str(item_change),
        flag=str(flag),
    )


def extract_zhaoxian_config(
    har_path: Path,
    session: Any,
    account: Optional[Dict[str, Any]] = None,
) -> Optional[ZhaoxianConfig]:
    """Return config only when the HAR contains a command-1627 draw."""
    from siyi_star_auto import field_map, parse_frame

    har_path = Path(har_path)
    try:
        har = json.loads(har_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    draw_payload: Optional[bytes] = None
    activity_id = ZHAOXIAN_ACTIVITY_ID
    last_result: Optional[ZhaoxianResult] = None
    for entry in har.get("log", {}).get("entries", []):
        for message in entry.get("_webSocketMessages") or []:
            try:
                packet = parse_frame(base64.b64decode(message.get("data", "")))
            except Exception:
                continue
            if message.get("type") != "send":
                continue
            if packet.command == CMD_ZHAOXIAN_DRAW:
                draw_payload = packet.payload
            elif packet.command == CMD_ACTIVITY_QUERY:
                fields = field_map(packet.payload)
                query_id = fields.get(1, [None])[0]
                if query_id == ZHAOXIAN_ACTIVITY_ID:
                    activity_id = ZHAOXIAN_ACTIVITY_ID

    if draw_payload is None:
        return None

    for entry in har.get("log", {}).get("entries", []):
        for message in entry.get("_webSocketMessages") or []:
            if message.get("type") != "receive":
                continue
            try:
                packet = parse_frame(base64.b64decode(message.get("data", "")))
            except Exception:
                continue
            if packet.command == CMD_ZHAOXIAN_DRAW:
                parsed = parse_zhaoxian_result(packet.payload)
                if parsed.results:
                    last_result = parsed

    last_meta: Dict[str, Any] = {}
    if last_result is not None:
        last_meta = {
            "field2": last_result.field2,
            "field3": last_result.field3,
            "item_change": last_result.item_change,
            "flag": last_result.flag,
        }
    return ZhaoxianConfig(
        source_har=str(har_path),
        session=session,
        account=account or {},
        activity_id=activity_id,
        draw_payload=draw_payload,
        last_results=last_result.results if last_result else [],
        last_result_meta=last_meta,
        updated_at=datetime.now(timezone.utc).isoformat(),
    )


def save_zhaoxian_config(
    config: ZhaoxianConfig,
    base_dir: Path = DEFAULT_DATA_DIR,
) -> None:
    path = zhaoxian_file(base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "format": ZHAOXIAN_FORMAT,
        "version": ZHAOXIAN_VERSION,
        "source_har": config.source_har,
        "updated_at": config.updated_at
        or datetime.now(timezone.utc).isoformat(),
        "account": config.account,
        "activity_id": config.activity_id,
        "draw_payload_base64": base64.b64encode(
            config.draw_payload
        ).decode("ascii"),
        "last_results": config.last_results,
        "last_result_meta": config.last_result_meta,
        "session": serialize_session(config.session),
    }
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.chmod(0o600)
    temporary.replace(path)
    path.chmod(0o600)


def load_zhaoxian_config(
    base_dir: Path = DEFAULT_DATA_DIR,
) -> ZhaoxianConfig:
    path = zhaoxian_file(base_dir)
    if not path.exists():
        raise ZhaoxianError("zhaoxian.json 不存在")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ZhaoxianError("zhaoxian.json 不是有效的 JSON") from exc
    if (
        payload.get("format") != ZHAOXIAN_FORMAT
        or payload.get("version") != ZHAOXIAN_VERSION
    ):
        raise ZhaoxianError("zhaoxian.json 格式或版本不受支持")
    session_payload = payload.get("session")
    if not isinstance(session_payload, dict):
        raise ZhaoxianError("zhaoxian.json 缺少 session")
    try:
        draw_payload = base64.b64decode(
            payload.get("draw_payload_base64", ""),
            validate=True,
        )
    except (ValueError, TypeError) as exc:
        raise ZhaoxianError("zhaoxian.json 抽卡 payload 无效") from exc
    account = payload.get("account")
    return ZhaoxianConfig(
        source_har=str(payload.get("source_har", "")),
        session=deserialize_session(
            session_payload,
            str(payload.get("source_har", "")),
        ),
        account=account if isinstance(account, dict) else {},
        activity_id=int(payload.get("activity_id") or ZHAOXIAN_ACTIVITY_ID),
        draw_payload=draw_payload or DEFAULT_DRAW_PAYLOAD,
        last_results=[
            str(item)
            for item in payload.get("last_results", [])
            if isinstance(item, str)
        ],
        last_result_meta=(
            payload.get("last_result_meta")
            if isinstance(payload.get("last_result_meta"), dict)
            else {}
        ),
        updated_at=str(payload.get("updated_at", "")),
    )


def update_zhaoxian_results(
    config: ZhaoxianConfig,
    result: ZhaoxianResult,
    base_dir: Path = DEFAULT_DATA_DIR,
) -> None:
    config.last_results = list(result.results)
    config.last_result_meta = {
        "field2": result.field2,
        "field3": result.field3,
        "item_change": result.item_change,
        "flag": result.flag,
    }
    config.updated_at = datetime.now(timezone.utc).isoformat()
    save_zhaoxian_config(config, base_dir)
