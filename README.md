# GT Automation Studio

跨 macOS / Windows 的桌面控制台，用于：

- 从 HAR 生成独立的账号与任务配置；
- 从 `task/daily_welfare.json` 运行每日福利任务；
- 从 `task/siyi_star.json` 运行四艺星盘任务；
- 从 `zhaoxian.json` 运行招贤十连任务；
- 从 `qiluo_jingyi.json` 运行绮罗锦衣特惠直购；
- 默认使用浅色主题，支持浅色 / 深色切换并记忆选择；
- 将运行日志保存到解压目录下的 `logs/` 文件夹。

## 环境要求

- Python 3.10+
- Tkinter（官方 Python 安装通常自带）
- `websockets==12.0`

## 安装

```bash
python -m pip install -r requirements.txt
```

## 启动桌面程序

macOS：

```bash
python3 gt_studio.py
```

也可以双击 `launch_macos.command`。

Windows：

```powershell
python gt_studio.py
```

也可以双击 `launch_windows.bat`。

也可以安装为本地包后使用：

```bash
python -m pip install -e .
gt-studio
```

或直接运行模块：

```bash
python -m gt_automation
```

## 构建一键运行压缩包

PyInstaller 需要在目标操作系统上构建：

```bash
python -m pip install -r requirements.txt -r requirements-build.txt
python packaging/build_desktop.py
```

生成的压缩包位于 `dist/`：

```text
GT-Automation-Studio-macOS-<arch>.zip
GT-Automation-Studio-Windows-<arch>.zip
```

macOS 用户解压后双击 `.app`；Windows 用户解压后双击 `.exe`。
详细说明见 `packaging/README.md`。

打包版本的用户数据目录：

```text
macOS:   ~/Library/Application Support/GT Automation Studio
Windows: %APPDATA%\GT Automation Studio
```

`account.json`、`task/` 和日志保存在解压目录；HAR 与本机设置保存在
用户数据目录，运行数据不写入 `.app` 或 `.exe` 内部。

## 使用流程

1. 打开桌面程序；
2. 选择当前版本的 HAR；
3. 点击“生成配置”；
4. 以后每日福利和四艺星盘任务分别读取 `task/` 下的独立任务文件。

会话配置区域提供“一键清除配置”，只删除 `account.json`、任务文件、
`zhaoxian.json` 和 `qiluo_jingyi.json`，
不会删除 HAR、日志或代理证书。

没有 `account.json` 或对应任务文件时，桌面程序会禁用该任务的运行按钮。

已有账号与任务配置时，导入包含四艺星盘的 HAR 会自动只更新
`task/siyi_star.json`，完整保留每日福利会话、初始化帧和最近奖励。
界面会直接提示本次导入了哪些任务。

## 命令行入口

```bash
python daily_welfare_claim.py --data-dir . --dry-run
python daily_welfare_claim.py --data-dir . --live --confirm-ad-watched

python siyi_star_auto.py --data-dir . --dry-run
python siyi_star_auto.py --data-dir . --live

python zhaoxian_auto.py --data-dir . --dry-run
python zhaoxian_auto.py --data-dir . --live

python qiluo_jingyi_auto.py --data-dir . --dry-run
python qiluo_jingyi_auto.py --data-dir . --live
```

四艺星盘要求导入的 HAR 中包含命令 `6589` 的活动状态。仅包含启动数据的
HAR 可以生成每日福利任务文件，但缺少命令 `6589` 时不会生成星盘任务文件。

## 项目结构

```text
gt_automation/
  app.py                 # 应用入口
  paths.py               # 跨平台路径
  logging_setup.py       # 日志配置
  process_runner.py      # 子进程与实时日志
  session_store.py       # HAR / 拆分账号与任务存储
  ui/
    main_window.py       # 桌面界面
    theme.py             # 主题与样式
gt_studio.py             # 桌面启动器
daily_welfare_claim.py   # 每日福利 CLI
siyi_star_auto.py        # 四艺星盘 CLI
account.json             # 账号信息
task/daily_welfare.json  # 每日福利会话与最近奖励
task/siyi_star.json      # 四艺星盘会话与活动状态
zhaoxian.json            # 招贤活动、请求 payload 和上次结果
qiluo_jingyi.json        # 绮罗锦衣活动、商品和上次奖励
data/har/                # HAR 抓包文件
docs/                    # 使用说明与分析报告
runtime/                 # 本机设置
logs/                    # 应用与任务日志
```

## 安全说明

账号与任务文件包含登录令牌、设备信息和账号会话，权限固定为 `0600`。
不要公开分享、提交到代码仓库或发送给其他人。任务执行前请确认账号授权、
游戏服务条款和可能产生的账号风险。
