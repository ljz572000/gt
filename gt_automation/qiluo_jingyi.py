"""Qiluo Jingyi direct-purchase configuration and result parsing."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from gt_automation.paths import DEFAULT_DATA_DIR, QILUO_JINGYI_FILE
from gt_automation.session_store import (
    deserialize_session,
    serialize_session,
)


QILUO_FORMAT = "gt-qiluo-jingyi"
QILUO_VERSION = 1
QILUO_ACTIVITY_ID = 1596413
QILUO_PRODUCT_ID = 17613694
CMD_QILUO_QUERY = 1173
CMD_QILUO_PURCHASE = 5515
CMD_QILUO_RESULT = 3372
DEFAULT_PURCHASE_PAYLOAD = bytes.fromhex(
    "08fdb76110fe86b308"
)


class QiluoJingyiError(RuntimeError):
    """Raised when qiluo_jingyi.json or HAR data is invalid."""


@dataclass
class QiluoJingyiResult:
    activity_id: int
    product_id: int
    status: Optional[int]
    rewards: List[str] = field(default_factory=list)


@dataclass
class QiluoJingyiConfig:
    source_har: str
    session: Any
    account: Dict[str, Any] = field(default_factory=dict)
    activity_id: int = QILUO_ACTIVITY_ID
    product_id: int = QILUO_PRODUCT_ID
    purchase_payload: bytes = DEFAULT_PURCHASE_PAYLOAD
    last_rewards: List[str] = field(default_factory=list)
    last_status: Optional[int] = None
    updated_at: str = ""


def qiluo_jingyi_file(base_dir: Path = DEFAULT_DATA_DIR) -> Path:
    return Path(base_dir) / QILUO_JINGYI_FILE.name


def parse_qiluo_jingyi_result(payload: bytes) -> QiluoJingyiResult:
    from siyi_star_auto import field_map

    fields = field_map(payload)
    activity_id = fields.get(1, [0])[0]
    product_id = fields.get(2, [0])[0]
    status = fields.get(3, [None])[0]
    raw_rewards = fields.get(4, [b""])[0]
    if isinstance(raw_rewards, bytes):
        reward_text = raw_rewards.decode("utf-8", "replace")
    else:
        reward_text = str(raw_rewards)
    rewards = [item for item in reward_text.split("&") if item]
    return QiluoJingyiResult(
        activity_id=int(activity_id) if isinstance(activity_id, int) else 0,
        product_id=int(product_id) if isinstance(product_id, int) else 0,
        status=int(status) if isinstance(status, int) else None,
        rewards=rewards,
    )


def extract_qiluo_jingyi_config(
    har_path: Path,
    session: Any,
    account: Optional[Dict[str, Any]] = None,
) -> Optional[QiluoJingyiConfig]:
    """Return config only when the HAR contains purchase command 5515."""
    from siyi_star_auto import field_map, parse_frame

    har_path = Path(har_path)
    try:
        har = json.loads(har_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    purchase_payload: Optional[bytes] = None
    activity_id = QILUO_ACTIVITY_ID
    product_id = QILUO_PRODUCT_ID
    for entry in har.get("log", {}).get("entries", []):
        for message in entry.get("_webSocketMessages") or []:
            if message.get("type") != "send":
                continue
            try:
                packet = parse_frame(base64.b64decode(message.get("data", "")))
            except Exception:
                continue
            if packet.command != CMD_QILUO_PURCHASE:
                continue
            fields = field_map(packet.payload)
            candidate_activity = fields.get(1, [None])[0]
            candidate_product = fields.get(2, [None])[0]
            if isinstance(candidate_activity, int):
                activity_id = candidate_activity
            if isinstance(candidate_product, int):
                product_id = candidate_product
            purchase_payload = packet.payload

    if purchase_payload is None:
        return None

    last_status: Optional[int] = None
    last_rewards: List[str] = []
    for entry in har.get("log", {}).get("entries", []):
        for message in entry.get("_webSocketMessages") or []:
            if message.get("type") != "receive":
                continue
            try:
                packet = parse_frame(base64.b64decode(message.get("data", "")))
            except Exception:
                continue
            if packet.command != CMD_QILUO_RESULT:
                continue
            parsed = parse_qiluo_jingyi_result(packet.payload)
            if parsed.status is not None or parsed.rewards:
                last_status = parsed.status
                last_rewards = parsed.rewards

    return QiluoJingyiConfig(
        source_har=str(har_path),
        session=session,
        account=account or {},
        activity_id=activity_id,
        product_id=product_id,
        purchase_payload=purchase_payload,
        last_rewards=last_rewards,
        last_status=last_status,
        updated_at=datetime.now(timezone.utc).isoformat(),
    )


def save_qiluo_jingyi_config(
    config: QiluoJingyiConfig,
    base_dir: Path = DEFAULT_DATA_DIR,
) -> None:
    path = qiluo_jingyi_file(base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "format": QILUO_FORMAT,
        "version": QILUO_VERSION,
        "source_har": config.source_har,
        "updated_at": config.updated_at
        or datetime.now(timezone.utc).isoformat(),
        "account": config.account,
        "activity_id": config.activity_id,
        "product_id": config.product_id,
        "purchase_payload_base64": base64.b64encode(
            config.purchase_payload
        ).decode("ascii"),
        "last_rewards": config.last_rewards,
        "last_status": config.last_status,
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


def load_qiluo_jingyi_config(
    base_dir: Path = DEFAULT_DATA_DIR,
) -> QiluoJingyiConfig:
    path = qiluo_jingyi_file(base_dir)
    if not path.exists():
        raise QiluoJingyiError("qiluo_jingyi.json 不存在")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise QiluoJingyiError(
            "qiluo_jingyi.json 不是有效的 JSON"
        ) from exc
    if (
        payload.get("format") != QILUO_FORMAT
        or payload.get("version") != QILUO_VERSION
    ):
        raise QiluoJingyiError("qiluo_jingyi.json 格式或版本不受支持")
    session_payload = payload.get("session")
    if not isinstance(session_payload, dict):
        raise QiluoJingyiError("qiluo_jingyi.json 缺少 session")
    try:
        purchase_payload = base64.b64decode(
            payload.get("purchase_payload_base64", ""),
            validate=True,
        )
    except (ValueError, TypeError) as exc:
        raise QiluoJingyiError(
            "qiluo_jingyi.json 购买 payload 无效"
        ) from exc
    account = payload.get("account")
    return QiluoJingyiConfig(
        source_har=str(payload.get("source_har", "")),
        session=deserialize_session(
            session_payload,
            str(payload.get("source_har", "")),
        ),
        account=account if isinstance(account, dict) else {},
        activity_id=int(payload.get("activity_id") or QILUO_ACTIVITY_ID),
        product_id=int(payload.get("product_id") or QILUO_PRODUCT_ID),
        purchase_payload=(
            purchase_payload or DEFAULT_PURCHASE_PAYLOAD
        ),
        last_rewards=[
            str(item)
            for item in payload.get("last_rewards", [])
            if isinstance(item, str)
        ],
        last_status=(
            int(payload["last_status"])
            if isinstance(payload.get("last_status"), int)
            else None
        ),
        updated_at=str(payload.get("updated_at", "")),
    )


def update_qiluo_jingyi_result(
    config: QiluoJingyiConfig,
    result: QiluoJingyiResult,
    base_dir: Path = DEFAULT_DATA_DIR,
) -> None:
    config.last_rewards = list(result.rewards)
    config.last_status = result.status
    config.activity_id = result.activity_id or config.activity_id
    config.product_id = result.product_id or config.product_id
    config.updated_at = datetime.now(timezone.utc).isoformat()
    save_qiluo_jingyi_config(config, base_dir)
