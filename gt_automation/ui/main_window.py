"""Main GT Automation Studio desktop window."""

from __future__ import annotations

import json
import logging
import queue
import re
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Dict, Optional

from gt_automation.logging_setup import configure_app_logging, get_app_logger
from gt_automation.network import detect_proxy_ca, format_user_error
from gt_automation.paths import (
    DEFAULT_DATA_DIR,
    HAR_DIR,
    LOGS_DIR,
    PROJECT_ROOT,
    SETTINGS_FILE,
    ensure_runtime_dirs,
)
from gt_automation.process_runner import ProcessRunner
from gt_automation.qiluo_jingyi import (
    QiluoJingyiError,
    load_qiluo_jingyi_config,
    save_qiluo_jingyi_config,
)
from gt_automation.session_store import (
    SessionFileError,
    SessionImportResult,
    SessionMetadata,
    TASK_DAILY,
    clear_configuration,
    ensure_data_files,
    extract_gt_session,
    import_detected_tasks,
    load_gt_metadata,
)
from gt_automation.task_launcher import task_base_command
from gt_automation.ui.theme import COLORS, configure_theme, set_theme
from gt_automation.zhaoxian import (
    ZhaoxianError,
    load_zhaoxian_config,
    save_zhaoxian_config,
)


DAILY_RESULT_PATTERN = re.compile(
    r"reward_id=(?P<reward>\S+)\s+status=(?P<status>\S+)\s+"
    r"server_time=(?P<server_time>\S+)"
)


class GtAutomationApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        ensure_runtime_dirs()
        configure_app_logging(LOGS_DIR)
        self.logger = get_app_logger()
        self.theme_name = "light"
        self.last_har_dir = HAR_DIR
        self._saved_ca_file = ""
        self._load_settings()
        self.theme_name = set_theme(self.theme_name)
        self.fonts = configure_theme(self)
        detected_ca = detect_proxy_ca()
        self.ca_file_var = tk.StringVar(
            master=self,
            value=self._saved_ca_file
            or (str(detected_ca) if detected_ca is not None else ""),
        )

        self.title("GT Automation Studio")
        self.geometry("1180x860")
        self.minsize(1040, 780)
        self.configure(bg=COLORS["bg"])
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self.data_dir = DEFAULT_DATA_DIR
        self.session_meta: Optional[SessionMetadata] = None
        self.runner = ProcessRunner()
        self.ui_events: "queue.Queue[tuple[str, Any]]" = queue.Queue()
        self.busy = False
        self.importing = False
        self.active_task: Optional[str] = None
        self.daily_result_data: Optional[tuple[str, str, str]] = None
        self.daily_last_error = ""
        self.zhaoxian_result_data: Optional[Dict[str, Any]] = None
        self.zhaoxian_last_error = ""
        self.qiluo_result_data: Optional[Dict[str, Any]] = None
        self.qiluo_last_error = ""
        self._build_layout()
        self._load_gt_status()
        self._refresh_controls()
        self.after(120, self._poll_events)

    def _load_settings(self) -> None:
        if not SETTINGS_FILE.exists():
            return
        try:
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        self._settings = data
        last_har_dir = data.get("last_har_dir")
        if isinstance(last_har_dir, str) and Path(last_har_dir).is_dir():
            self.last_har_dir = Path(last_har_dir)
        theme_name = data.get("theme")
        if theme_name in {"dark", "light"}:
            self.theme_name = theme_name
        ca_file = data.get("ca_file")
        if isinstance(ca_file, str) and Path(ca_file).is_file():
            self._saved_ca_file = ca_file

    def _save_settings(self) -> None:
        ensure_runtime_dirs()
        data = {
            "last_har_dir": str(self.last_har_dir),
            "geometry": self.geometry(),
            "theme": self.theme_name,
            "ca_file": self.ca_file_var.get().strip(),
        }
        temporary = SETTINGS_FILE.with_name(SETTINGS_FILE.name + ".tmp")
        temporary.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(SETTINGS_FILE)

    def _build_layout(self) -> None:
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self._build_sidebar()
        self._build_main()

    def _build_sidebar(self) -> None:
        sidebar = tk.Frame(
            self,
            width=210,
            bg=COLORS["sidebar"],
            highlightthickness=0,
        )
        sidebar.grid(row=0, column=0, sticky="ns")
        sidebar.grid_propagate(False)
        sidebar.grid_rowconfigure(8, weight=1)

        brand = tk.Frame(sidebar, bg=COLORS["sidebar"], padx=24, pady=24)
        brand.pack(fill="x")
        tk.Label(
            brand,
            text="GT",
            bg=COLORS["accent"],
            fg=COLORS["primary_text"],
            width=3,
            height=1,
            font=self.fonts["brand"],
            bd=0,
        ).pack(anchor="w")
        tk.Label(
            brand,
            text="AUTOMATION\nSTUDIO",
            bg=COLORS["sidebar"],
            fg=COLORS["text"],
            justify="left",
            font=self.fonts["brand"],
        ).pack(anchor="w", pady=(12, 0))
        tk.Label(
            brand,
            text="桌面任务控制台",
            bg=COLORS["sidebar"],
            fg=COLORS["muted"],
            font=self.fonts["small"],
        ).pack(anchor="w", pady=(4, 0))

        spacer = tk.Frame(sidebar, bg=COLORS["sidebar"])
        spacer.pack(fill="both", expand=True)

        session_box = tk.Frame(
            sidebar,
            bg=COLORS["sidebar_card"],
            highlightbackground=COLORS["border"],
            highlightthickness=1,
            padx=15,
            pady=14,
        )
        session_box.pack(fill="x", padx=16, pady=16)
        tk.Label(
            session_box,
            text="共享会话",
            bg=COLORS["sidebar_card"],
            fg=COLORS["muted"],
            font=self.fonts["small"],
        ).pack(anchor="w")
        self.sidebar_status = tk.Label(
            session_box,
            text="未加载",
            bg=COLORS["sidebar_card"],
            fg=COLORS["warning"],
            font=self.fonts["bold"],
        )
        self.sidebar_status.pack(anchor="w", pady=(5, 3))
        self.sidebar_account = tk.Label(
            session_box,
            text="需要账号配置",
            bg=COLORS["sidebar_card"],
            fg=COLORS["muted"],
            font=self.fonts["small"],
        )
        self.sidebar_account.pack(anchor="w")
        tk.Label(
            sidebar,
            text="v1.0  ·  macOS / Windows",
            bg=COLORS["sidebar"],
            fg=COLORS["muted"],
            font=self.fonts["small"],
        ).pack(fill="x", padx=24, pady=(0, 18))

    def _build_main(self) -> None:
        main = tk.Frame(self, bg=COLORS["bg"], padx=28, pady=22)
        main.grid(row=0, column=1, sticky="nsew")
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(3, weight=0)

        header = tk.Frame(main, bg=COLORS["bg"])
        header.grid(row=0, column=0, sticky="ew", pady=(0, 18))
        header.grid_columnconfigure(0, weight=1)
        tk.Label(
            header,
            text="任务控制台",
            bg=COLORS["bg"],
            fg=COLORS["text"],
            font=self.fonts["title"],
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            header,
            text="会话配置与任务入口",
            bg=COLORS["bg"],
            fg=COLORS["muted"],
            font=self.fonts["small"],
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))
        self.header_badge = tk.Label(
            header,
            text="配置未就绪",
            bg=COLORS["warning_bg"],
            fg=COLORS["warning"],
            padx=14,
            pady=7,
            font=self.fonts["bold"],
        )
        self.header_badge.grid(row=0, column=1, rowspan=2, sticky="e")
        self.theme_button = ttk.Button(
            header,
            text="浅色模式" if self.theme_name == "dark" else "深色模式",
            style="Secondary.TButton",
            command=self._toggle_theme,
        )
        self.theme_button.grid(
            row=0,
            column=2,
            rowspan=2,
            sticky="e",
            padx=(10, 0),
        )
        self.settings_button = ttk.Button(
            header,
            text="设置",
            style="Secondary.TButton",
            command=self._open_settings,
        )
        self.settings_button.grid(
            row=0,
            column=3,
            rowspan=2,
            sticky="e",
            padx=(8, 0),
        )

        self._build_session_card(main)
        self._build_task_cards(main)
        self._build_status_footer(main)

    def _card(self, parent: tk.Misc) -> tk.Frame:
        return tk.Frame(
            parent,
            bg=COLORS["card"],
            highlightbackground=COLORS["border"],
            highlightthickness=1,
            padx=20,
            pady=17,
        )

    def _build_session_card(self, parent: tk.Frame) -> None:
        card = self._card(parent)
        card.grid(row=1, column=0, sticky="ew", pady=(0, 14))
        card.grid_columnconfigure(1, weight=1)

        tk.Label(
            card,
            text="会话配置",
            bg=COLORS["card"],
            fg=COLORS["text"],
            font=self.fonts["section"],
        ).grid(row=0, column=0, columnspan=3, sticky="w")
        self.clear_config_button = ttk.Button(
            card,
            text="一键清除配置",
            style="Danger.TButton",
            command=self._clear_configuration,
        )
        self.clear_config_button.grid(row=0, column=3, sticky="e")
        tk.Label(
            card,
            text="从 HAR 生成 account.json 和独立的每日福利、星盘任务文件。",
            bg=COLORS["card"],
            fg=COLORS["muted"],
            font=self.fonts["small"],
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(4, 14))

        tk.Label(
            card,
            text="HAR 文件",
            bg=COLORS["card"],
            fg=COLORS["muted"],
            font=self.fonts["small"],
        ).grid(row=2, column=0, sticky="w", padx=(0, 12))
        self.har_var = tk.StringVar()
        self.har_entry = ttk.Entry(card, textvariable=self.har_var)
        self.har_entry.grid(row=2, column=1, sticky="ew", padx=(0, 10))
        self.browse_button = ttk.Button(
            card,
            text="选择",
            style="Secondary.TButton",
            command=self._choose_har,
        )
        self.browse_button.grid(row=2, column=2, padx=(0, 8))
        self.import_button = ttk.Button(
            card,
            text="生成配置",
            style="Primary.TButton",
            command=self._start_import,
        )
        self.import_button.grid(row=2, column=3)

        metrics = tk.Frame(card, bg=COLORS["card"])
        metrics.grid(
            row=3,
            column=0,
            columnspan=4,
            sticky="ew",
            pady=(15, 0),
        )
        for index in range(2):
            metrics.grid_columnconfigure(index, weight=1)
        self.account_value = self._metric(metrics, 0, "账号")
        self.capability_value = self._metric(metrics, 1, "能力")

        self.import_summary_label = tk.Label(
            card,
            text="尚未导入任务",
            bg=COLORS["card"],
            fg=COLORS["muted"],
            font=self.fonts["small"],
        )
        self.import_summary_label.grid(
            row=4,
            column=0,
            columnspan=4,
            sticky="w",
            pady=(11, 0),
        )

        self.import_progress = ttk.Progressbar(
            card,
            mode="indeterminate",
            style="Horizontal.TProgressbar",
        )
        self.import_progress.grid(
            row=5,
            column=0,
            columnspan=4,
            sticky="ew",
            pady=(14, 0),
        )
        self.import_progress.grid_remove()

    def _metric(
        self,
        parent: tk.Misc,
        column: int,
        title: str,
    ) -> tk.Label:
        frame = tk.Frame(parent, bg=COLORS["card_alt"], padx=13, pady=10)
        frame.grid(
            row=0,
            column=column,
            sticky="ew",
            padx=(0 if column == 0 else 8, 0),
        )
        tk.Label(
            frame,
            text=title.upper(),
            bg=COLORS["card_alt"],
            fg=COLORS["muted"],
            font=self.fonts["small"],
        ).pack(anchor="w")
        value = tk.Label(
            frame,
            text="—",
            bg=COLORS["card_alt"],
            fg=COLORS["text"],
            font=self.fonts["bold"],
        )
        value.pack(anchor="w", pady=(3, 0))
        return value

    def _build_task_cards(self, parent: tk.Frame) -> None:
        row = tk.Frame(parent, bg=COLORS["bg"])
        row.grid(row=2, column=0, sticky="ew", pady=(0, 14))
        row.grid_columnconfigure(0, weight=1, uniform="tasks")
        row.grid_columnconfigure(1, weight=1, uniform="tasks")

        daily = self._card(row)
        daily.grid(row=0, column=0, sticky="nsew", padx=(0, 6), pady=(0, 6))
        self._build_daily_card(daily)

        star = self._card(row)
        star.grid(row=0, column=1, sticky="nsew", padx=(6, 0), pady=(0, 6))
        self._build_star_card(star)

        zhaoxian = self._card(row)
        zhaoxian.grid(row=1, column=0, sticky="nsew", padx=(0, 6), pady=(6, 0))
        self._build_zhaoxian_card(zhaoxian)

        qiluo = self._card(row)
        qiluo.grid(row=1, column=1, sticky="nsew", padx=(6, 0), pady=(6, 0))
        self._build_qiluo_card(qiluo)

    def _build_daily_card(self, card: tk.Frame) -> None:
        card.grid_columnconfigure(0, weight=1)
        tk.Label(
            card,
            text="每日福利",
            bg=COLORS["card"],
            fg=COLORS["text"],
            font=self.fonts["section"],
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            card,
            text="一键连接游戏服务器并直接提交每日福利领奖请求。",
            bg=COLORS["card"],
            fg=COLORS["muted"],
            wraplength=410,
            justify="left",
            font=self.fonts["small"],
        ).grid(row=1, column=0, sticky="w", pady=(4, 12))

        tk.Label(
            card,
            text="task/daily_welfare.json",
            bg=COLORS["card"],
            fg=COLORS["blue"],
            font=self.fonts["small"],
        ).grid(row=2, column=0, sticky="w", pady=(0, 10))

        actions = tk.Frame(card, bg=COLORS["card"])
        actions.grid(row=3, column=0, sticky="ew")
        self.daily_run_button = ttk.Button(
            actions,
            text="运行任务",
            style="Primary.TButton",
            command=lambda: self._run_task("daily", live=True),
        )
        self.daily_run_button.pack(side="left")

        result_box = tk.Frame(
            card,
            bg=COLORS["card_alt"],
            padx=13,
            pady=10,
        )
        result_box.grid(row=4, column=0, sticky="ew", pady=(14, 0))
        tk.Label(
            result_box,
            text="最近结果",
            bg=COLORS["card_alt"],
            fg=COLORS["muted"],
            font=self.fonts["small"],
        ).pack(anchor="w")
        self.daily_result_status = tk.Label(
            result_box,
            text="暂无记录",
            bg=COLORS["card_alt"],
            fg=COLORS["text"],
            font=self.fonts["bold"],
        )
        self.daily_result_status.pack(anchor="w", pady=(3, 2))
        self.daily_result_detail = tk.Label(
            result_box,
            text="",
            bg=COLORS["card_alt"],
            fg=COLORS["muted"],
            justify="left",
            anchor="w",
            wraplength=390,
            font=self.fonts["small"],
        )
        self.daily_result_detail.pack(anchor="w", fill="x")

    def _build_star_card(self, card: tk.Frame) -> None:
        card.grid_columnconfigure(0, weight=1)
        tk.Label(
            card,
            text="四艺星盘",
            bg=COLORS["card"],
            fg=COLORS["text"],
            font=self.fonts["section"],
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            card,
            text="使用独立星盘任务文件中的活动状态执行升星流程。",
            bg=COLORS["card"],
            fg=COLORS["muted"],
            font=self.fonts["small"],
        ).grid(row=1, column=0, sticky="w", pady=(4, 12))

        tk.Label(
            card,
            text="task/siyi_star.json",
            bg=COLORS["card"],
            fg=COLORS["blue"],
            font=self.fonts["small"],
        ).grid(row=2, column=0, sticky="w", pady=(0, 10))

        self.star_claim_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            card,
            text="达到 7 星后领奖",
            variable=self.star_claim_var,
        ).grid(row=3, column=0, sticky="w", pady=(0, 10))

        actions = tk.Frame(card, bg=COLORS["card"])
        actions.grid(row=4, column=0, sticky="ew")
        self.star_run_button = ttk.Button(
            actions,
            text="运行任务",
            style="Primary.TButton",
            command=lambda: self._run_task("star", live=True),
        )
        self.star_run_button.pack(side="left")
        self.star_hint = tk.Label(
            card,
            text="尚未导入四艺星盘",
            bg=COLORS["card"],
            fg=COLORS["muted"],
            justify="left",
            anchor="w",
            wraplength=380,
            font=self.fonts["small"],
        )
        self.star_hint.grid(row=5, column=0, sticky="ew", pady=(10, 0))

    def _build_zhaoxian_card(self, card: tk.Frame) -> None:
        card.grid_columnconfigure(0, weight=1)
        tk.Label(
            card,
            text="招贤",
            bg=COLORS["card"],
            fg=COLORS["text"],
            font=self.fonts["section"],
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            card,
            text="提交招贤十连请求并显示抽取结果。",
            bg=COLORS["card"],
            fg=COLORS["muted"],
            font=self.fonts["small"],
        ).grid(row=1, column=0, sticky="w", pady=(4, 8))
        tk.Label(
            card,
            text="zhaoxian.json",
            bg=COLORS["card"],
            fg=COLORS["blue"],
            font=self.fonts["small"],
        ).grid(row=2, column=0, sticky="w", pady=(0, 8))
        self.zhaoxian_hint = tk.Label(
            card,
            text="尚未导入招贤",
            bg=COLORS["card"],
            fg=COLORS["muted"],
            font=self.fonts["small"],
        )
        self.zhaoxian_hint.grid(row=3, column=0, sticky="w", pady=(0, 8))
        self.zhaoxian_run_button = ttk.Button(
            card,
            text="运行任务",
            style="Primary.TButton",
            command=lambda: self._run_task("zhaoxian", live=True),
        )
        self.zhaoxian_run_button.grid(row=4, column=0, sticky="w")

        result_box = tk.Frame(
            card,
            bg=COLORS["card_alt"],
            padx=11,
            pady=9,
        )
        result_box.grid(row=5, column=0, sticky="ew", pady=(11, 0))
        self.zhaoxian_result_status = tk.Label(
            result_box,
            text="暂无招贤记录",
            bg=COLORS["card_alt"],
            fg=COLORS["text"],
            font=self.fonts["bold"],
        )
        self.zhaoxian_result_status.pack(anchor="w")
        self.zhaoxian_result_detail = tk.Label(
            result_box,
            text="",
            bg=COLORS["card_alt"],
            fg=COLORS["muted"],
            justify="left",
            anchor="w",
            wraplength=250,
            font=self.fonts["small"],
        )
        self.zhaoxian_result_detail.pack(anchor="w", fill="x", pady=(3, 0))

    def _build_qiluo_card(self, card: tk.Frame) -> None:
        card.grid_columnconfigure(0, weight=1)
        tk.Label(
            card,
            text="绮罗锦衣",
            bg=COLORS["card"],
            fg=COLORS["text"],
            font=self.fonts["section"],
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            card,
            text="提交绮罗锦衣特惠直购并显示奖励。",
            bg=COLORS["card"],
            fg=COLORS["muted"],
            font=self.fonts["small"],
        ).grid(row=1, column=0, sticky="w", pady=(4, 8))
        tk.Label(
            card,
            text="qiluo_jingyi.json",
            bg=COLORS["card"],
            fg=COLORS["blue"],
            font=self.fonts["small"],
        ).grid(row=2, column=0, sticky="w", pady=(0, 8))
        self.qiluo_hint = tk.Label(
            card,
            text="尚未导入绮罗锦衣",
            bg=COLORS["card"],
            fg=COLORS["muted"],
            font=self.fonts["small"],
        )
        self.qiluo_hint.grid(row=3, column=0, sticky="w", pady=(0, 8))
        self.qiluo_run_button = ttk.Button(
            card,
            text="运行任务",
            style="Primary.TButton",
            command=lambda: self._run_task("qiluo", live=True),
        )
        self.qiluo_run_button.grid(row=4, column=0, sticky="w")

        result_box = tk.Frame(
            card,
            bg=COLORS["card_alt"],
            padx=11,
            pady=9,
        )
        result_box.grid(row=5, column=0, sticky="ew", pady=(11, 0))
        self.qiluo_result_status = tk.Label(
            result_box,
            text="暂无绮罗锦衣记录",
            bg=COLORS["card_alt"],
            fg=COLORS["text"],
            font=self.fonts["bold"],
        )
        self.qiluo_result_status.pack(anchor="w")
        self.qiluo_result_detail = tk.Label(
            result_box,
            text="",
            bg=COLORS["card_alt"],
            fg=COLORS["muted"],
            justify="left",
            anchor="w",
            wraplength=460,
            font=self.fonts["small"],
        )
        self.qiluo_result_detail.pack(anchor="w", fill="x", pady=(3, 0))

    def _build_status_footer(self, parent: tk.Frame) -> None:
        footer = tk.Frame(
            parent,
            bg=COLORS["card"],
            highlightbackground=COLORS["border"],
            highlightthickness=1,
            padx=16,
            pady=10,
        )
        footer.grid(row=3, column=0, sticky="ew")
        footer.grid_columnconfigure(1, weight=1)

        self.status_dot = tk.Label(
            footer,
            text="●",
            bg=COLORS["card"],
            fg=COLORS["muted"],
            font=self.fonts["small"],
        )
        self.status_dot.grid(row=0, column=0, sticky="w")
        self.status_label = tk.Label(
            footer,
            text="就绪",
            bg=COLORS["card"],
            fg=COLORS["text"],
            font=self.fonts["bold"],
        )
        self.status_label.grid(row=0, column=1, sticky="w", padx=(7, 0))
        tk.Label(
            footer,
            text="日志保存在 %s" % LOGS_DIR,
            bg=COLORS["card"],
            fg=COLORS["muted"],
            font=self.fonts["small"],
        ).grid(row=0, column=2, sticky="e", padx=(12, 14))
        self.stop_button = ttk.Button(
            footer,
            text="停止任务",
            style="Danger.TButton",
            command=self._stop_task,
        )
        self.stop_button.grid(row=0, column=3, sticky="e")

    def _choose_har(self) -> None:
        selected = filedialog.askopenfilename(
            parent=self,
            title="选择当前版本 HAR",
            initialdir=str(self.last_har_dir),
            filetypes=[("HAR 文件", "*.har"), ("所有文件", "*")],
        )
        if selected:
            self.har_var.set(selected)
            self.last_har_dir = Path(selected).parent

    def _start_import(self) -> None:
        if self.importing or self.busy:
            return
        raw_path = self.har_var.get().strip()
        if not raw_path:
            messagebox.showwarning(
                "缺少 HAR",
                "请先选择一个当前版本的 HAR 文件。",
                parent=self,
            )
            return
        har_path = Path(raw_path).expanduser()
        if not har_path.exists():
            messagebox.showerror(
                "文件不存在",
                "HAR 文件不存在：\n%s" % har_path,
                parent=self,
            )
            return
        self.importing = True
        self._set_status("正在解析 HAR", COLORS["blue"])
        self._log_event("[导入] 正在解析 HAR：%s" % har_path)
        self.import_progress.grid()
        self.import_progress.start(12)
        self._refresh_controls()

        thread = threading.Thread(
            target=self._import_worker,
            args=(har_path,),
            daemon=True,
            name="gt-har-import",
        )
        thread.start()

    def _import_worker(self, har_path: Path) -> None:
        try:
            result = extract_gt_session(har_path)
            self.ui_events.put(("import_ready", result))
        except Exception as exc:
            self.ui_events.put(("import_error", str(exc)))

    def _handle_import_ready(self, result: SessionImportResult) -> None:
        try:
            metadata, imported = import_detected_tasks(
                result.session,
                self.data_dir,
                result.metadata,
            )
            if result.zhaoxian_config is not None:
                save_zhaoxian_config(
                    result.zhaoxian_config,
                    self.data_dir,
                )
                imported.append("zhaoxian")
                metadata = load_gt_metadata(self.data_dir)
            if result.qiluo_jingyi_config is not None:
                save_qiluo_jingyi_config(
                    result.qiluo_jingyi_config,
                    self.data_dir,
                )
                imported.append("qiluo")
                metadata = load_gt_metadata(self.data_dir)
        except Exception as exc:
            self._handle_import_error(str(exc))
            return
        self.importing = False
        self.import_progress.stop()
        self.import_progress.grid_remove()
        self.session_meta = metadata
        self._apply_metadata(metadata)
        task_names = {
            TASK_DAILY: "每日福利",
            "siyi_star": "四艺星盘",
            "zhaoxian": "招贤",
            "qiluo": "绮罗锦衣",
        }
        names = [task_names[task] for task in imported if task in task_names]
        summary = "已导入：" + "、".join(names) if names else "未发现可导入任务"
        self._set_status(summary, COLORS["success"])
        self._log_event("[完成] %s" % summary)
        self._refresh_controls()

    def _handle_import_error(self, message: str) -> None:
        self.importing = False
        self.import_progress.stop()
        self.import_progress.grid_remove()
        self._set_status("HAR 导入失败", COLORS["danger"])
        self._log_event(
            "[失败] HAR 导入失败：%s" % message,
            logging.ERROR,
        )
        messagebox.showerror("HAR 导入失败", message, parent=self)
        self._refresh_controls()

    def _load_gt_status(self) -> None:
        try:
            ensure_data_files(self.data_dir)
        except Exception as exc:
            self.session_meta = None
            self._apply_missing_state(error=str(exc))
            return
        if not (self.data_dir / "account.json").exists():
            self.session_meta = None
            self._apply_missing_state()
            self._refresh_controls()
            return
        try:
            metadata = load_gt_metadata(self.data_dir)
        except (OSError, SessionFileError, ValueError) as exc:
            self.session_meta = None
            self._apply_missing_state(error=str(exc))
            self._refresh_controls()
            return
        self.session_meta = metadata
        self._apply_metadata(metadata)
        self._refresh_controls()

    def _apply_missing_state(self, error: str = "") -> None:
        self.header_badge.configure(
            text="配置未就绪",
            bg=COLORS["warning_bg"],
            fg=COLORS["warning"],
        )
        self.sidebar_status.configure(
            text="未加载",
            fg=COLORS["warning"],
        )
        self.sidebar_account.configure(text="需要账号配置")
        self.account_value.configure(text="—")
        self.capability_value.configure(text="不可运行")
        self.import_summary_label.configure(
            text="尚未导入任务",
            fg=COLORS["muted"],
        )
        self.star_hint.configure(
            text="尚未导入四艺星盘",
            fg=COLORS["muted"],
        )
        self.zhaoxian_hint.configure(
            text="尚未导入招贤",
            fg=COLORS["muted"],
        )
        self.qiluo_hint.configure(
            text="尚未导入绮罗锦衣",
            fg=COLORS["muted"],
        )
        self._set_daily_result(
            "尚未导入每日福利",
            "请先生成或导入账号配置",
            COLORS["muted"],
        )
        self._set_zhaoxian_result(
            "尚未导入招贤",
            "请先导入包含招贤命令的 HAR",
            COLORS["muted"],
        )
        self._set_qiluo_result(
            "尚未导入绮罗锦衣",
            "请先导入包含命令 5515 的 HAR",
            COLORS["muted"],
        )
        if error:
            self._log_event(
                "[错误] 账号或任务配置读取失败：%s" % error,
                logging.ERROR,
            )

    def _apply_metadata(self, metadata: SessionMetadata) -> None:
        account = metadata.account
        role_name = str(account.get("role_name") or "").strip()
        role_id = account.get("role_id")
        account_text = role_name or "已登录会话"
        if role_id:
            account_text = "%s · %s" % (account_text, role_id)
        self.header_badge.configure(
            text="配置已就绪",
            bg=COLORS["success_bg"],
            fg=COLORS["accent"],
        )
        self.sidebar_status.configure(
            text="已就绪",
            fg=COLORS["accent"],
        )
        self.sidebar_account.configure(text=account_text)
        self.account_value.configure(text=account_text)
        capability_names = []
        if metadata.daily_ready:
            capability_names.append("每日福利")
        if metadata.star_ready:
            capability_names.append("四艺星盘")
        if metadata.zhaoxian_ready:
            capability_names.append("招贤")
        if metadata.qiluo_jingyi_ready:
            capability_names.append("绮罗锦衣")
        capability_text = (
            " + ".join(capability_names) if capability_names else "不可运行"
        )
        self.capability_value.configure(text=capability_text)
        imported_names = []
        if metadata.daily_ready:
            imported_names.append("每日福利")
        if metadata.star_ready:
            imported_names.append("四艺星盘")
        if metadata.zhaoxian_ready:
            imported_names.append("招贤")
        if metadata.qiluo_jingyi_ready:
            imported_names.append("绮罗锦衣")
        self.import_summary_label.configure(
            text=(
                "已导入：" + "、".join(imported_names)
                if imported_names
                else "尚未导入任务"
            ),
            fg=COLORS["accent"] if imported_names else COLORS["muted"],
        )
        self.star_hint.configure(
            text=(
                "已导入四艺星盘"
                if metadata.star_ready
                else "尚未导入四艺星盘"
            ),
            fg=COLORS["success"] if metadata.star_ready else COLORS["muted"],
        )
        self.zhaoxian_hint.configure(
            text=(
                "已导入招贤"
                if metadata.zhaoxian_ready
                else "尚未导入招贤"
            ),
            fg=COLORS["success"] if metadata.zhaoxian_ready else COLORS["muted"],
        )
        if metadata.zhaoxian_ready:
            if self.zhaoxian_result_data is not None:
                self._show_zhaoxian_data(self.zhaoxian_result_data)
            else:
                try:
                    config = load_zhaoxian_config(self.data_dir)
                    if config.last_results:
                        self._show_zhaoxian_results(
                            config.last_results,
                            prefix="上次招贤",
                        )
                    else:
                        self._set_zhaoxian_result(
                            "已导入招贤",
                            "尚未执行招贤十连",
                            COLORS["muted"],
                        )
                except (OSError, ValueError, ZhaoxianError):
                    self._set_zhaoxian_result(
                        "招贤配置异常",
                        "zhaoxian.json 无法读取",
                        COLORS["danger"],
                    )
        else:
            self._set_zhaoxian_result(
                "尚未导入招贤",
                "请先导入包含招贤命令的 HAR",
                COLORS["muted"],
            )
        self.qiluo_hint.configure(
            text=(
                "已导入绮罗锦衣"
                if metadata.qiluo_jingyi_ready
                else "尚未导入绮罗锦衣"
            ),
            fg=(
                COLORS["success"]
                if metadata.qiluo_jingyi_ready
                else COLORS["muted"]
            ),
        )
        if metadata.qiluo_jingyi_ready:
            if self.qiluo_result_data is not None:
                self._show_qiluo_data(self.qiluo_result_data)
            else:
                try:
                    config = load_qiluo_jingyi_config(self.data_dir)
                    if config.last_rewards:
                        self._show_qiluo_results(
                            config.last_rewards,
                            prefix="上次特惠直购",
                        )
                    else:
                        self._set_qiluo_result(
                            "已导入绮罗锦衣",
                            "尚未执行特惠直购",
                            COLORS["muted"],
                        )
                except (OSError, ValueError, QiluoJingyiError):
                    self._set_qiluo_result(
                        "绮罗锦衣配置异常",
                        "qiluo_jingyi.json 无法读取",
                        COLORS["danger"],
                    )
        else:
            self._set_qiluo_result(
                "尚未导入绮罗锦衣",
                "请先导入包含命令 5515 的 HAR",
                COLORS["muted"],
            )
        reward = metadata.last_reward
        if not metadata.daily_ready:
            self._set_daily_result(
                "尚未导入每日福利",
                "请先导入每日福利 HAR",
                COLORS["muted"],
            )
        elif self.daily_result_data is not None:
            self._set_daily_result(
                "领取成功",
                "奖励：%s\n状态：%s" % self.daily_result_data[:2],
                COLORS["success"],
            )
        elif self.daily_last_error:
            self._set_daily_result(
                "领取失败",
                self.daily_last_error,
                COLORS["danger"],
            )
        elif isinstance(reward, dict) and reward.get("reward_id"):
            self._set_daily_result(
                "最近一次领取成功",
                "奖励：%s\n状态：%s"
                % (reward.get("reward_id"), reward.get("status", "—")),
                COLORS["success"],
            )
        else:
            self._set_daily_result(
                "尚未运行",
                "点击“运行任务”查看领取结果",
                COLORS["muted"],
            )

    def _run_task(self, task: str, live: bool) -> None:
        if self.busy or self.importing:
            return
        if (
            not (self.data_dir / "account.json").exists()
            or self.session_meta is None
        ):
            messagebox.showerror(
                "缺少账号配置",
                "未找到或无法读取 account.json，不能运行任务。",
                parent=self,
            )
            return
        if task == "star" and not self.session_meta.star_ready:
            messagebox.showerror(
                "星盘数据不可用",
                "当前没有四艺星盘任务文件，"
                "请重新导入包含命令 6589 的 HAR。",
                parent=self,
            )
            return
        if task == "zhaoxian" and not self.session_meta.zhaoxian_ready:
            messagebox.showerror(
                "招贤数据不可用",
                "当前没有招贤任务配置文件，请导入包含招贤 HAR。",
                parent=self,
            )
            return
        if task == "qiluo" and not self.session_meta.qiluo_jingyi_ready:
            messagebox.showerror(
                "绮罗锦衣数据不可用",
                "当前没有绮罗锦衣任务配置文件，"
                "请导入包含命令 5515 的 HAR。",
                parent=self,
            )
            return
        if task == "daily":
            command = task_base_command("daily") + [
                "--data-dir",
                str(self.data_dir),
            ]
            if live:
                command.extend(["--live", "--confirm-ad-watched"])
            else:
                command.append("--dry-run")
            label = "每日福利"
        elif task == "star":
            command = task_base_command("star") + [
                "--data-dir",
                str(self.data_dir),
            ]
            if live:
                command.append("--live")
            else:
                command.append("--dry-run")
            if self.star_claim_var.get():
                command.append("--claim")
            label = "四艺星盘"
        elif task == "zhaoxian":
            command = task_base_command("zhaoxian") + [
                "--data-dir",
                str(self.data_dir),
                "--live",
            ]
            label = "招贤"
        else:
            command = task_base_command("qiluo") + [
                "--data-dir",
                str(self.data_dir),
                "--live",
            ]
            label = "绮罗锦衣"

        ca_file = self.ca_file_var.get().strip()
        if ca_file:
            command.extend(["--ca-file", ca_file])
        self._start_process(label, command)

    def _start_process(self, label: str, command: list[str]) -> None:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_label = {
            "每日福利": "daily",
            "四艺星盘": "star",
            "招贤": "zhaoxian",
            "绮罗锦衣": "qiluo",
        }[label]
        self.active_task = safe_label
        if safe_label == "daily":
            self.daily_result_data = None
            self.daily_last_error = ""
            self._set_daily_result(
                "正在请求接口",
                "正在登录并提交每日福利领奖请求…",
                COLORS["accent"],
            )
        elif safe_label == "zhaoxian":
            self.zhaoxian_result_data = None
            self.zhaoxian_last_error = ""
            self._set_zhaoxian_result(
                "正在招贤",
                "正在登录并提交招贤十连请求…",
                COLORS["accent"],
            )
        elif safe_label == "qiluo":
            self.qiluo_result_data = None
            self.qiluo_last_error = ""
            self._set_qiluo_result(
                "正在直购",
                "正在登录并提交绮罗锦衣特惠直购请求…",
                COLORS["accent"],
            )
        log_path = LOGS_DIR / ("%s_%s.log" % (safe_label, stamp))
        try:
            self.runner.start(
                command=command,
                cwd=PROJECT_ROOT,
                log_path=log_path,
                label=label,
            )
        except Exception as exc:
            self._set_status("启动失败", COLORS["danger"])
            self._log_event(
                "[失败] 无法启动任务：%s" % exc,
                logging.ERROR,
            )
            messagebox.showerror("启动失败", str(exc), parent=self)
            return
        self.busy = True
        self._set_status("%s运行中" % label, COLORS["accent"])
        self._log_event(
            "[命令] %s" % " ".join(self._quote_command(command)),
        )
        self._log_event("[日志] %s" % log_path)
        self._refresh_controls()

    def _quote_command(self, command: list[str]) -> list[str]:
        result = []
        for item in command:
            result.append('"%s"' % item if " " in item else item)
        return result

    def _poll_events(self) -> None:
        while True:
            try:
                kind, payload = self.runner.events.get_nowait()
            except queue.Empty:
                break
            if kind == "started":
                self._log_event("[开始] %s任务" % payload)
            elif kind == "output":
                self._handle_process_output(str(payload))
            elif kind == "error":
                self._log_event(
                    "[错误] %s" % payload,
                    logging.ERROR,
                )
            elif kind == "exit":
                self._handle_process_exit(int(payload))

        while True:
            try:
                kind, payload = self.ui_events.get_nowait()
            except queue.Empty:
                break
            if kind == "import_ready" and isinstance(
                payload,
                SessionImportResult,
            ):
                self._handle_import_ready(payload)
            elif kind == "import_error":
                self._handle_import_error(str(payload))

        self.after(120, self._poll_events)

    def _handle_process_exit(self, return_code: int) -> None:
        self.busy = False
        finished_task = self.active_task
        if return_code == 0:
            self._set_status("已完成", COLORS["success"])
            self._log_event("[完成] 任务成功结束")
            self._load_gt_status()
        else:
            self._set_status("任务失败", COLORS["danger"])
            self._log_event(
                "[失败] 任务结束，退出码=%d" % return_code,
                logging.ERROR,
            )
            if finished_task == "daily":
                message = self.daily_last_error or "任务退出码：%d" % return_code
                self._set_daily_result("领取失败", message, COLORS["danger"])
            elif finished_task == "zhaoxian":
                message = (
                    self.zhaoxian_last_error
                    or "招贤任务退出码：%d" % return_code
                )
                self._set_zhaoxian_result(
                    "招贤失败",
                    message,
                    COLORS["danger"],
                )
            elif finished_task == "qiluo":
                message = (
                    self.qiluo_last_error
                    or "绮罗锦衣任务退出码：%d" % return_code
                )
                self._set_qiluo_result(
                    "直购失败",
                    message,
                    COLORS["danger"],
                )
        self.active_task = None
        self._refresh_controls()

    def _handle_process_output(self, line: str) -> None:
        level = logging.INFO
        if any(word in line for word in ("[失败]", "Traceback", "Error")):
            level = logging.ERROR
        elif any(word in line for word in ("[提示]", "[警告]", "可能")):
            level = logging.WARNING
        self._log_event(line, level)

        if self.active_task == "qiluo":
            if line.startswith("[失败]"):
                self.qiluo_last_error = format_user_error(
                    RuntimeError(line.removeprefix("[失败]").strip())
                )
                self._set_qiluo_result(
                    "直购失败",
                    self.qiluo_last_error,
                    COLORS["danger"],
                )
            elif line.startswith("[绮罗锦衣结果]"):
                try:
                    data = json.loads(
                        line.removeprefix("[绮罗锦衣结果]").strip()
                    )
                except json.JSONDecodeError:
                    return
                if isinstance(data, dict):
                    self.qiluo_result_data = data
                    self._show_qiluo_data(data)
            return

        if self.active_task == "zhaoxian":
            if line.startswith("[失败]"):
                self.zhaoxian_last_error = format_user_error(
                    RuntimeError(line.removeprefix("[失败]").strip())
                )
                self._set_zhaoxian_result(
                    "招贤失败",
                    self.zhaoxian_last_error,
                    COLORS["danger"],
                )
            elif line.startswith("[招贤结果]"):
                try:
                    data = json.loads(
                        line.removeprefix("[招贤结果]").strip()
                    )
                except json.JSONDecodeError:
                    return
                if isinstance(data, dict):
                    self.zhaoxian_result_data = data
                    self._show_zhaoxian_data(data)
            return

        if self.active_task != "daily":
            return
        if line.startswith("[失败]"):
            raw_error = line.removeprefix("[失败]").strip()
            self.daily_last_error = format_user_error(RuntimeError(raw_error))
            self._set_daily_result(
                "领取失败",
                self.daily_last_error,
                COLORS["danger"],
            )
            return
        match = DAILY_RESULT_PATTERN.search(line)
        if match:
            self.daily_result_data = (
                match.group("reward"),
                match.group("status"),
                match.group("server_time"),
            )
            self._set_daily_result(
                "领取成功",
                "奖励：%s\n状态：%s"
                % self.daily_result_data[:2],
                COLORS["success"],
            )
            return
        if line.startswith("[领奖]"):
            self._set_daily_result(
                "正在提交领奖请求",
                "已连接服务器，等待返回奖励结果…",
                COLORS["accent"],
            )
        elif line.startswith("[完成]"):
            self._set_daily_result(
                "任务完成",
                line.removeprefix("[完成]").strip(),
                COLORS["success"],
            )

    def _set_daily_result(self, status: str, detail: str, color: str) -> None:
        self.daily_result_status.configure(text=status, fg=color)
        self.daily_result_detail.configure(text=detail)

    def _set_zhaoxian_result(
        self,
        status: str,
        detail: str,
        color: str,
    ) -> None:
        self.zhaoxian_result_status.configure(text=status, fg=color)
        self.zhaoxian_result_detail.configure(text=detail)

    def _show_zhaoxian_results(
        self,
        results: list[str],
        prefix: str = "招贤完成",
    ) -> None:
        self._set_zhaoxian_result(
            "%s · %d 项" % (prefix, len(results)),
            "、".join(results),
            COLORS["success"],
        )

    def _show_zhaoxian_data(self, data: Dict[str, Any]) -> None:
        results = data.get("results")
        if isinstance(results, list):
            self._show_zhaoxian_results(
                [str(item) for item in results],
            )

    def _set_qiluo_result(
        self,
        status: str,
        detail: str,
        color: str,
    ) -> None:
        self.qiluo_result_status.configure(text=status, fg=color)
        self.qiluo_result_detail.configure(text=detail)

    def _show_qiluo_results(
        self,
        rewards: list[str],
        prefix: str = "绮罗锦衣完成",
    ) -> None:
        self._set_qiluo_result(
            "%s · %d 项奖励" % (prefix, len(rewards)),
            "、".join(rewards),
            COLORS["success"],
        )

    def _show_qiluo_data(self, data: Dict[str, Any]) -> None:
        rewards = data.get("rewards")
        if isinstance(rewards, list):
            self._show_qiluo_results(
                [str(item) for item in rewards],
            )

    def _log_event(
        self,
        message: str,
        level: int = logging.INFO,
    ) -> None:
        self.logger.log(level, message)

    def _set_status(self, text: str, color: str) -> None:
        self._current_status_text = text
        self._current_status_color = color
        self.status_label.configure(text=text, fg=color)
        self.status_dot.configure(fg=color)

    def _clear_configuration(self) -> None:
        if self.busy or self.importing:
            return
        if not messagebox.askyesno(
            "一键清除配置",
            "将删除 account.json、task/ 下的任务文件、"
            "zhaoxian.json 和 qiluo_jingyi.json。\n\n"
            "HAR、日志和代理证书不会被删除。\n"
            "清除后需要重新导入 HAR。是否继续？",
            parent=self,
        ):
            return
        try:
            removed = clear_configuration(self.data_dir)
        except Exception as exc:
            self._set_status("清除失败", COLORS["danger"])
            self._log_event(
                "[失败] 清除配置失败：%s" % exc,
                logging.ERROR,
            )
            messagebox.showerror("清除失败", str(exc), parent=self)
            return
        self.session_meta = None
        self.daily_result_data = None
        self.daily_last_error = ""
        self.zhaoxian_result_data = None
        self.zhaoxian_last_error = ""
        self.qiluo_result_data = None
        self.qiluo_last_error = ""
        self._apply_missing_state()
        self._set_status("配置已清除", COLORS["success"])
        self._log_event(
            "[配置] 已清除 %d 个账号/任务文件" % len(removed)
        )
        self._refresh_controls()

    def _open_settings(self) -> None:
        dialog = tk.Toplevel(self)
        dialog.title("设置")
        dialog.configure(bg=COLORS["bg"])
        dialog.geometry("650x285")
        dialog.resizable(False, False)
        dialog.transient(self)
        dialog.grab_set()

        body = tk.Frame(
            dialog,
            bg=COLORS["card"],
            highlightbackground=COLORS["border"],
            highlightthickness=1,
            padx=22,
            pady=20,
        )
        body.pack(fill="both", expand=True, padx=18, pady=18)
        body.grid_columnconfigure(0, weight=1)

        tk.Label(
            body,
            text="网络证书",
            bg=COLORS["card"],
            fg=COLORS["text"],
            font=self.fonts["section"],
        ).grid(row=0, column=0, columnspan=3, sticky="w")
        tk.Label(
            body,
            text=(
                "仅在 Proxyman、Charles 等代理软件开启时需要选择其 CA 证书；"
                "关闭代理后可以留空。"
            ),
            bg=COLORS["card"],
            fg=COLORS["muted"],
            justify="left",
            wraplength=580,
            font=self.fonts["small"],
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(5, 16))

        ca_value = tk.StringVar(value=self.ca_file_var.get())
        entry = ttk.Entry(body, textvariable=ca_value)
        entry.grid(row=2, column=0, sticky="ew", padx=(0, 9))

        def browse() -> None:
            selected = filedialog.askopenfilename(
                parent=dialog,
                title="选择代理 CA 证书",
                filetypes=[
                    ("PEM 证书", "*.pem"),
                    ("证书文件", "*.crt *.cer"),
                    ("所有文件", "*"),
                ],
            )
            if selected:
                ca_value.set(selected)

        def autodetect() -> None:
            detected = detect_proxy_ca()
            if detected is None:
                messagebox.showinfo(
                    "未找到证书",
                    "没有检测到常见代理软件的 CA 证书，请手动选择。",
                    parent=dialog,
                )
                return
            ca_value.set(str(detected))

        ttk.Button(
            body,
            text="选择",
            style="Secondary.TButton",
            command=browse,
        ).grid(row=2, column=1, padx=(0, 8))
        ttk.Button(
            body,
            text="自动检测",
            style="Secondary.TButton",
            command=autodetect,
        ).grid(row=2, column=2)

        actions = tk.Frame(body, bg=COLORS["card"])
        actions.grid(row=3, column=0, columnspan=3, sticky="e", pady=(24, 0))

        def save() -> None:
            selected = ca_value.get().strip()
            if selected and not Path(selected).is_file():
                messagebox.showerror(
                    "证书不存在",
                    "CA 证书文件不存在：\n%s" % selected,
                    parent=dialog,
                )
                return
            self.ca_file_var.set(selected)
            self._save_settings()
            self._set_status("设置已保存", COLORS["success"])
            self._log_event(
                "[设置] 代理 CA：%s" % (selected or "未配置")
            )
            dialog.destroy()

        ttk.Button(
            actions,
            text="取消",
            style="Secondary.TButton",
            command=dialog.destroy,
        ).pack(side="left", padx=(0, 8))
        ttk.Button(
            actions,
            text="保存",
            style="Primary.TButton",
            command=save,
        ).pack(side="left")

    def _toggle_theme(self) -> None:
        if self.busy or self.importing:
            return
        self.theme_name = "light" if self.theme_name == "dark" else "dark"
        set_theme(self.theme_name)
        configure_theme(self)
        self.configure(bg=COLORS["bg"])
        har_value = self.har_var.get()
        star_claim = self.star_claim_var.get()
        for child in self.winfo_children():
            child.destroy()
        self._build_layout()
        if har_value:
            self.har_var.set(har_value)
        self.star_claim_var.set(star_claim)
        self._load_gt_status()
        self._set_status(
            getattr(self, "_current_status_text", "就绪"),
            getattr(self, "_current_status_color", COLORS["muted"]),
        )
        self._refresh_controls()
        self._save_settings()
        self._log_event("[界面] 已切换为%s主题" % ("浅色" if self.theme_name == "light" else "深色"))

    def _stop_task(self) -> None:
        if not self.runner.is_running:
            return
        if messagebox.askyesno(
            "停止任务",
            "确定要停止当前任务吗？",
            parent=self,
        ):
            self.runner.stop()
            self._set_status("正在停止", COLORS["warning"])
            self._log_event(
                "[停止] 已请求停止任务",
                logging.WARNING,
            )

    def _refresh_controls(self) -> None:
        busy = self.busy or self.importing
        config_exists = (self.data_dir / "account.json").exists() or any(
            path.exists() for path in (self.data_dir / "task").glob("*.json")
        )
        has_session = (
            (self.data_dir / "account.json").exists()
            and self.session_meta is not None
        )
        star_ready = bool(has_session and self.session_meta.star_ready)
        zhaoxian_ready = bool(
            has_session and self.session_meta.zhaoxian_ready
        )
        qiluo_ready = bool(
            has_session and self.session_meta.qiluo_jingyi_ready
        )

        import_state = ["disabled"] if busy else ["!disabled"]
        self.import_button.state(import_state)
        self.browse_button.state(import_state)
        self.theme_button.state(import_state)
        self.settings_button.state(import_state)
        self.clear_config_button.state(
            ["!disabled"] if config_exists and not busy else ["disabled"]
        )
        daily_ready = bool(has_session and self.session_meta.daily_ready)
        daily_state = ["!disabled"] if daily_ready and not busy else ["disabled"]
        self.daily_run_button.state(daily_state)
        star_state = ["!disabled"] if star_ready and not busy else ["disabled"]
        self.star_run_button.state(star_state)
        zhaoxian_state = (
            ["!disabled"] if zhaoxian_ready and not busy else ["disabled"]
        )
        self.zhaoxian_run_button.state(zhaoxian_state)
        qiluo_state = (
            ["!disabled"] if qiluo_ready and not busy else ["disabled"]
        )
        self.qiluo_run_button.state(qiluo_state)
        self.stop_button.state(
            ["!disabled"] if self.runner.is_running else ["disabled"]
        )

    def _on_close(self) -> None:
        if self.runner.is_running:
            if not messagebox.askyesno(
                "退出程序",
                "任务仍在运行，退出将停止任务。是否继续？",
                parent=self,
            ):
                return
            self.runner.stop()
        self._save_settings()
        self.destroy()
