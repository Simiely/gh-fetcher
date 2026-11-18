# AGENTS.md · 项目规则

> 📌 **文档基线**：2026-10-08（v1.0.0 首发）完成四件套
> **更新文档/代码后，请更新此行**（日期 + 新 tag），并在 CHANGELOG 追加版本

## 技术栈

- Python 3.14.6 + tkinter（**纯标准库，零第三方依赖**——硬约定）
- 打包：PyInstaller `--onefile --noconsole`，必须用带 tkinter 的解释器构建
- 目标环境：Windows 10/11，DPI 缩放 100%~200%

## 关键坑（每条都实测踩过）

- **DPI 感知必须在 `import tkinter` 之前声明**，否则高分屏整窗位图拉伸（文字发虚+拖动掉帧）
- **contents raw 接口对启用了 eol=crlf 的仓库会做换行转换**：下载字节 ≠ 树元数据 size（实测 94249≠67124）。源码文件精确下载必须走 **Git Blobs API**（按 sha 取 base64 解码），大小校验才能对上
- **镜像/直连都可能"静默断流"**：连接中断时 `read()` 正常返回已收字节而不抛错——必须校验 `got == Content-Length`，不符即清理半成品换下一条路由
- **threading.Thread 的 args 与函数签名参数数不匹配 → TypeError 被守护线程静默吞掉**，界面毫无反应。改函数签名时必须同步检查所有调用点（曾因多传一个 path 参数导致双击下载从 v1 起就静默失效）
- **ctypes 取错误码用 `WinDLL(use_last_error=True)` + `ctypes.get_last_error()`**；`windll.kernel32.GetLastError()` 会被 ctypes 内部调用重置
- **公共镜像（ghproxy 家族）有匿名限流（403）**，且可用性随时变——镜像列表要可配置，token 绝不发公共镜像
- **lambda 捕获循环变量必须默认参数绑定**（`lambda ip=ip:`）

## 约定

- UI 标签只写名词，不放操作方式说明（操作靠按钮自解释）
- 界面深色主题（#1e1f24 系）；单实例互斥（CreateMutexW）
- token 仅存内存不落盘；下载默认存系统下载目录（SHGetKnownFolderPath 自适应）

## 机器层（repo-kit · link-only）

- `schema/taxonomy.json` + `schema/kit.lock.json`（kit `2.9.0` / 规范 `2.1.4` / `link-only`）+ `tools/repo-kit/check_repo.py`
- 校验：`python tools/repo-kit/check_repo.py --root .`；锁哈希：`pwsh tools/update-repo-kit.ps1 -Check`
- 只查**链接与锚点**：全仓 md 豁免 frontmatter（本仓没有 frontmatter 契约）
- `schema/kit.lock.json` **不要手改**，升级只能跑 `tools/update-repo-kit.ps1`

## 常用命令

- 构建：`pyinstaller --onefile --noconsole --name gh-fetcher gh-fetcher.py`
- 渲染验证：PrintWindow API 直接从窗口渲染缓冲截图（用户在别的虚拟桌面时 ImageGrab 只能抓到壁纸）
- 冒烟：无 GUI 直接调 `api_get / download_via_mirror / smart_download`

## 详细规则（按需 @引用）

- @DEVELOPMENT.md（架构 + 问题记录）
