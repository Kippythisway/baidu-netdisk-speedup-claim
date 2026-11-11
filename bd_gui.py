#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
百度网盘 激励体系 一站式领取 + 用券（本地账号）

覆盖四条独立链路：
  A. taskscore 全量任务
  B. /rest/2.0/pcs/adv 广告场景
  C. 抽奖
  D. 用券提速（进客户端进程调 native，不截图不点击）

双击运行 = 交互菜单；带参数运行 = 静默模式（适合计划任务）

用法:
    bd_gui.py                 交互菜单
    bd_gui.py all             全跑（任务 + 场景 + 抽奖，不含用券）
    bd_gui.py --status        只看剩余
    bd_gui.py --tasks         只刷任务
    bd_gui.py --adv           只刷场景
    bd_gui.py --lottery       只抽奖
    bd_gui.py --ticket        只用一个加速券
    bd_gui.py --no-pause      跑完不暂停（脚本调用用）
"""
import base64, ctypes, ctypes.wintypes as wt
import hashlib, json, os, random, sqlite3, ssl, struct, sys, time
import urllib.parse, urllib.request

# ---- 强制 UTF-8 输出（打包成 exe 后中文不乱码）
try:
    ctypes.windll.kernel32.SetConsoleOutputCP(65001)
    ctypes.windll.kernel32.SetConsoleCP(65001)
except Exception:
    pass
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

APP = os.path.expandvars(r"%APPDATA%\baidunetdisk")
WORK = os.path.dirname(os.path.abspath(sys.argv[0] if getattr(sys, "frozen", False) else __file__))

PID_ANDROID = "1551078141121"
ZID = "67F22DA33A61F6B445E2618A87BC30786F"
CUID = "728D228D4F794773BDF6C26CE1ECCDB2|0"
APNA = "com.baidu.netdisk.platform.business.app"
KEY_ADV = "eyls80i58hqvg8lgvv0fqx3kry2mpcjc"
SALT_TASK = "ae82c240578eb391de93c2f4c3dfc3ba"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
REF_ADV = ("https://pan.baidu.com/wap/svip/privilege/freeget"
           "?na_immerse_theme=1&task_scene=reward_video_bidding_scene_hybrid_forward_download_speed"
           "&from=pc_acceleration")

R = "\033[0m"; G = "\033[92m"; Y = "\033[93m"; C = "\033[96m"; B = "\033[1m"
try:
    os.system("")          # enable ANSI on Windows 10+
except Exception:
    pass

def say(msg, color=""):
    print(f"{color}{msg}{R}" if color else msg, flush=True)

# ------------------------------------------------------------------ cookies
def extract_cookies():
    import win32crypt
    from Crypto.Cipher import AES
    ls_path = os.path.join(APP, "Local State")
    if not os.path.exists(ls_path):
        raise SystemExit(f"[x] 没找到客户端配置：{ls_path}\n    请先安装并登录 PC 版百度网盘")
    ls = json.load(open(ls_path, encoding="utf-8"))
    enc = base64.b64decode(ls["os_crypt"]["encrypted_key"])[5:]
    key = win32crypt.CryptUnprotectData(enc, None, None, None, 0)[1]
    tmp = os.path.join(WORK, "_ck.db")
    with open(os.path.join(APP, "Network", "Cookies"), "rb") as s, open(tmp, "wb") as d:
        d.write(s.read())
    def dec(b):
        if not b: return ""
        if b[:3] in (b"v10", b"v11"):
            return AES.new(key, AES.MODE_GCM, nonce=b[3:15]).decrypt_and_verify(
                b[15:-16], b[-16:]).decode("utf-8", "replace")
        return b.decode("utf-8", "replace")
    con = sqlite3.connect(tmp); out = {}
    for host, name, ev, val in con.execute(
            "SELECT host_key,name,encrypted_value,value FROM cookies"):
        out.setdefault(host, {})[name] = dec(ev) if ev else val
    con.close()
    try: os.unlink(tmp)
    except Exception: pass
    return out

def cookie_header(ck):
    pb = ck.get(".pan.baidu.com") or ck.get("pan.baidu.com") or {}
    bb = ck.get(".baidu.com") or ck.get("baidu.com") or {}

    def pick(*names):
        for src in (pb, bb):
            for n in names:
                if src.get(n):
                    return src[n]
        return ""

    bduss = pick("BDUSS", "BDUSS_BFESS")
    if not bduss:
        raise SystemExit("[x] 没读到 BDUSS，请先在客户端登录一次\n"
                         "    需要 BDUSS 或 BDUSS_BFESS 其中之一")
    return (f"BDUSS={bduss}; STOKEN={pick('STOKEN')}; "
            f"BAIDUID={pick('BAIDUID', 'BAIDUID_BFESS')}; "
            f"PANPSC={pick('PANPSC')}; langs=zh-CN")

# ------------------------------------------------------------------ http
def _get(url, params=None, cookie="", referer="https://pan.baidu.com/disk/main"):
    if params: url += "?" + urllib.parse.urlencode(params, safe='[]{}":,|')
    r = urllib.request.Request(url, headers={"User-Agent": UA, "Cookie": cookie,
        "Referer": referer, "Accept": "application/json, text/plain, */*"})
    try:
        with urllib.request.urlopen(r, timeout=25, context=ssl.create_default_context()) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        try: return json.loads(e.read().decode("utf-8", "replace"))
        except Exception: return {"http": e.code}
    except Exception as e:
        return {"err": str(e)}

def quota(cookie):
    q = _get("https://pan.baidu.com/api/quota",
             {"checkfree":"1","checkexpire":"1","clienttype":"0","app_id":"250528"}, cookie)
    return q.get("total", 0)

# ------------------------------------------------------------------ tasks
def list_tasks(cookie, task_from=""):
    r = _get("https://pan.baidu.com/api/taskscore/tasklist",
             {"task_from": task_from, "version": "12.0.0", "channel": "0",
              "clienttype": "0", "app_id": "250528"}, cookie)
    return (r.get("result") or {}).get("list") or []

def tasksave(cookie, uk, task_id, task_from=""):
    rnd = str(random.randint(10**15, 10**16 - 1)); ts = int(time.time())
    token = hashlib.md5(f"{task_id}_{uk}_{rnd}_{ts}_{SALT_TASK}".encode()).hexdigest()
    return _get("https://pan.baidu.com/api/taskscore/tasksave", {
        "uk": uk, "task_id": task_id, "rand": rnd, "time": ts, "token": token,
        "task_from": task_from, "version": "12.0.0", "clienttype": "0",
        "channel": "0", "jsToken": "", "app_id": "250528"}, cookie)

# ------------------------------------------------------------------ adv
def adv_params(mode, uk, sid=None):
    ts = int(time.time())
    sig = hashlib.md5(f"{KEY_ADV}{ts}{PID_ANDROID}".encode()).hexdigest()
    p = {"m": mode, "pid": PID_ANDROID, "zid": ZID, "uk": str(uk),
         "ct": "2", "ot": "2", "nt": "1", "s": f"{ts}:{sig}",
         "cuid": CUID, "proid": "35", "mod": "NOH-AL00",
         "ov": "12", "ver": "11.60.3", "apna": APNA,
         "ad": '[{"da_ext1":1,"da_ext3":24,"da_page":"NAVIDEO",'
               '"da_type":103,"extra_param":"default","origin_time":%d}]' % ts,
         "ua": UA, "timestamp": str(ts)}
    if sid is not None: p["sid"] = str(sid)
    return p

def adv(cookie, mode, uk, sid=None):
    return _get("https://pan.baidu.com/rest/2.0/pcs/adv",
                adv_params(mode, uk, sid), cookie, referer=REF_ADV)

# ------------------------------------------------------------------ flows
def run_tasks(cookie, uk, status_only=False):
    tasks = list_tasks(cookie, "")
    say(f"\n=== 任务表：{len(tasks)} 条 ===", C)
    ok = space = rew = 0
    for i, t in enumerate(tasks, 1):
        if status_only: break
        r = tasksave(cookie, uk, t["task_id"])
        if r.get("errno") == 0:
            res = r.get("result") or {}
            ok += 1
            space += res.get("addSpace", 0)
            rew += res.get("addRewardNum", 0)
        if i % 40 == 0:
            say(f"  ... {i}/{len(tasks)}  成功 {ok}")
            time.sleep(2)
        time.sleep(0.55)
    if not status_only:
        say(f"  完成 {ok}/{len(tasks)}  空间单位 {space}  奖励 {rew}", G)

def run_adv(cookie, uk, status_only=False):
    cfg = adv(cookie, "config", uk)
    scenes = cfg.get("scene_list") or []
    say(f"\n=== 广告场景：{len(scenes)} 个 ===", C)
    total = 0
    for s in scenes:
        sid = s["scene_id"]
        lo = (adv(cookie, "leftover", uk, sid).get("task_count") or {})
        remain = lo.get("remain_step_cnt") or 0
        name = (s.get("task_list") or [{}])[0].get("complete_notice", "")[:26]
        if remain <= 0:
            if status_only: say(f"  {sid:6d}  剩余 0  {name}")
            continue
        say(f"  {sid:6d}  剩余 {remain}  {name}")
        if status_only: continue
        n = 0
        while remain > 0 and n < 100:
            r = adv(cookie, "callback", uk, sid)
            if r.get("err_code") != 0: break
            n += 1; remain -= 1
            time.sleep(0.9)
        say(f"         领取 {n}", G)
        total += n
    if not status_only:
        say(f"  场景合计领取 {total} 次", G)

def run_lottery(cookie, uk, status_only=False):
    say("\n=== 抽奖 10051 ===", C)
    li = adv(cookie, "lotteryinfo", uk, 10051).get("lottery_info") or {}
    say(f"  当前：{li}")
    if status_only:
        return
    for _ in range(11):
        r = adv(cookie, "callback", uk, 10051)
        if r.get("err_code") != 0: break
        time.sleep(0.9)
    li = adv(cookie, "lotteryinfo", uk, 10051).get("lottery_info") or {}
    chances = li.get("remain_chances") or 0
    say(f"  可抽 {chances} 次")
    for i in range(chances):
        d = adv(cookie, "lotterydraw", uk, 10051)
        dr = d.get("draw_result") or {}
        say(f"  第{i+1}次：{dr.get('prize_name')}  ({dr.get('prize_code')})",
            G if dr.get("prize_code") in ("30scoupon","10scoupon","speedboost") else "")
        if d.get("err_code") != 0: break
        time.sleep(1.0)

# ------------------------------------------------------------------ 用券
# 客户端那个「用券提速」按钮，点下去最终落到 browserengine.dll 的一个导出：
#   browser_engine_consume_ticket_speedup([0], 10, "", cb)
# 用券没有公开 HTTP 接口，所以这里进客户端进程直接调这个 native 函数。
# 全程不截图、不点击、不抢焦点、不联网、不读账号密码。

TICKET_EXE = "baidunetdiskunite.exe"
TICKET_DIR = ("baidu", "baidunetdisk", "module", "browserengine")
TICKET_DLL = "browserengine.dll"
TICKET_ALLOWED = frozenset((
    "browser_engine_refresh_flow",
    "browser_engine_query_ticket_speedup",
    "browser_engine_consume_ticket_speedup",
))

# x64 小段机器码：从参数块装载 4 个寄存器参数后 call
# 布局 [0]=argc [8..0x20]=args [0x28]=函数地址
_TICKET_STUB = bytes([
    0x49, 0x89, 0xCC,              # mov r12, rcx
    0x49, 0x8B, 0x44, 0x24, 0x28,  # mov rax, [r12+0x28]
    0x49, 0x8B, 0x4C, 0x24, 0x08,  # mov rcx, [r12+0x08]
    0x49, 0x8B, 0x54, 0x24, 0x10,  # mov rdx, [r12+0x10]
    0x4D, 0x8B, 0x44, 0x24, 0x18,  # mov r8,  [r12+0x18]
    0x4D, 0x8B, 0x4C, 0x24, 0x20,  # mov r9,  [r12+0x20]
    0x48, 0x83, 0xEC, 0x28,        # sub rsp, 0x28
    0xFF, 0xD0,                    # call rax
    0x48, 0x83, 0xC4, 0x28,        # add rsp, 0x28
    0xC3,                          # ret
])


def _ticket_proc_exe(pid):
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    h = k32.OpenProcess(0x1000, False, pid)
    if not h:
        return None
    try:
        buf = ctypes.create_unicode_buffer(2048)
        n = wt.DWORD(2048)
        ok = k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n))
        return buf.value if ok else None
    finally:
        k32.CloseHandle(h)


def _ticket_is_target(pid):
    p = _ticket_proc_exe(pid)
    if not p:
        return False
    low = p.lower().replace("/", "\\")
    if not low.endswith(TICKET_EXE):
        return False
    return all(part in low for part in TICKET_DIR)


class TicketClient:
    """连到百度网盘主进程，调它自己的 browserengine.dll 用券。

    目标白名单写死：可执行文件必须位于
      %APPDATA%\\baidu\\BaiduNetdisk\\module\\BrowserEngine\\BaiduNetdiskUnite.exe
    只调用 TICKET_ALLOWED 里的三个导出，其余一律拒绝。
    """

    def __init__(self):
        self.k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi")
        u32 = ctypes.WinDLL("user32")
        u32.SetProcessDPIAware()
        self.u32 = u32
        self.psapi = psapi
        k = self.k32
        k.GetModuleHandleW.restype = ctypes.c_void_p
        k.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
        k.OpenProcess.restype = wt.HANDLE
        k.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
        k.VirtualAllocEx.restype = ctypes.c_void_p
        k.VirtualAllocEx.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_size_t,
                                     wt.DWORD, wt.DWORD]
        k.WriteProcessMemory.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                         ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
        k.ReadProcessMemory.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                        ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
        k.CreateRemoteThread.restype = wt.HANDLE
        k.CreateRemoteThread.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_size_t,
                                         ctypes.c_void_p, ctypes.c_void_p, wt.DWORD,
                                         ctypes.POINTER(wt.DWORD)]
        psapi.EnumProcessModulesEx.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD,
                                               ctypes.POINTER(wt.DWORD), wt.DWORD]
        psapi.GetModuleBaseNameW.argtypes = [wt.HANDLE, ctypes.c_void_p,
                                             ctypes.c_wchar_p, wt.DWORD]

        self.pid = self._find_pid()
        if not self.pid:
            raise RuntimeError("没找到百度网盘主窗口。请先打开客户端并登录。")

        self.h = k.OpenProcess(0x1F0FFF, False, self.pid)
        if not self.h:
            raise RuntimeError("无法打开客户端进程 pid=%d" % self.pid)

        self.base = self._find_dll_base()
        if not self.base:
            raise RuntimeError("客户端未加载 %s，可能版本不匹配" % TICKET_DLL)

        dll = os.path.join(os.environ["APPDATA"], "baidu", "BaiduNetdisk",
                           "module", "BrowserEngine", TICKET_DLL)
        if not os.path.isfile(dll):
            raise RuntimeError("本机没有 %s，请确认客户端完整" % dll)
        self.local = ctypes.WinDLL(dll)
        self.local_base = k.GetModuleHandleW(TICKET_DLL)

        # 常驻块：故意永不释放。native 会存下回调指针异步调用，
        # 提前释放会让客户端崩溃。
        self.blk = k.VirtualAllocEx(self.h, None, 0x2000, 0x3000, 0x40)
        if not self.blk:
            raise RuntimeError("VirtualAllocEx 失败")
        self._wp(self.blk + 0x300, _TICKET_STUB)
        self._wp(self.blk + 0x200, b"\xC3")    # 回调桩：直接 ret
        self._wp(self.blk + 0x220, b"\x00")    # 空字符串

    def _find_pid(self):
        hits = []

        @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
        def cb(hwnd, _):
            n = self.u32.GetWindowTextLengthW(hwnd)
            if n > 0:
                buf = ctypes.create_unicode_buffer(n + 1)
                self.u32.GetWindowTextW(hwnd, buf, n + 1)
                if "百度网盘" in buf.value and self.u32.IsWindowVisible(hwnd):
                    r = wt.RECT()
                    self.u32.GetWindowRect(hwnd, ctypes.byref(r))
                    if r.right - r.left > 800:
                        pid = wt.DWORD()
                        self.u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                        if _ticket_is_target(pid.value):
                            hits.append(pid.value)
            return True

        self.u32.EnumWindows(cb, 0)
        return hits[0] if hits else None

    def _find_dll_base(self):
        mods = (ctypes.c_void_p * 1024)()
        need = wt.DWORD()
        self.psapi.EnumProcessModulesEx(self.h, ctypes.cast(mods, ctypes.c_void_p),
                                        ctypes.sizeof(mods), ctypes.byref(need), 3)
        name = ctypes.create_unicode_buffer(260)
        for i in range(need.value // ctypes.sizeof(ctypes.c_void_p)):
            m = mods[i]
            if not m:
                continue
            self.psapi.GetModuleBaseNameW(self.h, ctypes.c_void_p(m), name, 260)
            if name.value.lower() == TICKET_DLL:
                return m
        return None

    def _wp(self, addr, data):
        w = ctypes.c_size_t(0)
        self.k32.WriteProcessMemory(self.h, ctypes.c_void_p(addr), data, len(data),
                                    ctypes.byref(w))

    def _rd(self, addr, n):
        b = ctypes.create_string_buffer(n)
        w = ctypes.c_size_t(0)
        self.k32.ReadProcessMemory(self.h, ctypes.c_void_p(addr), b, n, ctypes.byref(w))
        return b.raw[:w.value]

    def call(self, name, args, timeout_ms=30000):
        """args 记号：'OUT'=int64* 输出槽  'CB'=回调桩  'E'=空字符串"""
        if name not in TICKET_ALLOWED:
            raise RuntimeError("拒绝调用白名单外的导出: %s" % name)
        rva = ctypes.cast(getattr(self.local, name), ctypes.c_void_p).value - self.local_base
        fn = self.base + rva
        out_slot = self.blk + 0x100
        self._wp(out_slot, b"\x00" * 8)

        resolved = []
        for a in args:
            if a == "OUT":
                resolved.append(out_slot)
            elif a == "CB":
                resolved.append(self.blk + 0x200)
            elif a == "E":
                resolved.append(self.blk + 0x220)
            else:
                resolved.append(int(a))

        blob = struct.pack("<Q", len(resolved))
        blob += b"".join(struct.pack("<Q", v & 0xFFFFFFFFFFFFFFFF) for v in resolved)
        blob = blob.ljust(0x28, b"\x00") + struct.pack("<Q", fn)
        self._wp(self.blk, blob)

        tid = wt.DWORD()
        th = self.k32.CreateRemoteThread(self.h, None, 0,
                                         ctypes.c_void_p(self.blk + 0x300),
                                         ctypes.c_void_p(self.blk), 0, ctypes.byref(tid))
        if not th:
            return None, None
        self.k32.WaitForSingleObject(th, timeout_ms)
        code = wt.DWORD()
        self.k32.GetExitCodeThread(th, ctypes.byref(code))
        self.k32.CloseHandle(th)
        raw = self._rd(out_slot, 8)
        out = struct.unpack("<q", raw)[0] if len(raw) == 8 else None
        return code.value, out

    def query(self):
        return self.call("browser_engine_query_ticket_speedup", ["OUT", 10, "CB"])

    def consume(self):
        return self.call("browser_engine_consume_ticket_speedup", ["OUT", 10, "E", "CB"])

    def close(self):
        self.k32.CloseHandle(self.h)


def run_ticket():
    say("\n=== 用券提速 ===", C)
    say("  进入客户端进程调 native —— 不截图、不点击、不抢焦点")
    try:
        cli = TicketClient()
    except RuntimeError as e:
        say(f"[x] {e}", Y)
        return
    try:
        say(f"[*] 已连接客户端  pid={cli.pid}", G)
        ret, _ = cli.query()
        say(f"[*] 查券 ret={ret}")
        say("[*] 正在用券 ...")
        ret, out = cli.consume()
        if ret != 0:
            say(f"[x] 用券失败 ret={ret}", Y)
            say("    常见原因：没有可用加速券 / 当前没有正在下载的任务")
            return
        say(f"[+] 用券成功  状态={out}", G)
        time.sleep(3)
        cli.query()
        say("[+] 完成。切到客户端「传输」页可以看到速度变化。", G)
    finally:
        cli.close()

# ------------------------------------------------------------------ main
def bootstrap():
    ck = extract_cookies(); cookie = cookie_header(ck)
    info = _get("https://pan.baidu.com/rest/2.0/xpan/nas?method=uinfo", None, cookie)
    uk = info.get("uk")
    if not uk:
        raise SystemExit(f"[x] 登录态失效：{info}\n    请重新登录 PC 客户端")
    say(f"\n[账号] {info.get('baidu_name','?')}   uk={uk}   vip={info.get('vip_type')}", B)
    before = quota(cookie)
    say(f"[空间] {before/1e9:.2f} GB\n")
    return cookie, uk, before

def menu():
    say("=" * 56, C)
    say("  百度网盘 激励领取 + 用券", B)
    say("=" * 56, C)
    say("  1) 全部领取（任务 + 场景 + 抽奖）")
    say("  2) 只看今天还剩多少")
    say("  3) 只刷任务表")
    say("  4) 只刷广告场景")
    say("  5) 只抽奖")
    say("  6) 用券提速（后台，不点鼠标）")
    say("  0) 退出")
    say("=" * 56, C)
    return input("  请选择 [1]: ").strip() or "1"

def main():
    argv = [a for a in sys.argv[1:]]
    no_pause = "--no-pause" in argv
    args = set(a for a in argv if a != "--no-pause")
    interactive = not args

    try:
        # 用券不依赖 cookie，先处理，跳过 bootstrap
        if interactive:
            choice = menu()
            if choice == "0":
                return
            if choice == "6":
                run_ticket()
                return
        else:
            choice = None
            if "--ticket" in args:
                run_ticket()
                if len(args) == 1:
                    return

        cookie, uk, before = bootstrap()

        if interactive:
            if choice == "2":
                run_tasks(cookie, uk, True); run_adv(cookie, uk, True); run_lottery(cookie, uk, True)
            elif choice == "3":
                run_tasks(cookie, uk)
            elif choice == "4":
                run_adv(cookie, uk)
            elif choice == "5":
                run_lottery(cookie, uk)
            else:
                run_tasks(cookie, uk); run_adv(cookie, uk); run_lottery(cookie, uk)
        else:
            status_only = "--status" in args
            do_all = ("all" in args) or not ({"--tasks","--adv","--lottery","--ticket"} & args)
            if do_all or "--tasks" in args:   run_tasks(cookie, uk, status_only)
            if do_all or "--adv" in args:     run_adv(cookie, uk, status_only)
            if do_all or "--lottery" in args: run_lottery(cookie, uk, status_only)

        after = quota(cookie)
        say(f"\n[空间] {after/1e9:.2f} GB   （{(after-before)/1e9:+.2f} GB）",
            G if after > before else "")
    except SystemExit as e:
        say(str(e), Y)
    except KeyboardInterrupt:
        say("\n已中断", Y)
    except Exception as e:
        say(f"\n[x] 出错：{e}", Y)
    finally:
        if interactive and not no_pause:
            try:
                input("\n按回车退出...")
            except EOFError:
                pass

if __name__ == "__main__":
    main()
