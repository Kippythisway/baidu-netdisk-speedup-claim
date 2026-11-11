#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
百度网盘 激励体系 一站式领取（本地账号）

覆盖三条独立链路：
  A. taskscore 全量任务
  B. /rest/2.0/pcs/adv 广告场景
  C. 抽奖

双击运行 = 交互菜单；带参数运行 = 静默模式（适合计划任务）

用法:
    bd_max.exe                 交互菜单
    bd_max.exe all             全跑
    bd_max.exe --status        只看剩余
    bd_max.exe --tasks         只刷任务
    bd_max.exe --adv           只刷场景
    bd_max.exe --lottery       只抽奖
    bd_max.exe --no-pause      跑完不暂停（脚本调用用）
"""
import base64, hashlib, json, os, random, sqlite3, ssl, sys, time
import urllib.parse, urllib.request

# ---- 强制 UTF-8 输出（打包成 exe 后中文不乱码）
try:
    import ctypes
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
    say("  百度网盘 激励领取", B)
    say("=" * 56, C)
    say("  1) 全部领取（任务 + 场景 + 抽奖）")
    say("  2) 只看今天还剩多少")
    say("  3) 只刷任务表")
    say("  4) 只刷广告场景")
    say("  5) 只抽奖")
    say("  0) 退出")
    say("=" * 56, C)
    return input("  请选择 [1]: ").strip() or "1"

def main():
    argv = [a for a in sys.argv[1:]]
    no_pause = "--no-pause" in argv
    args = set(a for a in argv if a != "--no-pause")
    interactive = not args

    try:
        cookie, uk, before = bootstrap()

        if interactive:
            choice = menu()
            if choice == "0":
                return
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
            do_all = ("all" in args) or (not ({"--tasks","--adv","--lottery"} & args))
            if do_all or "--tasks" in args:   run_tasks(cookie, uk, status_only)
            if do_all or "--adv" in args:     run_adv(cookie, uk, status_only)
            if do_all or "--lottery" in args: run_lottery(cookie, uk, status_only)

        after = quota(cookie)
        say(f"\n[空间] {after/1e9:.2f} GB   （{(after-before)/1e9:+.2f} GB）", G if after > before else "")
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
