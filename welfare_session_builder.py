#!/usr/bin/env python3
"""旧文件名兼容入口：从 HAR 生成账号与任务配置。

该工具只处理本地 HAR，不连接游戏服务器。可以命令行运行，也可以
不带参数启动简单的图形界面：

    python3 gt_session_builder.py
    python3 gt_session_builder.py --har friend.har
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from gt_automation.paths import DEFAULT_DATA_DIR
from gt_automation.session_store import (
    SessionFileError,
    extract_gt_session,
    load_gt_session,
    save_import,
)
from siyi_star_auto import HarFormatError, ProtocolError


def build_session(
    har_path: Path,
    output_dir: Path,
    overwrite: bool = False,
) -> None:
    if not har_path.exists():
        raise FileNotFoundError("HAR 不存在：%s" % har_path)
    if har_path.suffix.lower() != ".har":
        raise ValueError("请选择 .har 文件：%s" % har_path)
    existing = (output_dir / "account.json").exists() or (
        output_dir / "task"
    ).exists()
    if existing and not overwrite:
        raise FileExistsError(
            "输出目录已有账号或任务配置，避免误覆盖：%s"
            "（如确认覆盖请加 --force）" % output_dir
        )

    print("[解析] %s" % har_path)
    result = extract_gt_session(har_path)
    metadata = save_import(
        result.session,
        output_dir,
        result.metadata,
        replace=True,
    )

    # 立即重新读取，确认每日福利任务文件可加载。
    loaded_session, _loaded_metadata = load_gt_session(output_dir)
    if loaded_session.context_id != result.session.context_id:
        raise HarFormatError("生成后的缓存校验失败：上下文 ID 不一致")

    print("[完成] %s" % output_dir)
    print("[账号文件] %s" % (output_dir / "account.json"))
    print("[任务目录] %s" % (output_dir / "task"))
    print("[会话] %s" % result.session.websocket_url)
    print("[上下文] field8=%d" % result.session.context_id)
    print("[初始化帧] %d" % len(result.session.init_frames))
    print(
        "[能力] 每日福利%s"
        % (" + 四艺星盘" if metadata.star_ready else "")
    )
    print("[权限] 0600")
    sample = metadata.last_reward
    if sample is None:
        print("[样本] 该 HAR 中没有命令 5960 的历史领奖响应")
    else:
        print(
            "[样本] reward_id=%s status=%s"
            % (sample.get("reward_id"), sample.get("status"))
        )
    print("[提示] account.json 和 task/ 含登录凭证，不要公开分享。")


def run_gui() -> int:
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox
    except ImportError:
        print(
            "当前 Python 没有 tkinter，请改用命令行：\n"
            "python3 gt_session_builder.py --har <HAR文件>",
            file=sys.stderr,
        )
        return 2

    output_path = DEFAULT_DATA_DIR
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        print("无法启动图形界面：%s" % exc, file=sys.stderr)
        return 2

    root.title("生成账号与任务配置")
    root.geometry("620x320")
    root.resizable(False, False)

    frame = tk.Frame(root, padx=24, pady=22)
    frame.pack(fill="both", expand=True)

    title = tk.Label(
        frame,
        text="从朋友自己的 HAR 生成账号与任务配置",
        font=("Helvetica", 17, "bold"),
    )
    title.pack(anchor="w")

    description = (
        "本工具不会生成或猜测账号登录凭证。\n"
        "需要先通过抓包工具完成一次当前版本的微信小程序登录，"
        "并由你选择导出的 HAR 文件。\n\n"
        "生成后，把 account.json 和 task/ 放在程序目录，"
        "以后运行时不再需要 HAR。"
    )
    tk.Label(
        frame,
        text=description,
        justify="left",
        wraplength=560,
        pady=14,
    ).pack(anchor="w")

    output_label = tk.Label(
        frame,
        text="输出：%s" % output_path,
        justify="left",
        wraplength=560,
    )
    output_label.pack(anchor="w", pady=(0, 12))

    status = tk.Label(frame, text="等待选择 HAR 文件", fg="#555555")
    status.pack(anchor="w", pady=(0, 12))

    def choose_and_build() -> None:
        selected = filedialog.askopenfilename(
            parent=root,
            title="选择 Proxyman 或其他工具导出的 HAR",
            filetypes=[
                ("HAR 文件", "*.har"),
                ("所有文件", "*"),
            ],
        )
        if not selected:
            return
        har_path = Path(selected)
        overwrite = False
        existing_config = (output_path / "account.json").exists() or any(
            (output_path / "task").glob("*.json")
        )
        if existing_config:
            overwrite = messagebox.askyesno(
                "覆盖配置",
                "账号或任务配置已存在，是否覆盖？",
                parent=root,
            )
            if not overwrite:
                return

        status.config(text="正在解析 HAR，请稍候……")
        root.update_idletasks()
        try:
            build_session(har_path, output_path, overwrite=overwrite)
        except (
            OSError,
            ValueError,
            HarFormatError,
            ProtocolError,
            SessionFileError,
        ) as exc:
            status.config(text="生成失败")
            messagebox.showerror("生成失败", str(exc), parent=root)
            return

        status.config(text="生成成功")
        messagebox.showinfo(
            "生成成功",
            "已生成：\n%s\n\n请勿把该文件发送给其他人。"
            % output_path,
            parent=root,
        )

    tk.Button(
        frame,
        text="选择 HAR 并生成",
        command=choose_and_build,
        width=22,
        height=2,
    ).pack(anchor="w")

    root.mainloop()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="从朋友的 HAR 生成账号与任务配置"
    )
    parser.add_argument("--har", type=Path, help="输入 HAR 文件")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help="account.json 与 task/ 的输出目录",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="允许覆盖已存在的输出文件",
    )
    parser.add_argument(
        "--gui",
        action="store_true",
        help="强制启动图形界面",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.gui or args.har is None:
        return run_gui()
    try:
        build_session(args.har, args.data_dir, overwrite=args.force)
    except (
        OSError,
        ValueError,
        HarFormatError,
        ProtocolError,
        SessionFileError,
    ) as exc:
        print("生成失败：%s" % exc, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
