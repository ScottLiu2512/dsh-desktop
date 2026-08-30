# DSH Desktop

DeepSeek Harness（DSH）桌面客户端（Windows）。把 DSH 的 Web 界面包装成原生桌面应用：一键启动/停止 `dsh web`，内嵌浏览器加载其完整界面，并提供图形化的模型/API 配置与日志查看，让你像用普通软件一样使用 DeepSeek Harness。

> 说明：本应用是 DeepSeek Harness 的**客户端外壳**，不包含 Harness 本体，运行前需要先安装 Node.js 和 `dsh`。
>
> 当前版本：v1.0.2 ｜ [Releases](https://github.com/ScottLiu2512/dsh-desktop/releases)

## 功能特性

- **一键启动/停止 dsh**：后台拉起 `dsh web`，自动解析访问地址并加载
- **内嵌浏览器**：直接使用 DSH 自带的完整 Web UI（多会话、历史记录、流式输出、工具调用与审批等）
- **图形化配置**：API Key、模型、推理强度、工作区目录、端口，无需手改配置文件
- **日志面板 + 状态栏**：实时查看 dsh 进程输出，便于排查问题

## 界面预览

![DSH Desktop 运行截图](https://github.com/ScottLiu2512/dsh-desktop/releases/latest/download/screenshot.png)

## 环境要求

- Windows 10 及以上
- [Node.js](https://nodejs.org/) 22 或更高版本
- [DeepSeek Harness（dsh）](https://github.com/deepseek-ai/deepseek-harness)

## 安装

### 方式一：安装包（推荐）

1. 前往 [Releases 页面](https://github.com/ScottLiu2512/dsh-desktop/releases/latest) 下载 `DSH-Desktop-Setup.exe`
2. 双击安装（需要管理员权限，会弹出 UAC 确认），默认安装到 `Program Files\DSH-Desktop`，自动创建开始菜单快捷方式，安装向导中可选创建桌面快捷方式
3. 安装完成后会检测 Node.js 和 dsh，缺失会弹出提示

### 方式二：免安装版

在 [Releases 页面](https://github.com/ScottLiu2512/dsh-desktop/releases/latest) 下载 `DSH-Desktop.exe`，双击即可运行，无需安装。

### 方式三：从源码运行

```bash
git clone https://github.com/ScottLiu2512/dsh-desktop.git
cd dsh-desktop
pip install -r requirements.txt
python main.py
```

依赖：Python 3.10+、PySide6、PyYAML（见 `requirements.txt`）。

## 使用

1. 打开后点左上角「设置」，填入 DeepSeek API Key（也可配置模型、推理强度、工作区目录和端口）
2. 点「启动 dsh」，等待状态栏显示运行地址，内嵌浏览器会加载出 DeepSeek Harness 界面
3. 后续操作（选择工作区、发送任务、审批工具调用等）都在界面内完成

## 打包（可选）

```bash
# 安装打包依赖（PyInstaller）
pip install pyinstaller

# 生成单文件 exe
python -m PyInstaller dsh_gui.spec --noconfirm --clean

# 生成安装包（需先安装 Inno Setup 6）
ISCC.exe dsh_client_setup.iss
```

## 联系方式

- 作者：梧井流曦
- 邮箱：744508955@qq.com

欢迎交流，共同研究。使用中遇到问题请在 [GitHub Issues](https://github.com/ScottLiu2512/dsh-desktop/issues) 反馈。
