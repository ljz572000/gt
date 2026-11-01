"""Split account and task session persistence.

Runtime files:

    account.json
    task/daily_welfare.json
    task/siyi_star.json

Older gt.json files are migrated automatically when split files do not yet exist.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from gt_automation.paths import (
    ACCOUNT_FILE,
    DEFAULT_DATA_DIR,
    QILUO_JINGYI_FILE,
    TASK_DIR,
    ZHAOXIAN_FILE,
)


ACCOUNT_FORMAT = "gt-account"
ACCOUNT_VERSION = 1
TASK_FORMAT = "gt-task-session"
TASK_VERSION = 1

TASK_DAILY = "daily_welfare"
TASK_STAR = "siyi_star"
TASKS = {TASK_DAILY, TASK_STAR}
TASK_LABELS = {
    TASK_DAILY: "每日福利",
    TASK_STAR: "四艺星盘",
}
TASK_FILENAMES = {
    TASK_DAILY: "daily_welfare.json",
    TASK_STAR: "siyi_star.json",
}

LEGACY_GT_FORMAT = "gt-automation-session"
LEGACY_WELFARE_FORMAT = "9917-daily-welfare-session"


class SessionFileError(RuntimeError):
    """Raised when account or task files are missing or invalid."""


@dataclass
class SessionMetadata:
    source_har: str = ""
    created_at: str = ""
    daily_ready: bool = True
    star_ready: bool = False
    zhaoxian_ready: bool = False
    qiluo_jingyi_ready: bool = False
    account: Dict[str, Any] = field(default_factory=dict)
    last_reward: Optional[Dict[str, Any]] = None
    context_id: int = 0
    activity_id: int = 0
    init_frames: int = 0


@dataclass
class SessionImportResult:
    session: Any
    metadata: SessionMetadata
    zhaoxian_config: Any = None
    qiluo_jingyi_config: Any = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def account_file(base_dir: Path = DEFAULT_DATA_DIR) -> Path:
    return Path(base_dir) / ACCOUNT_FILE.name


def task_dir(base_dir: Path = DEFAULT_DATA_DIR) -> Path:
    return Path(base_dir) / TASK_DIR.name


def task_file(
    task: str,
    base_dir: Path = DEFAULT_DATA_DIR,
) -> Path:
    if task not in TASKS:
        raise ValueError("未知任务：%s" % task)
    return task_dir(base_dir) / TASK_FILENAMES[task]


def _b64decode(value: Any, field_name: str) -> bytes:
    if not isinstance(value, str):
        raise SessionFileError("%s 不是有效的 Base64 字符串" % field_name)
    try:
        return base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as exc:
        raise SessionFileError("%s 无法解码" % field_name) from exc


def _http_request_to_dict(spec: Any) -> Optional[Dict[str, Any]]:
    if spec is None:
        return None
    return {
        "url": spec.url,
        "headers": dict(spec.headers),
        "body_base64": base64.b64encode(spec.body).decode("ascii"),
    }


def _http_request_from_dict(value: Any, name: str) -> Any:
    from siyi_star_auto import HttpRequestSpec

    if not isinstance(value, dict):
        raise SessionFileError("任务文件缺少 %s" % name)
    url = value.get("url")
    headers = value.get("headers")
    if not isinstance(url, str) or not isinstance(headers, dict):
        raise SessionFileError("任务文件中的 %s 格式错误" % name)
    body = _b64decode(value.get("body_base64"), "%s.body" % name)
    return HttpRequestSpec(
        url=url,
        headers={str(key): str(val) for key, val in headers.items()},
        body=body,
    )


def _state_to_dict(state: Any) -> Dict[str, Any]:
    return {
        "activity_id": state.activity_id,
        "astrology_result": state.astrology_result,
        "star_level": state.star_level,
        "unknown_4": state.unknown_4,
        "protection_progress": state.protection_progress,
        "unknown_6": state.unknown_6,
    }


def _session_to_dict(session: Any) -> Dict[str, Any]:
    return {
        "websocket_url": session.websocket_url,
        "auth_frame_base64": base64.b64encode(session.auth_frame).decode("ascii"),
        "context_id": session.context_id,
        "activity_id": session.activity_id,
        "origin": session.origin,
        "user_agent": session.user_agent,
        "current_state": _state_to_dict(session.current_state),
        "init_frames": [
            [offset, base64.b64encode(frame).decode("ascii")]
            for offset, frame in session.init_frames
        ],
        "login_info_request": _http_request_to_dict(session.login_info_request),
        "login_do_request": _http_request_to_dict(session.login_do_request),
    }


def serialize_session(session: Any) -> Dict[str, Any]:
    return _session_to_dict(session)


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.chmod(0o600)
    temporary.replace(path)
    path.chmod(0o600)


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise SessionFileError("文件不存在：%s" % path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SessionFileError("%s 不是有效的 JSON" % path.name) from exc
    if not isinstance(payload, dict):
        raise SessionFileError("%s 顶层必须是对象" % path.name)
    return payload


def _account_payload(account: Dict[str, Any], created_at: str = "") -> Dict[str, Any]:
    now = _now()
    return {
        "format": ACCOUNT_FORMAT,
        "version": ACCOUNT_VERSION,
        "created_at": created_at or now,
        "updated_at": now,
        "account": account,
    }


def _task_payload(
    session: Any,
    metadata: SessionMetadata,
    task: str,
    account: Dict[str, Any],
) -> Dict[str, Any]:
    payload = {
        "format": TASK_FORMAT,
        "version": TASK_VERSION,
        "task": task,
        "source_har": metadata.source_har or str(session.har_path),
        "updated_at": _now(),
        "account": account,
        "session": _session_to_dict(session),
    }
    if task == TASK_DAILY:
        payload["last_reward"] = metadata.last_reward
    return payload


def save_account(
    metadata: SessionMetadata,
    base_dir: Path = DEFAULT_DATA_DIR,
) -> None:
    path = account_file(base_dir)
    created_at = metadata.created_at
    if path.exists():
        try:
            created_at = str(_read_json(path).get("created_at") or created_at)
        except SessionFileError:
            pass
    _write_json(path, _account_payload(metadata.account, created_at))


def save_task_session(
    session: Any,
    base_dir: Path = DEFAULT_DATA_DIR,
    metadata: Optional[SessionMetadata] = None,
    task: str = TASK_DAILY,
) -> None:
    if task not in TASKS:
        raise ValueError("未知任务：%s" % task)
    if metadata is None:
        metadata = SessionMetadata(
            source_har=str(session.har_path),
            daily_ready=task == TASK_DAILY,
            star_ready=task == TASK_STAR,
        )
    account = metadata.account
    if not account:
        try:
            account = load_account(base_dir)
        except SessionFileError:
            account = {}
    _write_json(
        task_file(task, base_dir),
        _task_payload(session, metadata, task, account),
    )


def load_account(base_dir: Path = DEFAULT_DATA_DIR) -> Dict[str, Any]:
    path = account_file(base_dir)
    if not path.exists():
        return {}
    payload = _read_json(path)
    if payload.get("format") != ACCOUNT_FORMAT:
        raise SessionFileError("account.json 格式不正确")
    account = payload.get("account")
    return account if isinstance(account, dict) else {}


def _load_task_payload(
    task: str,
    base_dir: Path = DEFAULT_DATA_DIR,
) -> Dict[str, Any]:
    path = task_file(task, base_dir)
    payload = _read_json(path)
    if payload.get("format") != TASK_FORMAT:
        raise SessionFileError("%s 格式不正确" % path.name)
    if payload.get("task") != task:
        raise SessionFileError(
            "%s 中的任务类型不匹配" % path.name
        )
    return payload


def _session_from_task(payload: Dict[str, Any]) -> Any:
    from siyi_star_auto import HarSession, StarState

    session_payload = payload.get("session")
    if not isinstance(session_payload, dict):
        raise SessionFileError("任务文件缺少 session")
    auth_frame = _b64decode(
        session_payload.get("auth_frame_base64"),
        "auth_frame",
    )
    raw_frames = session_payload.get("init_frames")
    if not isinstance(raw_frames, list):
        raise SessionFileError("任务文件中的 init_frames 格式错误")
    init_frames = []
    for item in raw_frames:
        if (
            not isinstance(item, list)
            or len(item) != 2
            or not isinstance(item[0], (int, float))
        ):
            raise SessionFileError("任务文件中的初始化帧格式错误")
        init_frames.append((float(item[0]), _b64decode(item[1], "init_frame")))

    context_id = session_payload.get("context_id")
    activity_id = session_payload.get("activity_id", 0)
    websocket_url = session_payload.get("websocket_url")
    origin = session_payload.get("origin")
    user_agent = session_payload.get("user_agent")
    if not isinstance(context_id, int):
        raise SessionFileError("任务文件中的 context_id 格式错误")
    if not isinstance(activity_id, int):
        activity_id = 0
    if not isinstance(websocket_url, str) or not isinstance(origin, str):
        raise SessionFileError("任务文件中的 WebSocket 参数格式错误")
    if not isinstance(user_agent, str):
        raise SessionFileError("任务文件中的 User-Agent 格式错误")

    raw_state = session_payload.get("current_state")
    if isinstance(raw_state, dict):
        state = StarState(
            activity_id=int(raw_state.get("activity_id") or activity_id),
            astrology_result=int(raw_state.get("astrology_result") or 0),
            star_level=int(raw_state.get("star_level") or 0),
            unknown_4=raw_state.get("unknown_4"),
            protection_progress=int(raw_state.get("protection_progress") or 0),
            unknown_6=int(raw_state.get("unknown_6") or 0),
        )
    else:
        state = StarState(
            activity_id=activity_id,
            astrology_result=0,
            star_level=0,
            unknown_4=None,
            protection_progress=0,
            unknown_6=0,
        )
    return HarSession(
        har_path=Path(str(payload.get("source_har") or "task-session")),
        websocket_url=websocket_url,
        auth_frame=auth_frame,
        context_id=context_id,
        activity_id=activity_id,
        current_state=state,
        init_frames=init_frames,
        origin=origin,
        user_agent=user_agent,
        login_info_request=_http_request_from_dict(
            session_payload.get("login_info_request"),
            "login_info_request",
        ),
        login_do_request=_http_request_from_dict(
            session_payload.get("login_do_request"),
            "login_do_request",
        ),
    )


def deserialize_session(
    session_payload: Dict[str, Any],
    source_har: str = "",
) -> Any:
    return _session_from_task(
        {
            "source_har": source_har,
            "session": session_payload,
        }
    )


def load_gt_metadata(base_dir: Path = DEFAULT_DATA_DIR) -> SessionMetadata:
    ensure_data_files(Path(base_dir))
    account = load_account(base_dir)
    daily_payload: Optional[Dict[str, Any]] = None
    star_payload: Optional[Dict[str, Any]] = None
    try:
        daily_payload = _load_task_payload(TASK_DAILY, base_dir)
    except SessionFileError:
        pass
    try:
        star_payload = _load_task_payload(TASK_STAR, base_dir)
    except SessionFileError:
        pass
    if not account:
        account = (
            daily_payload.get("account")
            if daily_payload
            else star_payload.get("account")
            if star_payload
            else {}
        )
        account = account if isinstance(account, dict) else {}

    selected = daily_payload or star_payload or {}
    session_payload = selected.get("session")
    session_payload = session_payload if isinstance(session_payload, dict) else {}
    star_session = (star_payload or {}).get("session")
    star_session = star_session if isinstance(star_session, dict) else {}
    init_frames = session_payload.get("init_frames")
    last_reward = daily_payload.get("last_reward") if daily_payload else None
    source_har = str(
        (star_payload or {}).get("source_har")
        or selected.get("source_har")
        or ""
    )
    return SessionMetadata(
        source_har=source_har,
        created_at=str(selected.get("updated_at", "")),
        daily_ready=daily_payload is not None,
        star_ready=star_payload is not None,
        zhaoxian_ready=(Path(base_dir) / ZHAOXIAN_FILE.name).exists(),
        qiluo_jingyi_ready=(
            Path(base_dir) / QILUO_JINGYI_FILE.name
        ).exists(),
        account=account,
        last_reward=last_reward if isinstance(last_reward, dict) else None,
        context_id=int(session_payload.get("context_id") or 0),
        activity_id=int(
            star_session.get("activity_id")
            or session_payload.get("activity_id")
            or 0
        ),
        init_frames=len(init_frames) if isinstance(init_frames, list) else 0,
    )


def load_gt_session(
    base_dir: Path = DEFAULT_DATA_DIR,
    task: str = TASK_DAILY,
) -> Tuple[Any, SessionMetadata]:
    if task not in TASKS:
        raise ValueError("未知任务：%s" % task)
    ensure_data_files(Path(base_dir))
    try:
        payload = _load_task_payload(task, base_dir)
    except SessionFileError as exc:
        raise SessionFileError(
            "没有%s任务配置文件：%s"
            % (TASK_LABELS[task], task_file(task, base_dir))
        ) from exc
    return _session_from_task(payload), load_gt_metadata(base_dir)


def _legacy_to_v3(payload: Dict[str, Any]) -> Dict[str, Any]:
    if payload.get("version") == 3 and isinstance(payload.get("tasks"), dict):
        return payload
    if payload.get("format") not in {
        LEGACY_GT_FORMAT,
        LEGACY_WELFARE_FORMAT,
    }:
        raise SessionFileError("旧 gt.json 格式不受支持")
    capabilities = payload.get("capabilities")
    capabilities = capabilities if isinstance(capabilities, dict) else {}
    flat_session = {
        key: payload.get(key)
        for key in (
            "websocket_url",
            "auth_frame_base64",
            "context_id",
            "activity_id",
            "origin",
            "user_agent",
            "current_state",
            "init_frames",
            "login_info_request",
            "login_do_request",
        )
    }
    account = payload.get("account")
    account = account if isinstance(account, dict) else {}
    tasks: Dict[str, Any] = {
        TASK_DAILY: {
            "source_har": str(payload.get("source_har", "")),
            "updated_at": str(payload.get("created_at", "")) or _now(),
            "account": account,
            "last_reward": payload.get("last_reward"),
            "session": flat_session,
        }
    }
    if capabilities.get("siyi_star"):
        tasks[TASK_STAR] = {
            "source_har": str(payload.get("source_har", "")),
            "updated_at": str(payload.get("created_at", "")) or _now(),
            "account": account,
            "session": flat_session,
        }
    return {
        "account": account,
        "tasks": tasks,
    }


def migrate_legacy_gt(
    base_dir: Path = DEFAULT_DATA_DIR,
    legacy_path: Optional[Path] = None,
) -> bool:
    """Migrate old gt.json when no split files exist yet."""
    if account_file(base_dir).exists() or any(
        task_file(task, base_dir).exists() for task in TASKS
    ):
        return False
    if legacy_path is None:
        legacy_path = Path(base_dir) / "gt.json"
    if not legacy_path.exists():
        return False
    legacy_payload = _read_json(legacy_path)
    migrated = _legacy_to_v3(legacy_payload)
    account = migrated.get("account")
    account = account if isinstance(account, dict) else {}
    _write_json(
        account_file(base_dir),
        _account_payload(account),
    )
    for task, payload in migrated["tasks"].items():
        if task not in TASKS or not isinstance(payload, dict):
            continue
        session_payload = payload.get("session")
        if not isinstance(session_payload, dict):
            continue
        task_payload = {
            "format": TASK_FORMAT,
            "version": TASK_VERSION,
            "task": task,
            "source_har": str(payload.get("source_har", "")),
            "updated_at": str(payload.get("updated_at", "")) or _now(),
            "account": account,
            "session": session_payload,
        }
        if task == TASK_DAILY:
            task_payload["last_reward"] = payload.get("last_reward")
        _write_json(task_file(task, base_dir), task_payload)
    return True


def ensure_data_files(base_dir: Path = DEFAULT_DATA_DIR) -> None:
    migrate_legacy_gt(base_dir)


def save_import(
    session: Any,
    base_dir: Path = DEFAULT_DATA_DIR,
    metadata: Optional[SessionMetadata] = None,
    replace: bool = True,
) -> SessionMetadata:
    if metadata is None:
        metadata = SessionMetadata(
            source_har=str(session.har_path),
            daily_ready=True,
            star_ready=session.activity_id > 0,
        )
    if replace:
        save_account(metadata, base_dir)
        existing_tasks = set(TASKS)
        requested_tasks = set()
        if metadata.daily_ready:
            requested_tasks.add(TASK_DAILY)
            save_task_session(session, base_dir, metadata, TASK_DAILY)
        if metadata.star_ready:
            requested_tasks.add(TASK_STAR)
            save_task_session(session, base_dir, metadata, TASK_STAR)
        for task in existing_tasks - requested_tasks:
            task_file(task, base_dir).unlink(missing_ok=True)
    else:
        if not account_file(base_dir).exists():
            save_account(metadata, base_dir)
        if metadata.daily_ready:
            save_task_session(session, base_dir, metadata, TASK_DAILY)
        if metadata.star_ready:
            save_task_session(session, base_dir, metadata, TASK_STAR)
    return load_gt_metadata(base_dir)


def import_detected_tasks(
    session: Any,
    base_dir: Path = DEFAULT_DATA_DIR,
    metadata: Optional[SessionMetadata] = None,
) -> Tuple[SessionMetadata, list[str]]:
    """Import detected tasks without deleting or blocking existing ones."""
    ensure_data_files(base_dir)
    if metadata is None:
        metadata = SessionMetadata(
            source_har=str(session.har_path),
            daily_ready=True,
            star_ready=session.activity_id > 0,
        )
    existing = load_gt_metadata(base_dir)
    if not accounts_match(existing.account, metadata.account):
        raise SessionFileError("新 HAR 与现有账号或角色不一致")
    imported: list[str] = []

    # A star/zhaoxian HAR is normally captured to add that task. Preserve an
    # existing daily task snapshot in that case.
    preserve_existing_daily = (
        metadata.star_ready or metadata.zhaoxian_ready
    ) and existing.daily_ready
    if metadata.daily_ready and not (
        preserve_existing_daily
    ):
        save_task_session(session, base_dir, metadata, TASK_DAILY)
        imported.append(TASK_DAILY)
    if metadata.star_ready:
        save_task_session(session, base_dir, metadata, TASK_STAR)
        imported.append(TASK_STAR)
    if not account_file(base_dir).exists() and metadata.account:
        save_account(metadata, base_dir)
    return load_gt_metadata(base_dir), imported


def clear_configuration(base_dir: Path = DEFAULT_DATA_DIR) -> list[Path]:
    """Delete only account and task files. HARs and logs are untouched."""
    removed: list[Path] = []
    paths = [
        account_file(base_dir),
        task_file(TASK_DAILY, base_dir),
        task_file(TASK_STAR, base_dir),
        Path(base_dir) / ZHAOXIAN_FILE.name,
        Path(base_dir) / QILUO_JINGYI_FILE.name,
    ]
    for path in paths:
        if path.exists():
            path.unlink()
            removed.append(path)
    return removed


def _identity_value(account: Dict[str, Any], key: str) -> str:
    value = account.get(key)
    return str(value).strip() if value not in (None, "") else ""


def accounts_match(
    existing: Dict[str, Any],
    imported: Dict[str, Any],
) -> bool:
    compared = False
    for key in ("uid", "role_id"):
        old = _identity_value(existing, key)
        new = _identity_value(imported, key)
        if old and new:
            compared = True
            if old != new:
                return False
    return compared or not existing or not imported


def merge_star_session(
    session: Any,
    base_dir: Path = DEFAULT_DATA_DIR,
    metadata: Optional[SessionMetadata] = None,
) -> SessionMetadata:
    ensure_data_files(base_dir)
    if not task_file(TASK_DAILY, base_dir).exists():
        raise SessionFileError("每日福利任务文件不存在，无法执行星盘合并")
    existing_metadata = load_gt_metadata(base_dir)
    if metadata is None:
        metadata = SessionMetadata(
            source_har=str(session.har_path),
            star_ready=True,
            account=existing_metadata.account,
        )
    if not metadata.star_ready:
        raise SessionFileError("新 HAR 中没有四艺星盘活动数据")
    if not accounts_match(existing_metadata.account, metadata.account):
        raise SessionFileError("新 HAR 与现有账号或角色不一致")
    metadata.account = existing_metadata.account or metadata.account
    save_task_session(session, base_dir, metadata, TASK_STAR)
    return load_gt_metadata(base_dir)


def _extract_account(har: Dict[str, Any]) -> Dict[str, Any]:
    from siyi_star_auto import field_map, parse_frame

    for entry in har.get("log", {}).get("entries", []):
        for message in entry.get("_webSocketMessages") or []:
            if message.get("type") != "receive":
                continue
            try:
                frame = base64.b64decode(message.get("data", ""))
                packet = parse_frame(frame)
            except Exception:
                continue
            if packet.command != 1000:
                continue
            fields = field_map(packet.payload)
            role_id = fields.get(1, [None])[0]
            role_name = fields.get(2, [b""])[0]
            uid = fields.get(4, [b""])[0]
            if isinstance(role_name, bytes):
                role_name = role_name.decode("utf-8", "replace")
            if isinstance(uid, bytes):
                uid = uid.decode("utf-8", "replace")
            return {
                "role_id": role_id,
                "role_name": role_name,
                "uid": uid,
            }
    return {}


def extract_gt_session(har_path: Path) -> SessionImportResult:
    """Extract task-capable session data from one HAR."""
    from daily_welfare_claim import (
        extract_welfare_session,
        find_last_har_reward,
    )
    from siyi_star_auto import HarFormatError, extract_session

    har_path = Path(har_path)
    if not har_path.exists():
        raise FileNotFoundError("HAR 不存在：%s" % har_path)
    if har_path.suffix.lower() != ".har":
        raise ValueError("请选择 .har 文件")
    star_ready = True
    try:
        session = extract_session(har_path)
    except HarFormatError:
        star_ready = False
        session = extract_welfare_session(har_path)

    reward = find_last_har_reward(har_path)
    reward_payload = None
    if reward is not None:
        reward_payload = {
            "reward_id": reward.reward_id,
            "status": reward.status,
            "server_time": reward.server_time,
        }
    try:
        har = json.loads(har_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        har = {}
    metadata = SessionMetadata(
        source_har=str(har_path),
        created_at=_now(),
        daily_ready=True,
        star_ready=star_ready,
        account=_extract_account(har),
        last_reward=reward_payload,
        context_id=session.context_id,
        activity_id=session.activity_id,
        init_frames=len(session.init_frames),
    )
    zhaoxian_config = None
    qiluo_jingyi_config = None
    try:
        from gt_automation.zhaoxian import extract_zhaoxian_config

        zhaoxian_config = extract_zhaoxian_config(
            har_path,
            session=session,
            account=metadata.account,
        )
    except Exception:
        zhaoxian_config = None
    try:
        from gt_automation.qiluo_jingyi import extract_qiluo_jingyi_config

        qiluo_jingyi_config = extract_qiluo_jingyi_config(
            har_path,
            session=session,
            account=metadata.account,
        )
    except Exception:
        qiluo_jingyi_config = None
    metadata.zhaoxian_ready = zhaoxian_config is not None
    metadata.qiluo_jingyi_ready = qiluo_jingyi_config is not None
    return SessionImportResult(
        session=session,
        metadata=metadata,
        zhaoxian_config=zhaoxian_config,
        qiluo_jingyi_config=qiluo_jingyi_config,
    )


def import_har_to_gt(
    har_path: Path,
    base_dir: Path = DEFAULT_DATA_DIR,
) -> SessionMetadata:
    result = extract_gt_session(har_path)
    return save_import(result.session, base_dir, result.metadata, replace=True)
