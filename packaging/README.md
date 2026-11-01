# 桌面打包

PyInstaller 不支持跨操作系统交叉打包。要生成 Windows 和 macOS 两个
压缩包，需要分别在对应系统运行构建，或使用 GitHub Actions。

## 本机构建

```bash
python -m pip install -r requirements.txt -r requirements-build.txt
python packaging/build_desktop.py
```

输出：

```text
dist/GT-Automation-Studio-macOS-<arch>.zip
dist/GT-Automation-Studio-Windows-<arch>.zip
```

每次只会生成当前操作系统的压缩包。

也可以使用 `.github/workflows/build-desktop.yml` 在 GitHub Actions 中
分别由 macOS 和 Windows 原生 runner 构建两个平台的压缩包。

## 发布内容

- macOS：包含 `.app` 和中文使用说明；
- Windows：包含 GUI `.exe`、依赖文件和中文使用说明；
- GUI 内包含每日福利、四艺星盘、招贤与绮罗锦衣任务执行器；
- 不包含账号配置、任务配置、HAR、日志或开发者本机配置。
- 用户生成后的 `account.json` 与 `task/` 保存在解压目录。
- 运行日志会写入解压目录下的 `logs/` 文件夹。

## macOS 签名

默认使用临时签名。未使用开发者证书公证的包首次打开时，可能需要在
Finder 中右键选择“打开”。正式分发建议配置 Apple Developer ID 和
notarization。
