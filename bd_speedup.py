"""
百度网盘 免费加速券 / 空间券 自动领取（本地账号，无需扫码）

原理
----
PC 客户端的 CEF 浏览器把登录态存在
    %APPDATA%\\baidunetdisk\\Network\\Cookies      (SQLite, AES-256-GCM)
    %APPDATA%\\baidunetdisk\\Local State           (DPAPI 保护的 master key)

奖励接口 /rest/2.0/pcs/adv 与 /api/taskscore/tasksave 的签名全部在
前端 JS 里硬编码，服务端不校验"真的看了广告"，只校验 md5 一致性：

    tasksave :  md5( task_id + "_" + uk + "_" + rand + "_" + time + "_" + ae82c240578eb391de93c2f4c3dfc3ba )
    pcs/adv  :  md5( eyls80i58hqvg8lgvv0fqx3kry2mpcjc + timestamp + pid )   →  "timestamp:md5"

用法
----
    python bd_speedup.py            # 领满今天所有场景
    python bd_speedup.py --status   # 只看剩余次数
    python bd_speedup.py --all      # 领满 + 顺手刷 taskscore 成长任务

场景 ID
-------
    10040  节点加速     3/天    ← 下载加速
    10049  50M 空间    10/天
    20004  1G 空间      1/天
    20001  50M 空间     1/天
    10061  未知         2/天
    10033  加速券(需购买)
"""
import base64, hashlib, json, os, sqlite3, ssl, sys, time
import urllib.parse, urllib.request

APP = os.path.expandvars(r"%APPDATA%\baidunetdisk")
WORK = os.path.dirname(os.path.abspath(__file__))

# ---- 硬编码前端常量（来自 privilegeFreeGet / logic-source bundle）----
PID_ANDROID = "1551078141121"
ZID = "67F22DA33A61F6B445E2618A87BC30786F"
CUID = "728D228D4F794773BDF6C26CE1ECCDB2|0"
APNA = "com.baidu.netdisk.platform.business.app"
KEY_ADV = "eyls80i58hqvg8lgvv0fqx3kry2mpcjc"          # android
KEY_ADV_IOS = "lo1ax90tsjipbg182sgszfvxzam6zkjx"
SALT_TASK = "ae82c240578eb391de93c2f4c3dfc3ba"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
REFERER = ("https://pan.baidu.com/wap/svip/privilege/freeget"
           "?na_immerse_theme=1&task_scene=reward_video_bidding_scene_hybrid_forward_download_speed"
           "&from=pc_acceleration")

SCENES = {
    10040: ("节点加速", 3),
    10049: ("50M 空间", 10),
    20004: ("1G 空间", 1),
    20001: ("50M 空间", 1),
    10061: ("未知场景", 2),
}


# ------------------------------------------------------------------ cookies
def extract_cookies():
    """从 CEF profile 解密出 BDUSS / STOKEN / BAIDUID / PANPSC"""
    import win32crypt
    from Crypto.Cipher import AES

    ls_path = os.path.join(APP, "Local State")
    ck_path = os.path.join(APP, "Network", "Cookies")
    if not (os.path.exists(ls_path) and os.path.exists(ck_path)):
        raise SystemExit("找不到 CEF profile，确认百度网盘装过并登录过")

    ls = json.load(open(ls_path, encoding="utf-8"))
    enc = base64.b64decode(ls["os_crypt"]["encrypted_key"])[5:]
    key = win32crypt.CryptUnprotectData(enc, None, None, None, 0)[1]

    tmp = os.path.join(WORK, "_cookies.db")
    with open(ck_path, "rb") as src, open(tmp, "wb") as dst:
        dst.write(src.read())

    def dec(blob):
        if not blob:
            return ""
        if blob[:3] in (b"v10", b"v11"):
            return AES.new(key, AES.MODE_GCM, nonce=blob[3:15]).decrypt_and_verify(
                blob[15:-16], blob[-16:]).decode("utf-8", "replace")
        return blob.decode("utf-8", "replace")

    con = sqlite3.connect(tmp)
    out = {}
    for host, name, enc_v, val in con.execute(
            "SELECT host_key,name,encrypted_value,value FROM cookies"):
        out.setdefault(host, {})[name] = dec(enc_v) if enc_v else val
    con.close()
    os.unlink(tmp)
    return out


def cookie_header(ck):
    pb = ck.get(".pan.baidu.com") or ck.get("pan.baidu.com") or {}
    bb = ck.get(".baidu.com") or ck.get("baidu.com") or {}
    return (f"BDUSS={pb.get('BDUSS','')}; STOKEN={pb.get('STOKEN','')}; "
            f"BAIDUID={bb.get('BAIDUID','')}; PANPSC={pb.get('PANPSC','')}; langs=zh-CN")


# ------------------------------------------------------------------ http
def _req(url, cookie, referer=REFERER):
    r = urllib.request.Request(url, headers={
        "User-Agent": UA, "Cookie": cookie, "Referer": referer,
        "Accept": "application/json, text/plain, */*"})
    try:
        with urllib.request.urlopen(r, timeout=20,
                                    context=ssl.create_default_context()) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode("utf-8", "replace"))
        except Exception:
            return {"err": f"HTTP {e.code}"}
    except Exception as e:
        return {"err": str(e)}


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
    if sid is not None:
        p["sid"] = str(sid)
    return p


def adv(mode, uk, cookie, sid=None):
    q = urllib.parse.urlencode(adv_params(mode, uk, sid), safe='[]{}":,|')
    return _req(f"https://pan.baidu.com/rest/2.0/pcs/adv?{q}", cookie)


def tasksave(uk, cookie, task_id, task_from="free_model"):
    rnd = str(__import__("random").randint(10**15, 10**16 - 1))
    ts = int(time.time())
    token = hashlib.md5(f"{task_id}_{uk}_{rnd}_{ts}_{SALT_TASK}".encode()).hexdigest()
    q = urllib.parse.urlencode({
        "uk": uk, "task_id": task_id, "rand": rnd, "time": ts, "token": token,
        "task_from": task_from, "version": "12.0.0", "clienttype": "0",
        "channel": "0", "jsToken": "", "app_id": "250528"})
    return _req(f"https://pan.baidu.com/api/taskscore/tasksave?{q}", cookie,
                referer="https://pan.baidu.com/disk/main")


# ------------------------------------------------------------------ main
def main():
    only_status = "--status" in sys.argv
    do_all = "--all" in sys.argv

    ck = extract_cookies()
    cookie = cookie_header(ck)

    info = _req("https://pan.baidu.com/rest/2.0/xpan/nas?method=uinfo", cookie)
    uk = info.get("uk")
    if not uk:
        raise SystemExit(f"cookie 失效: {info}")
    print(f"[账号] {info.get('baidu_name','?')}   uk={uk}   vip_type={info.get('vip_type')}")

    def quota():
        q = _req("https://pan.baidu.com/api/quota?checkfree=1&checkexpire=1"
                 "&clienttype=0&app_id=250528", cookie)
        return q.get("total", 0)

    before = quota()
    print(f"[空间] {before/1e9:.2f} GB\n")

    for sid, (name, cap) in SCENES.items():
        st = adv("leftover", uk, cookie, sid).get("task_count") or {}
        remain = st.get("remain_step_cnt")
        got = st.get("received_step_cnt")
        print(f"--- {sid} {name:<8} 剩余 {remain}  今日已领 {got}")
        if only_status:
            continue
        n = 0
        while True:
            r = adv("callback", uk, cookie, sid)
            if r.get("err_code") != 0:
                print(f"    stop: {r.get('err_code')} {r.get('err_msg')}")
                break
            n += 1
            time.sleep(1.2)
            chk = adv("leftover", uk, cookie, sid).get("task_count") or {}
            if (chk.get("remain_step_cnt") or 0) <= 0:
                break
            if n >= cap:
                break
        print(f"    领取 {n} 次")

    if do_all:
        print("\n=== taskscore 成长任务 ===")
        tl = _req("https://pan.baidu.com/api/taskscore/tasklist?task_from=free_model"
                  "&version=1.0.0&channel=0&clienttype=0&app_id=250528", cookie)
        for t in ((tl.get("result") or {}).get("list") or []):
            tid = t["task_id"]
            fin = 0
            while True:
                r = tasksave(uk, cookie, tid)
                if r.get("errno") != 0:
                    print(f"  task {tid}: {r.get('errno')} {r.get('show_msg','')}")
                    break
                fin += 1
                time.sleep(1.2)
                if fin >= 20:
                    break

    after = quota()
    print(f"\n[空间] {after/1e9:.2f} GB   (+{(after-before)/1e6:.0f} MB)")


if __name__ == "__main__":
    main()
