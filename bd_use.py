# -*- coding: utf-8 -*-
"""
bd_use.py —— 让百度网盘 PC 客户端使用一张「极速下载券」

【这个脚本做什么】
    让已经登录在你电脑上的百度网盘客户端，用掉一张加速券，给正在进行的
    下载提速。效果与手动操作「用券提速」→ 选券 →「立即使用」完全一致。

【为什么要在进程内调用】
    百度把用券动作放在客户端自带的 browserengine.dll 里，没有公开的 HTTP
    接口。本脚本进入你自己的客户端进程，调用它自己的导出函数。
    不修改磁盘上任何文件，不注入外部代码，不发任何网络请求。

【安全边界 —— 代码里强制校验，任一不符立即退出】
    1. 目标进程的可执行文件必须严格位于百度网盘安装目录：
       %APPDATA%\\baidu\\BaiduNetdisk\\module\\BrowserEngine\\BaiduNetdiskUnite.exe
    2. 目标进程必须已加载百度自己的 browserengine.dll
    3. 只调用该 dll 的三个导出：
       browser_engine_refresh_flow
       browser_engine_query_ticket_speedup
       browser_engine_consume_ticket_speedup
    4. 不读取账号密码、不上传任何数据、不联网

    这不是一个通用进程注入工具，只对上面那一个路径的程序有效。

【用法】
    先打开百度网盘客户端并保持登录，然后：
        python bd_use.py
"""
import ctypes
import ctypes.wintypes as wt
import os
import struct
import sys
import time

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
psapi = ctypes.WinDLL("psapi")
u32 = ctypes.WinDLL("user32")

u32.SetProcessDPIAware()

k32.GetModuleHandleW.restype = ctypes.c_void_p
k32.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
k32.OpenProcess.restype = wt.HANDLE
k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
k32.QueryFullProcessImageNameW.restype = wt.BOOL
k32.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, ctypes.c_wchar_p,
                                           ctypes.POINTER(wt.DWORD)]
k32.VirtualAllocEx.restype = ctypes.c_void_p
k32.VirtualAllocEx.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_size_t, wt.DWORD, wt.DWORD]
k32.WriteProcessMemory.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                   ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
k32.ReadProcessMemory.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                  ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
k32.CreateRemoteThread.restype = wt.HANDLE
k32.CreateRemoteThread.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_size_t,
                                   ctypes.c_void_p, ctypes.c_void_p, wt.DWORD,
                                   ctypes.POINTER(wt.DWORD)]
psapi.EnumProcessModulesEx.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD,
                                       ctypes.POINTER(wt.DWORD), wt.DWORD]
psapi.GetModuleBaseNameW.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_wchar_p, wt.DWORD]

# ---- 目标白名单：只认这一个可执行文件 ----
TARGET_EXE = "baidunetdiskunite.exe"
TARGET_DIR_PARTS = ("baidu", "baidunetdisk", "module", "browserengine")
DLL_NAME = "browserengine.dll"

# 允许调用的导出，仅此三个
ALLOWED_EXPORTS = frozenset((
    "browser_engine_refresh_flow",
    "browser_engine_query_ticket_speedup",
    "browser_engine_consume_ticket_speedup",
))

# ---- x64 小段机器码：从参数块装载 4 个寄存器参数后 call ----
# 参数块布局: [0]=argc  [8..0x20]=args  [0x28]=函数地址
# 作用等同 C 的:  fn(a0, a1, a2, a3)
STUB = bytes([
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

MEM_COMMIT_RESERVE = 0x3000
PAGE_RWX = 0x40
PROCESS_QUERY_LIMITED = 0x1000
PROCESS_ALL = 0x1F0FFF


def _fix_console():
    try:
        k32.SetConsoleOutputCP(65001)
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _log(msg):
    print(msg, flush=True)


def _proc_exe_path(pid):
    """取进程映像路径；失败返回 None"""
    h = k32.OpenProcess(PROCESS_QUERY_LIMITED, False, pid)
    if not h:
        return None
    try:
        buf = ctypes.create_unicode_buffer(2048)
        size = wt.DWORD(2048)
        ok = k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size))
        return buf.value if ok else None
    finally:
        k32.CloseHandle(h)


def _is_target(pid):
    """严格校验：必须是百度网盘 BrowserEngine 目录下的 BaiduNetdiskUnite.exe"""
    path = _proc_exe_path(pid)
    if not path:
        return False
    low = path.lower().replace("/", "\\")
    if not low.endswith(TARGET_EXE):
        return False
    return all(part in low for part in TARGET_DIR_PARTS)


def find_target_pid():
    """枚举顶层窗口，找到通过校验的百度网盘主窗口进程"""
    hits = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(hwnd, _):
        n = u32.GetWindowTextLengthW(hwnd)
        if n > 0:
            buf = ctypes.create_unicode_buffer(n + 1)
            u32.GetWindowTextW(hwnd, buf, n + 1)
            if "百度网盘" in buf.value and u32.IsWindowVisible(hwnd):
                r = wt.RECT()
                u32.GetWindowRect(hwnd, ctypes.byref(r))
                if r.right - r.left > 800:
                    pid = wt.DWORD()
                    u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                    if _is_target(pid.value):
                        hits.append(pid.value)
        return True

    u32.EnumWindows(cb, 0)
    return hits[0] if hits else None


def _dll_path():
    return os.path.join(os.environ["APPDATA"], "baidu", "BaiduNetdisk",
                        "module", "BrowserEngine", DLL_NAME)


class Client:
    def __init__(self):
        self.pid = find_target_pid()
        if not self.pid:
            raise RuntimeError("没找到百度网盘主窗口。请先打开客户端并登录。")

        self.h = k32.OpenProcess(PROCESS_ALL, False, self.pid)
        if not self.h:
            raise RuntimeError("无法打开客户端进程 pid=%d err=%d"
                               % (self.pid, ctypes.get_last_error()))

        self.base = self._find_dll_base()
        if not self.base:
            raise RuntimeError("客户端 %d 未加载 %s，可能版本不匹配" % (self.pid, DLL_NAME))

        dll = _dll_path()
        if not os.path.isfile(dll):
            raise RuntimeError("本机没有 %s，请确认百度网盘已安装" % dll)
        self.local = ctypes.WinDLL(dll)
        self.local_base = k32.GetModuleHandleW(DLL_NAME)

        # 常驻内存块：故意永不释放。
        # 原因：native 会把回调指针存下来异步调用，提前释放会导致客户端崩溃。
        self.blk = k32.VirtualAllocEx(self.h, None, 0x2000, MEM_COMMIT_RESERVE, PAGE_RWX)
        if not self.blk:
            raise RuntimeError("VirtualAllocEx 失败 err=%d" % ctypes.get_last_error())
        self._wp(self.blk + 0x300, STUB)
        self._wp(self.blk + 0x200, b"\xC3")    # 回调桩：直接 return
        self._wp(self.blk + 0x220, b"\x00")    # 空字符串

    def _find_dll_base(self):
        mods = (ctypes.c_void_p * 1024)()
        need = wt.DWORD()
        psapi.EnumProcessModulesEx(self.h, ctypes.cast(mods, ctypes.c_void_p),
                                   ctypes.sizeof(mods), ctypes.byref(need), 3)
        name = ctypes.create_unicode_buffer(260)
        for i in range(need.value // ctypes.sizeof(ctypes.c_void_p)):
            m = mods[i]
            if not m:
                continue
            psapi.GetModuleBaseNameW(self.h, ctypes.c_void_p(m), name, 260)
            if name.value.lower() == DLL_NAME:
                return m
        return None

    def _wp(self, addr, data):
        w = ctypes.c_size_t(0)
        k32.WriteProcessMemory(self.h, ctypes.c_void_p(addr), data, len(data),
                               ctypes.byref(w))

    def _rd(self, addr, n):
        b = ctypes.create_string_buffer(n)
        w = ctypes.c_size_t(0)
        k32.ReadProcessMemory(self.h, ctypes.c_void_p(addr), b, n, ctypes.byref(w))
        return b.raw[:w.value]

    def _rva(self, name):
        return ctypes.cast(getattr(self.local, name), ctypes.c_void_p).value - self.local_base

    def call(self, name, args, timeout_ms=30000):
        """args 记号：'OUT'=int64* 输出槽  'CB'=回调桩  'E'=空字符串"""
        if name not in ALLOWED_EXPORTS:
            raise RuntimeError("拒绝调用白名单外的导出: %s" % name)
        fn = self.base + self._rva(name)
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
        th = k32.CreateRemoteThread(self.h, None, 0, ctypes.c_void_p(self.blk + 0x300),
                                    ctypes.c_void_p(self.blk), 0, ctypes.byref(tid))
        if not th:
            return None, None
        k32.WaitForSingleObject(th, timeout_ms)
        code = wt.DWORD()
        k32.GetExitCodeThread(th, ctypes.byref(code))
        k32.CloseHandle(th)
        raw = self._rd(out_slot, 8)
        out = struct.unpack("<q", raw)[0] if len(raw) == 8 else None
        return code.value, out

    def query(self):
        return self.call("browser_engine_query_ticket_speedup", ["OUT", 10, "CB"])

    def consume(self):
        return self.call("browser_engine_consume_ticket_speedup", ["OUT", 10, "E", "CB"])

    def close(self):
        k32.CloseHandle(self.h)


def main():
    try:
        c = Client()
    except RuntimeError as e:
        _log("[x] %s" % e)
        return 1

    _log("[*] 已连接百度网盘  pid=%d" % c.pid)
    try:
        ret, _ = c.query()
        _log("[*] 查券 ret=%s" % ret)

        _log("[*] 正在用券 ...")
        ret, out = c.consume()
        if ret != 0:
            _log("[x] 用券失败 ret=%s" % ret)
            _log("    常见原因：没有可用加速券 / 当前没有正在下载的任务")
            return 2

        _log("[+] 用券成功  状态=%s" % out)
        time.sleep(3)
        c.query()
        _log("[+] 完成。切到客户端「传输」页可以看到速度变化。")
        return 0
    finally:
        c.close()   # 常驻块故意不释放


if __name__ == "__main__":
    _fix_console()
    _rc = main()
    if "--no-pause" not in sys.argv:
        try:
            if sys.stdin and sys.stdin.isatty():
                input("\n按回车退出...")
        except Exception:
            pass
    sys.exit(_rc)
