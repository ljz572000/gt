#!/usr/bin/env python3
"""Build a one-click macOS or Windows desktop distribution."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
BUILD_ROOT = ROOT / "build" / "desktop"
DIST_ROOT = ROOT / "dist"
APP_NAME = "GT Automation Studio"


def configure_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def run(command: list[str]) -> None:
    print("[build]", " ".join(command))
    subprocess.run(command, cwd=ROOT, check=True)


def pyinstaller(*args: str) -> None:
    run([sys.executable, "-m", "PyInstaller", *args])


def platform_key() -> str:
    if sys.platform == "darwin":
        return "macOS"
    if sys.platform == "win32":
        return "Windows"
    raise RuntimeError("仅支持在 macOS 或 Windows 上构建")


def reset_directory(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def build_worker(name: str, script: Path, output: Path) -> Path:
    pyinstaller(
        "--noconfirm",
        "--clean",
        "--onefile",
        "--console",
        "--name",
        name,
        "--distpath",
        str(output),
        "--workpath",
        str(BUILD_ROOT / "workers" / name),
        "--specpath",
        str(BUILD_ROOT / "spec" / name),
        str(script),
    )
    executable = output / (name + (".exe" if sys.platform == "win32" else ""))
    if not executable.exists():
        raise FileNotFoundError("任务执行器构建失败：%s" % executable)
    return executable


def build_gui(worker_dir: Path, output: Path) -> Path:
    daily = worker_dir / (
        "daily_worker.exe" if sys.platform == "win32" else "daily_worker"
    )
    star = worker_dir / (
        "star_worker.exe" if sys.platform == "win32" else "star_worker"
    )
    zhaoxian = worker_dir / (
        "zhaoxian_worker.exe"
        if sys.platform == "win32"
        else "zhaoxian_worker"
    )
    qiluo = worker_dir / (
        "qiluo_jingyi_worker.exe"
        if sys.platform == "win32"
        else "qiluo_jingyi_worker"
    )
    for helper in (daily, star, zhaoxian, qiluo):
        if not helper.exists():
            raise FileNotFoundError(helper)

    pyinstaller(
        "--noconfirm",
        "--clean",
        "--windowed",
        "--onedir",
        "--name",
        APP_NAME,
        "--hidden-import",
        "daily_welfare_claim",
        "--hidden-import",
        "siyi_star_auto",
        "--hidden-import",
        "zhaoxian_auto",
        "--hidden-import",
        "qiluo_jingyi_auto",
        "--add-binary",
        "%s%sbin" % (daily, os.pathsep),
        "--add-binary",
        "%s%sbin" % (star, os.pathsep),
        "--add-binary",
        "%s%sbin" % (zhaoxian, os.pathsep),
        "--add-binary",
        "%s%sbin" % (qiluo, os.pathsep),
        "--distpath",
        str(output),
        "--workpath",
        str(BUILD_ROOT / "gui"),
        "--specpath",
        str(BUILD_ROOT / "spec" / "gui"),
        str(ROOT / "gt_studio.py"),
    )

    if sys.platform == "darwin":
        app = output / (APP_NAME + ".app")
        if not app.exists():
            raise FileNotFoundError("macOS App 构建失败：%s" % app)
        locate_macos_helpers(app)
        run(["codesign", "--force", "--deep", "--sign", "-", str(app)])
        run([str(app / "Contents" / "MacOS" / APP_NAME), "--package-check"])
        return app

    executable = output / APP_NAME / (APP_NAME + ".exe")
    if not executable.exists():
        raise FileNotFoundError("Windows EXE 构建失败：%s" % executable)
    locate_windows_helpers(output / APP_NAME)
    run([str(executable), "--package-check"])
    return output / APP_NAME


def locate_macos_helpers(app: Path) -> None:
    for name in (
        "daily_worker",
        "star_worker",
        "zhaoxian_worker",
        "qiluo_jingyi_worker",
    ):
        expected = [
            app / "Contents" / "Frameworks" / "bin" / name,
            app / "Contents" / "Resources" / "bin" / name,
            app / "Contents" / "MacOS" / "bin" / name,
        ]
        if not any(path.exists() for path in expected):
            raise FileNotFoundError(
                "找不到打包后的任务执行器：%s" % name
            )


def locate_windows_helpers(package_dir: Path) -> None:
    for name in (
        "daily_worker.exe",
        "star_worker.exe",
        "zhaoxian_worker.exe",
        "qiluo_jingyi_worker.exe",
    ):
        expected = [
            package_dir / "_internal" / "bin" / name,
            package_dir / "bin" / name,
        ]
        if not any(path.exists() for path in expected):
            raise FileNotFoundError(
                "找不到打包后的任务执行器：%s" % name
            )


def distribution_readme() -> str:
    return """GT Automation Studio

1. 运行程序：
   macOS: 双击 GT Automation Studio.app
   Windows: 双击 GT Automation Studio.exe

2. 在界面中选择当前账号的 HAR，点击“生成配置”。
3. 以后启动程序即可运行每日福利、四艺星盘、招贤和绮罗锦衣任务。
4. account.json、task/、zhaoxian.json、qiluo_jingyi.json 和 logs
   会保存在当前解压目录。

账号数据目录：
   macOS: ~/Library/Application Support/GT Automation Studio
   Windows: %APPDATA%\\GT Automation Studio

注意：
- 账号、任务和招贤/绮罗锦衣配置含有令牌，不要分享。
- macOS 未公证版本首次运行可能需要右键选择“打开”。
- 四艺星盘要求 HAR 包含命令 6589 的活动状态。
"""


def package_zip(built: Path) -> Path:
    platform_name = platform_key()
    architecture = platform.machine()
    archive_name = "GT-Automation-Studio-%s-%s.zip" % (
        platform_name,
        architecture,
    )
    archive = DIST_ROOT / archive_name
    if archive.exists():
        archive.unlink()

    if sys.platform == "darwin":
        staging = BUILD_ROOT / "package" / "macOS"
        reset_directory(staging)
        package_dir = staging / APP_NAME
        package_dir.mkdir()
        shutil.copytree(built, package_dir / built.name, symlinks=True)
        (package_dir / "README.txt").write_text(
            distribution_readme(),
            encoding="utf-8",
        )
        run(
            [
                "ditto",
                "-c",
                "-k",
                "--sequesterRsrc",
                "--keepParent",
                str(package_dir),
                str(archive),
            ]
        )
    else:
        package_dir = built
        (package_dir / "README.txt").write_text(
            distribution_readme(),
            encoding="utf-8",
        )
        with zipfile.ZipFile(
            archive,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
        ) as handle:
            for path in package_dir.rglob("*"):
                handle.write(path, path.relative_to(package_dir.parent))

    return archive


def main() -> int:
    configure_console()
    platform_key()
    reset_directory(BUILD_ROOT)
    DIST_ROOT.mkdir(parents=True, exist_ok=True)
    worker_output = BUILD_ROOT / "workers" / "dist"
    worker_output.mkdir(parents=True, exist_ok=True)
    gui_output = DIST_ROOT / "desktop"
    reset_directory(gui_output)

    build_worker("daily_worker", ROOT / "daily_welfare_claim.py", worker_output)
    build_worker("star_worker", ROOT / "siyi_star_auto.py", worker_output)
    build_worker("zhaoxian_worker", ROOT / "zhaoxian_auto.py", worker_output)
    build_worker(
        "qiluo_jingyi_worker",
        ROOT / "qiluo_jingyi_auto.py",
        worker_output,
    )
    built = build_gui(worker_output, gui_output)
    archive = package_zip(built)
    print("[完成] %s" % archive)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
