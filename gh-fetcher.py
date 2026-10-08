# -*- coding: utf-8 -*-
"""
gh-fetcher —— GitHub 仓库/Release 浏览下载器（tkinter 深色，纯标准库）

路由策略（基于 2026-10-08 全天实测）：
  读取（文件树/Releases/源码文件）→ api.github.com（全天最稳，token 可选）
  整仓归档                        → codeload.github.com（302，稳定）
  Release 资产 → 公开仓库：镜像链自动回退（ghproxy.net → gh-proxy.com → 直连）
              → 私有仓库：token 直连 302（绝不让 token 流向公共镜像）

安全约定：token 只存内存（本版不落盘）；公共镜像请求永不携带 token。
"""
from __future__ import annotations
import ctypes
import json
import os
import re
import ssl
import sys
import threading
import time
import tkinter as tk
import urllib.error
import urllib.parse
import urllib.request
from tkinter import ttk

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass
import tkinter as tk
from tkinter import ttk

SCALE = 1.0
try:
    SCALE = ctypes.windll.shcore.GetScaleFactorForDevice(0) / 96.0
except Exception:
    pass

TITLE = "GitHub 下载器 · gh-fetcher"
MIRRORS = ["https://ghproxy.net/", "https://gh-proxy.com/"]
API = "https://api.github.com"
TIMEOUT = 20

BG, PANEL, PANEL2 = "#1e1f24", "#26272e", "#2c2d35"
FG, MUT, BORDER = "#e8e8ea", "#9a9aa3", "#3a3b44"
GREEN, AMBER, RED, BLUE = "#4caf7d", "#e2b45a", "#e26d5a", "#5b8dd9"

_MUTEX = None


def _base_opener():
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(
            context=ssl.create_default_context()))


def _exe_dir():
    return (os.path.dirname(sys.executable) if getattr(sys, "frozen", False)
            else os.path.dirname(os.path.abspath(__file__)))


def _config_path():
    return os.path.join(_exe_dir(), "fetcher-config.json")


CTX_OP = _base_opener()


def load_config():
    try:
        with open(_config_path(), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(cfg):
    try:
        with open(_config_path(), "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def api_get(url, token="", timeout=TIMEOUT, raw=False):
    """GET api.github.com，返回解析后 JSON 或原始字节。"""
    headers = {"User-Agent": "gh-fetcher",
               "Accept": ("application/vnd.github.raw" if raw
                          else "application/vnd.github+json")}
    if token:
        headers["Authorization"] = f"token {token}"
    req = urllib.request.Request(url, headers=headers)
    r = CTX_OP.open(req, timeout=timeout)
    return r.read() if raw else json.loads(r.read())


def human(n):
    if n is None:
        return "-"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def download_noredirect(url, token, dest, progress=None, timeout=60):
    """带 token 直下（302 手工跟随且跨域时剥离 Authorization）。"""
    base = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(
            context=ssl.create_default_context()))
    headers = {"User-Agent": "gh-fetcher"}
    if token:
        headers["Authorization"] = f"token {token}"
    cur = url
    for _ in range(5):
        req = urllib.request.Request(cur, headers=headers)
        try:
            r = base.open(req, timeout=timeout)
            break
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 303, 307, 308):
                loc = e.headers.get("Location")
                if not loc:
                    raise
                nxt = urllib.parse.urljoin(cur, loc)
                if urllib.parse.urlsplit(nxt).netloc != \
                        urllib.parse.urlsplit(cur).netloc:
                    headers.pop("Authorization", None)   # 跨域剥离 token
                cur = nxt
                continue
            raise
    total = int(r.headers.get("Content-Length") or 0)
    got = 0
    with open(dest, "wb") as f:
        while True:
            chunk = r.read(262144)
            if not chunk:
                break
            f.write(chunk)
            got += len(chunk)
            if progress:
                progress(got, total)
    return got


def download_via_mirror(mirror, url, dest, progress=None, timeout=120):
    req = urllib.request.Request(mirror + url,
                                 headers={"User-Agent": "gh-fetcher"})
    r = CTX_OP.open(req, timeout=timeout)
    total = int(r.headers.get("Content-Length") or 0)
    got = 0
    with open(dest, "wb") as f:
        while True:
            chunk = r.read(262144)
            if not chunk:
                break
            f.write(chunk)
            got += len(chunk)
            if progress:
                progress(got, total)
    if total and got < total:   # 服务端中途断流：立即报错，不留半截文件
        raise IOError(f"连接中断 {got}/{total}")
    return got


def smart_download(browser_url, dest, token, progress=None, log=None,
                   expected_size=None, name="", gateway=""):
    """路由顺序：自建网关（若有）→ 公共镜像链（仅公开仓库）→ 直连×2。
    每条路由校验大小，失败清理半成品换下一条。"""
    routes = []
    gw = (gateway or "").strip().rstrip("/")
    if gw:
        routes.append(("自建网关",
                       lambda: download_via_mirror(gw + "/", browser_url,
                                                   dest, progress)))
    if not token:
        routes.append(("镜像 gh-proxy.com",
                       lambda: download_via_mirror(
                           "https://gh-proxy.com/", browser_url, dest, progress)))
        routes.append(("镜像 ghproxy.net",
                       lambda: download_via_mirror(
                           "https://ghproxy.net/", browser_url, dest, progress)))
    for i in range(2):
        routes.append((f"直连(第{i+1}次)",
                       lambda i=i: download_noredirect(browser_url, token,
                                                       dest, progress)))
    last = None
    for label, fn in routes:
        try:
            if log:
                log(f"{label} 下载 {name} …")
            n = fn()
            if expected_size and n != expected_size:
                raise IOError(f"大小不符 {n}/{expected_size}"
                              "（可能拿到的是错误页/半截文件）")
            if log:
                log(f"{label} 成功（{human(n)}）")
            return n
        except Exception as e:
            last = e
            if log:
                log(f"{label} 失败: {type(e).__name__} {str(e)[:70]}")
            try:
                os.remove(dest)
            except OSError:
                pass
    raise last or IOError("无可用下载路由")


def _downloads_dir():
    """系统真实下载目录，三级自适应：
    ① SHGetKnownFolderPath（微软官方 API，正确处理下载目录迁移/重定向）
    ② 注册表 Shell Folders GUID
    ③ ~/Downloads 回退"""
    if sys.platform == "win32":
        from ctypes import wintypes, windll
        from uuid import UUID

        class GUID(ctypes.Structure):
            _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                        ("Data3", wintypes.WORD),
                        ("Data4", wintypes.BYTE * 8)]

            def __init__(self, uuidstr):
                uuid = UUID(uuidstr)
                ctypes.Structure.__init__(self)
                self.Data1, self.Data2, self.Data3, \
                    self.Data4[0], self.Data4, rest = uuid.fields
                for i in range(2, 8):
                    self.Data4[i] = rest >> (8 - i - 1) * 8 & 0xFF

        get_path = windll.shell32.SHGetKnownFolderPath
        get_path.argtypes = [ctypes.POINTER(GUID), wintypes.DWORD,
                             wintypes.HANDLE,
                             ctypes.POINTER(ctypes.c_wchar_p)]
        try:
            pathptr = ctypes.c_wchar_p()
            guid = GUID("{374DE290-123F-4565-9164-39C4925E467B}")  # FOLDERID_Downloads
            if get_path(ctypes.byref(guid), 0, None,
                        ctypes.byref(pathptr)) == 0 and pathptr.value:
                return pathptr.value
        except Exception:
            pass
        try:
            import winreg
            with winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    r"SOFTWARE\Microsoft\Windows\CurrentVersion"
                    r"\Explorer\Shell Folders") as key:
                loc = winreg.QueryValueEx(
                    key, "{374DE290-123F-4565-9164-39C4925E467B}")[0]
                if loc:
                    return loc
        except Exception:
            pass
    return os.path.join(os.path.expanduser("~"), "Downloads")


class App:
    def __init__(self, root):
        self.root = root
        root.title(TITLE)
        root.geometry(f"{int(860*SCALE)}x{int(620*SCALE)}")
        root.minsize(int(760*SCALE), int(560*SCALE))
        root.configure(bg=BG)
        self.token = ""
        self.repo = ""
        self.tree_sha = ""
        self.releases = []          # [(tag, name, size, url, kind)]
        self.iids = {}
        self.save_dir = _downloads_dir()
        self.running = False
        self.config_path = _config_path()
        self.gateway = ""
        self._build_style()
        self._build_ui()

    def _build_style(self):
        s = ttk.Style()
        s.theme_use("clam")
        s.configure(".", background=BG, foreground=FG, fieldbackground=PANEL,
                    bordercolor=BORDER, font=("Microsoft YaHei UI", 10))
        s.configure("TFrame", background=BG)
        s.configure("Muted.TLabel", foreground=MUT)
        s.configure("TButton", background=PANEL2, foreground=FG,
                    bordercolor=BORDER, padding=(12, 5))
        s.map("TButton", background=[("active", "#3a3b45")])
        s.configure("Accent.TButton", background="#2e5c46", foreground="#dff5ea")
        s.map("Accent.TButton", background=[("active", "#376e54")])
        s.configure("TEntry", fieldbackground=PANEL, insertcolor=FG)
        s.configure("Treeview", background=PANEL, fieldbackground=PANEL,
                    foreground=FG, rowheight=int(26*SCALE), bordercolor=BORDER)
        s.configure("Treeview.Heading", background=PANEL2, foreground=MUT,
                    relief="flat", font=("Microsoft YaHei UI", 9))
        s.map("Treeview", background=[("selected", "#35414d")])
        s.configure("TProgressbar", background=GREEN, troughcolor=PANEL2,
                    bordercolor=BORDER)

    def _build_ui(self):
        top = ttk.Frame(self.root)
        top.pack(fill="x", padx=14, pady=(12, 4))
        ttk.Label(top, text="仓库").pack(side="left")
        self.repo_var = tk.StringVar()
        e_repo = ttk.Entry(top, textvariable=self.repo_var, width=24)
        e_repo.pack(side="left", padx=(4, 10))
        e_repo.bind("<Return>", lambda e: self.load_repo())
        ttk.Label(top, text="Token(可选)").pack(side="left")
        self.token_var = tk.StringVar()
        e_tok = ttk.Entry(top, textvariable=self.token_var, width=22,
                          show="*")
        e_tok.pack(side="left", padx=(4, 10))
        ttk.Label(top, text="镜像前缀(可选)").pack(side="left")
        self.gateway_var = tk.StringVar()
        e_gw = ttk.Entry(top, textvariable=self.gateway_var, width=20)
        e_gw.pack(side="left", padx=(4, 0))
        self.btn_load = ttk.Button(top, text="加载", style="Accent.TButton",
                                   command=self.load_repo)
        self.btn_load.pack(side="left", padx=(10, 0))

        body = ttk.Frame(self.root)
        body.pack(fill="both", expand=True, padx=14, pady=6)

        left = ttk.Frame(body)
        left.pack(side="left", fill="both", expand=True)
        ttk.Label(left, text="仓库文件").pack(anchor="w")
        self.tree = ttk.Treeview(left, columns=("p", "s"), show="headings",
                                 height=12, selectmode="browse")
        self.tree.heading("p", text="路径")
        self.tree.heading("s", text="大小")
        self.tree.column("p", width=280, anchor="w")
        self.tree.column("s", width=80, anchor="e", stretch=False)
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<Double-1>", lambda e: self.download_tree_item())
        brow = ttk.Frame(left)
        brow.pack(fill="x", pady=(4, 0))
        ttk.Button(brow, text="下载选中文件",
                   command=self.download_tree_item).pack(side="left")
        ttk.Button(brow, text="下载所有zip",
                   command=self.download_zip).pack(side="right")

        right = ttk.Frame(body)
        right.pack(side="left", fill="both", expand=True, padx=(12, 0))
        ttk.Label(right, text="Releases").pack(anchor="w")
        self.rel = ttk.Treeview(right,
                                columns=("c", "t", "a", "s"),
                                show="headings", height=12,
                                selectmode="browse")
        self.rel.heading("c", text="选")
        self.rel.heading("t", text="版本")
        self.rel.heading("a", text="资产")
        self.rel.heading("s", text="大小")
        self.rel.column("c", width=36, anchor="center", stretch=False)
        self.rel.column("t", width=90, anchor="w")
        self.rel.column("a", width=180, anchor="w")
        self.rel.column("s", width=70, anchor="e", stretch=False)
        self.rel.pack(fill="both", expand=True)
        self.rel.bind("<Double-1>", lambda e: self.download_asset())
        self.rel.bind("<Button-1>", self._rel_click)
        self.rel_checked = set()
        ttk.Button(right, text="下载勾选的资产",
                   command=self.download_all_assets).pack(anchor="w", pady=(4, 0))

        self.pbar = ttk.Progressbar(self.root, mode="determinate", maximum=100)
        self.pbar.pack(fill="x", padx=14, pady=(2, 0))
        bottom = ttk.Frame(self.root)
        bottom.pack(fill="x", padx=14, pady=(4, 4))
        ttk.Label(bottom, text="保存到").pack(side="left")
        self.dir_var = tk.StringVar(value=self.save_dir)
        ttk.Entry(bottom, textvariable=self.dir_var).pack(
            side="left", fill="x", expand=True, padx=6)
        ttk.Button(bottom, text="…", width=3,
                   command=self.pick_dir).pack(side="left")

        self.status = ttk.Label(self.root, text="就绪（公开仓库可不填 token；"
                                "token 仅存内存）", style="Muted.TLabel")
        self.status.pack(fill="x", padx=14, pady=(0, 10))
        # 恢复持久化设置（网关/保存目录；token 不落盘）
        try:
            cfg = load_config()
            self.gateway = cfg.get("gateway", "")
            self.gateway_var.set(self.gateway)
            sd = cfg.get("save_dir")
            if sd:
                self.save_dir = sd
                self.dir_var.set(sd)
        except Exception:
            pass

    def _persist(self):
        try:
            save_config({"gateway": self.gateway_var.get().strip(),
                         "save_dir": self.dir_var.get()})
        except Exception:
            pass

    # ---------- 工具
    def log(self, msg):
        self.status.configure(text=msg)

    def pick_dir(self):
        from tkinter import filedialog
        d = filedialog.askdirectory(initialdir=self.save_dir)
        if d:
            self.dir_var.set(d)
            self.save_dir = d

    def dest_for(self, name):
        os.makedirs(self.save_dir, exist_ok=True)
        return os.path.join(self.save_dir, name)

    # ---------- 加载仓库
    def load_repo(self):
        repo = self.repo_var.get().strip()
        # 宽容解析：https://github.com/x/y 、github.com/x/y 、www 变体 → x/y
        repo = re.sub(r"^https?://", "", repo, flags=re.I)
        repo = re.sub(r"^(www\.)?github\.com/?", "", repo, flags=re.I)
        repo = repo.strip("/")
        if repo.count("/") != 1 or not re.match(r"^[\w.-]+/[\w.-]+$", repo):
            self.log(f"仓库格式不对：你输入的是「{self.repo_var.get().strip()}」。"
                     "应为 owner/repo（如 Simiely/MonitorBrightness）"
                     "或完整 GitHub 链接")
            return
        self.repo = repo
        self.token = self.token_var.get().strip()
        self.gateway = self.gateway_var.get().strip()
        self._persist()
        self.running = getattr(self, "running", False)
        if self.running:
            return
        self.running = True
        self.btn_load.configure(state="disabled")
        self.log(f"加载 {self.repo} …")
        threading.Thread(target=self._load_work, daemon=True).start()

    def _load_work(self):
        try:
            info = api_get(f"{API}/repos/{self.repo}", self.token)
            branch = info.get("default_branch", "main")
            tree = api_get(
                f"{API}/repos/{self.repo}/git/trees/{branch}?recursive=1",
                self.token)
            rels = api_get(
                f"{API}/repos/{self.repo}/releases?per_page=30", self.token)
            self.root.after(0, lambda: self._load_done(
                info.get("full_name", self.repo), branch, tree, rels))
        except urllib.error.HTTPError as e:
            msg = {401: "token 无效", 403: "限流/无权限（私有仓库需 token）",
                   404: "仓库不存在（私有仓库请填 token）"}.get(e.code,
                                                            f"HTTP {e.code}")
            self.root.after(0, lambda: self._load_fail(msg))
        except Exception as e:
            self.root.after(0, lambda: self._load_fail(
                f"{type(e).__name__}: {str(e)[:80]}"))

    def _load_fail(self, msg):
        self.running = False
        self.btn_load.configure(state="normal")
        self.log(f"加载失败：{msg}")

    def _load_done(self, full_name, branch, tree, rels):
        self.running = False
        self.btn_load.configure(state="normal")
        self.tree.delete(*self.tree.get_children())
        self.rel.delete(*self.rel.get_children())
        self.iids.clear()
        self.tree_sizes = {}
        self.tree_sha = {}
        entries = [(t["path"], t.get("size", 0), t["sha"])
                   for t in tree.get("tree", [])
                   if t["type"] == "blob" and not t["path"].startswith(".")]
        entries.sort(key=lambda x: ("/" in x[0], x[0].lower()))
        for p, sz, sha in entries:
            self.tree_sizes[p] = sz
            self.tree_sha[p] = sha
            self.tree.insert("", "end", values=(p, human(sz)),
                             tags=("row",))
        n_trunc = tree.get("truncated")
        self.rel_checked = set()   # 默认全不勾，用户点第一列自行勾选
        for rel in rels:
            for a in rel.get("assets", []):
                row = (rel.get("tag_name", "?"), a["name"], a["size"])
                iid = self.rel.insert("", "end",
                                      values=("☐", *row), tags=("row",))
                self.releases.append(
                    {"iid": iid, "tag": row[0], "name": a["name"],
                     "size": a["size"], "id": a["id"],
                     "url": a["browser_download_url"]})
            if not rel.get("assets"):
                row = (rel.get("tag_name", "?"),
                       "(源码 zip，无二进制资产)", 0)
                iid = self.rel.insert("", "end",
                                      values=("", *row), tags=("muted",))
                self.releases.append({"iid": iid, "tag": row[0],
                                      "name": f"{self.repo}-{row[0]}.zip",
                                      "size": 0, "zip": rel.get("zipball_url"),
                                      "url": None})
        self.rel.tag_configure("row", foreground=FG)
        self.rel.tag_configure("muted", foreground=MUT)
        self.tree.tag_configure("row", foreground=FG)
        note = f"{full_name}（{branch}）: {len(entries)} 文件, " \
               f"{len(self.releases)} 项可下载"
        if n_trunc:
            note += " ⚠ 仓库过大，文件树不完整（只显示部分）"
        self.log(note)

    # ---------- 下载
    def _pick(self, widget):
        sel = widget.selection()
        return widget.item(sel[0], "values") if sel else None

    def download_tree_item(self):
        vals = self._pick(self.tree)
        if not vals:
            return
        path = vals[0]
        url = f"{API}/repos/{self.repo}/contents/{urllib.parse.quote(path)}"
        expected = getattr(self, "tree_sizes", {}).get(path)
        sha = getattr(self, "tree_sha", {}).get(path)
        opts = {"blob": True, "sha": sha} if sha else {"raw": True}
        threading.Thread(target=self._dl_work, daemon=True, args=(
            url, os.path.basename(path), opts, expected)).start()

    def download_zip(self):
        if not self.repo:
            self.log("先加载仓库")
            return
        url = f"{API}/repos/{self.repo}/zipball"
        name = f"{self.repo.replace('/', '-')}.zip"
        threading.Thread(target=self._dl_work, daemon=True, args=(
            url, name, {"follow": True}, name)).start()

    def download_asset(self):
        vals = self._pick(self.rel)
        if not vals:
            return
        item = next((r for r in self.releases
                     if r["tag"] == vals[0] and r["name"] == vals[1]), None)
        if not item:
            return
        if item.get("zip"):
            name = item["name"]
            url = item["zip"]
            threading.Thread(target=self._dl_work, daemon=True, args=(
                url, name, {"follow": True})).start()
            return
        threading.Thread(target=self._dl_asset_work, daemon=True, args=(
            item["id"], item["name"], item["url"], item["size"])).start()

    def _rel_click(self, event):
        """点击第一列的勾选框：切换 ☑/☐。"""
        if self.rel.identify_region(event.x, event.y) not in ("cell", "tree"):
            return
        col = self.rel.identify_column(event.x)
        if col != "#1":
            return
        iid = self.rel.identify_row(event.y)
        if not iid or not self.rel.set(iid, "c"):
            return
        if iid in self.rel_checked:
            self.rel_checked.discard(iid)
            self.rel.set(iid, "c", "☐")
        else:
            self.rel_checked.add(iid)
            self.rel.set(iid, "c", "☑")

    def download_all_assets(self):
        items = [r for r in self.releases
                 if r["iid"] in self.rel_checked and r["url"]]
        if not items:
            self.log("没有勾选可下载的资产（点资产行第一列打勾）")
            return
        self.log(f"批量下载 {len(items)} 个勾选资产……")
        threading.Thread(target=self._dl_many, daemon=True, args=(items,)).start()

    def _dl_many(self, items):
        ok = fail = 0
        for it in items:
            try:
                self._dl_asset(it["id"], it["name"], it["url"], it["size"])
                ok += 1
            except Exception as e:
                fail += 1
                self.root.after(0, lambda e=e, n=it["name"]:
                                self.log(f"{n} 失败: {type(e).__name__} "
                                         f"{str(e)[:60]}"))
        self.root.after(0, lambda: self.log(
            f"批量下载完成：成功 {ok}，失败 {fail}"))

    def _dl_asset(self, asset_id, name, browser_url, expected_size=None):
        dest = self.dest_for(name)
        last = None
        for attempt in range(2):   # 整链跑两轮：网络窗口期靠时间换成功
            try:
                n = smart_download(browser_url, dest, self.token,
                                   progress=self._progress, log=self.log,
                                   expected_size=expected_size, name=name,
                                   gateway=self.gateway)
                self.root.after(0, lambda: self.log(f"完成: {name}（{human(n)}）"))
                return
            except Exception as e:
                last = e
                if attempt == 0:
                    self.root.after(0, lambda: self.log(
                        "整链失败（网络窗口期），30 秒后自动重试一轮……"))
                    time.sleep(30)
        self.root.after(0, lambda: self.log(
            f"下载失败 {name}: {type(last).__name__} {str(last)[:70]}"))

    def _dl_work(self, url, name, opts, expected=None):
        dest = self.dest_for(name)
        token = self.token
        if opts.get("blob"):
            # Git Blobs API：按 sha 取原始字节（base64），不受 eol 转换影响
            def go():
                import base64
                d = api_get(f"{API}/repos/{self.repo}/git/blobs/{opts['sha']}",
                            token, timeout=90)
                data = base64.b64decode(d["content"])
                if expected and len(data) != expected:
                    raise IOError(f"大小不符 {len(data)}/{expected}")
                with open(dest, "wb") as f:
                    f.write(data)
                return len(data)
            self._run_progress(go, name)
        elif opts.get("raw"):
            def go():
                return download_noredirect(url, token, dest,
                                           progress=self._progress)
            self._run_progress(go, name)

    def _dl_asset_work(self, asset_id, name, browser_url, expected_size=None):
        dest = self.dest_for(name)
        last = None
        for attempt in range(2):
            try:
                n = smart_download(browser_url, dest, self.token,
                                   progress=self._progress, log=self.log,
                                   expected_size=expected_size, name=name,
                                   gateway=self.gateway)
                self.root.after(0, lambda: self.log(f"完成: {name}（{human(n)}）"))
                return
            except Exception as e:
                last = e
                if attempt == 0:
                    self.root.after(0, lambda: self.log(
                        "整链失败（网络窗口期），30 秒后自动重试一轮……"))
                    time.sleep(30)
        self.root.after(0, lambda: self.log(
            f"下载失败 {name}: {type(last).__name__} {str(last)[:70]}"))

    def _run_progress(self, fn, name):
        try:
            n = fn()
            self.root.after(0, lambda: self.log(f"完成: {name}（{human(n)}）"))
        except Exception as e:
            self.root.after(0, lambda: self.log(
                f"下载失败 {name}: {type(e).__name__} {str(e)[:60]}"))

    def _progress(self, got, total):
        now = time.time()
        st = getattr(self, "_dl_state", None)
        if not st or got < st[1]:          # 新一轮下载：重置采样
            self._dl_state = [now, got, now, 0.0]
        t0, last_got, last_t, ema = self._dl_state
        dt = now - last_t
        if dt >= 0.2:                       # 200ms 采样一次，EMA 平滑
            inst = (got - last_got) / dt
            ema = inst if ema == 0 else ema * 0.6 + inst * 0.4
            self._dl_state = [t0, got, now, ema]
        pct = got * 100 / total if total else 0
        speed_txt = f"{human(ema)}/s" if ema else "…"
        eta_txt = ""
        if ema and total and total > got:
            eta = (total - got) / ema
            if eta >= 3600:
                eta_txt = f" · 剩余 {eta/3600:.1f} 小时"
            else:
                eta_txt = f" · 剩余 {int(eta // 60):02d}:{int(eta % 60):02d}"
        self.root.after(0, lambda: (
            self.pbar.configure(value=pct),
            self.status.configure(
                text=f"下载中 {human(got)}/{human(total)} ({pct:.0f}%)"
                     f" · {speed_txt}{eta_txt}")))

    # ---- 调度占位（无自动刷新需求）
    pass


def main():
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        global _MUTEX
        _MUTEX = k32.CreateMutexW(None, False, "gh-fetcher-single")
        if ctypes.get_last_error() == 183:
            u = ctypes.windll.user32
            hwnd = u.FindWindowW(None, TITLE)
            if hwnd:
                u.ShowWindow(hwnd, 9)
                u.SetForegroundWindow(hwnd)
            return 0
        root = tk.Tk()
        App(root)
        root.mainloop()
        return 0
    except Exception:
        import traceback
        d = (os.path.dirname(sys.executable) if getattr(sys, "frozen", False)
             else os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(d, "fetcher-crash.txt"), "w",
                  encoding="utf-8") as f:
            f.write(traceback.format_exc())
        raise


if __name__ == "__main__":
    main()
