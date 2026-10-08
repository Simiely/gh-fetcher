# DEVELOPMENT.md · 开发文档

## 项目概览

单文件 tkinter 应用（约 600 行），纯标准库。三段架构：

```
api.github.com（读取：文件树/Releases/源码 blobs/codeload 归档）
        ↓ build_jobs / run 逻辑
下载路由（smart_download）：
  镜像前缀(可选) → gh-proxy.com → ghproxy.net → 直连×2
  每条路由校验 expected_size，失败清理半成品换下一条
        ↓ root.after(0, ...) 渐进上屏
App：文件树 / Releases 勾选 / 进度（速度+ETA，EMA 平滑）
```

## 关键问题与方案

## 问题：Release 资产下载下来只有 1.45KB

**TL;DR**：把 API 资产端点传给了镜像链，且直连时没带 `Accept: octet-stream`——下到的是 JSON 元数据。

- 问题：批量下载"成功"但文件只有 1.45KB 打不开
- 根因：`_dl_asset` 传错 URL 形态（api.github.com 端点 ≠ 浏览器下载地址），两种端点需要不同的请求头和路由
- 解决：镜像链/直连统一改用 `browser_download_url`；带 token 的私有仓库走直连 302（跨域重定向自动剥离 Authorization）
- 预防：URL 形态是 API 集成的第一审查项

## 问题：双击下载文件毫无反应

**TL;DR**：`threading.Thread(args=...)` 与函数签名参数数不匹配，TypeError 被守护线程静默吞掉。

- 问题：文件树双击/按钮下载完全无反应，无报错
- 根因：调用点多传一个历史遗留参数（5 个），函数签名只收 4 个
- 解决：参数对齐；同时把静默路径显式化（状态栏透出每条路由成败）
- 预防：改函数签名时 grep 所有调用点；交付前用真 mainloop 的 E2E 驱动一遍全部按钮路径

## 问题：源码文件下载字节数与仓库不一致

**TL;DR**：contents raw 接口会按 .gitattributes 做 eol 转换（LF→CRLF），字节虚增 40%。

- 问题：树元数据说 67124B，下载得到 94249B，大小校验误报
- 根因：raw 媒体类型对文本文件应用了 eol 归一化
- 解决：改走 **Git Blobs API**（`GET /repos/{o}/{r}/git/blobs/{sha}`，base64 解码），拿到与树元数据完全一致的原始字节
- 预防：需要"精确字节"时用 blobs API，不用 contents raw

## 问题：下载到一半静默断流，留下半截文件

**TL;DR**：连接中断时 `read()` 正常返回已收字节而不抛错，必须显式校验 Content-Length。

- 问题：用户侧下载"不完整"，程序却报成功
- 根因：镜像/直连的 TCP 连接中途被掐，`read()` 循环正常结束
- 解决：`got < Content-Length` 立即抛错清理半成品；smart_download 内置 4 条路由回退 + 整链重试（30s 间隔）
- 附加：镜像可用性随时变（实测 ghproxy.net 一天内两次截断、gh-proxy.com 三次完整）——路由顺序要按实测动态排

## 问题：下载目录猜错（每台电脑不同）

**TL;DR**：下载目录可能被用户挪到任意盘符，`~/Downloads` 猜测必然出错。

- 问题：默认保存路径不是用户的真实下载目录
- 根因：Windows 下载目录是 Known Folder，可被迁移/重定向
- 解决：三级自适应——①`SHGetKnownFolderPath(FOLDERID_Downloads)` 官方 API ②注册表 Shell Folders GUID ③`~/Downloads` 回退
- 预防：凡系统已知目录（下载/文档/桌面），一律走 Known Folder API

## 测试约定

- E2E 用**真 mainloop + after 调度**驱动（手动 update() 泵与 tkinter 线程模型冲突，after 会抛 "main thread is not in main loop"）
- poll 分支 return 前必须续排 `root.after`，否则进程假死
- **断言基准必须当次从 API 现取**（仓库内容会变，旧会话的文件大小硬编码必踩坑）
- 渲染验证：PrintWindow API（PW_RENDERFULLCONTENT）从窗口缓冲截图，无视虚拟桌面与遮挡
