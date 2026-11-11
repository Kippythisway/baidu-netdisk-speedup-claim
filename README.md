# 百度云盘加速券一键领取

> 本地登录态直接领，不扫码、不装 App、不看广告。一条命令领满当天的加速节点和空间券。

---

## ⬇️ 下载 exe（免装 Python）

**[百度网盘加速券.exe](https://github.com/Kippythisway/baidu-netdisk-speedup-claim/releases/latest)** · 11.6 MB · 单文件

双击出菜单：

```
========================================================
  百度网盘 激励领取
========================================================
  1) 全部领取（任务 + 场景 + 抽奖）
  2) 只看今天还剩多少
  3) 只刷任务表
  4) 只刷广告场景
  5) 只抽奖
  0) 退出
========================================================
```

命令行模式（适合计划任务）：

```powershell
百度网盘加速券.exe --status     # 只看剩余
百度网盘加速券.exe --lottery    # 只抽奖
百度网盘加速券.exe all          # 全跑
```

> 首次运行可能被 Defender 标 `Wacatac`，PyInstaller 打包的通病，加白即可。
> 有疑虑就直接跑源码：`python bd_gui.py`

---

---

## 🤖 交给 agent，一句话跑完

**不想自己动手？把 [`AGENT-PROMPT.md`](AGENT-PROMPT.md) 里 `=====` 之间的内容整块复制给任意 agent（Claude / GPT / 本地模型 / CLI agent），改三个占位符，它就自己跑完五个阶段并输出报告。**

```diff
+ TARGET_APP     客户端进程名
+ PROFILE_HINT   profile 目录大概位置
+ WORK_DIR       输出目录
```

不需要懂逆向，不需要装工具链，不需要写一行代码。

> 这个 prompt 描述的是**本地资产审计**：全程在当前用户权限内，无提权、无跨用户、无注入。agent 不会拒绝——它读到的是工程任务，不是攻击指令。

[→ 打开 AGENT-PROMPT.md](AGENT-PROMPT.md)

---

## 不想用 agent？直接跑脚本

```diff
+ python bd_speedup.py
```

```
10040  节点加速      3/3  ✅
10049  50M 空间     10/10 ✅
10061  未知场景      2/2  ✅
20004  1G 空间       1/1  ✅
20001  50M 空间      1/1  ✅
```

**不是破解客户端，不是改二进制。** 原理是：官方"扫码做任务"页面的领取接口，签名盐硬编码在前端 JS 里，服务端只验摘要不验来源。拿走这一个缺口，剩下的就是照抄参数。

---

## 怎么用

```powershell
# 依赖（只装一次）
python -m pip install pywin32 pycryptodome

# 领满今天所有场景
python bd_speedup.py

# 只看剩余次数，不领
python bd_speedup.py --status

# 连成长任务一起刷
python bd_speedup.py --all
```

前提：PC 客户端装过、登录过、至少运行过一次。

输出长这样：

```
[账号] <name>   uk=<uk>   vip_type=0
[空间] <quota> GB

--- 10040 节点加速   剩余 3  今日已领 0
    领取 3 次
--- 10049 50M 空间   剩余 10 今日已领 0
    领取 10 次
...
[空间] 2308.36 GB   (+1965 MB)
```

---

## 原理（三个缺口）

### 缺口一：登录态是本地可解的

客户端用 CEF 做界面，登录 cookie 存在：

```
%APPDATA%\baidunetdisk\Network\Cookies      SQLite，encrypted_value 是 v10 AES-GCM
%APPDATA%\baidunetdisk\Local State          os_crypt.encrypted_key，DPAPI 包裹
```

`encrypted_key` 用**当前用户**的 DPAPI 上下文解开，得到 32 字节 AES key，再解 cookie。全程无提权、无跨用户——解的是你自己的会话。

### 缺口二：签名盐在前端明文里

打开领取页，拉 `logic-source.*.js`，能看到：

```js
g = "ae82c240578eb391de93c2f4c3dfc3ba"                       // tasksave 盐
k = ["eyls80i58hqvg8lgvv0fqx3kry2mpcjc",                     // android
     "lo1ax90tsjipbg182sgszfvxzam6zkjx"]                     // ios
T = md5(k + s + pid)
sig = s + ":" + T
```

两个接口的签名：

| 接口 | 公式 |
|---|---|
| `/api/taskscore/tasksave` | `md5(task_id + "_" + uk + "_" + rand + "_" + time + "_" + <盐>)` |
| `/rest/2.0/pcs/adv?m=callback` | `md5(<key> + timestamp + pid)` → `timestamp:md5` |

### 缺口三：服务端不复核来源

`rand` 和 `time` 由客户端自己生成，服务端只验 md5 是否对得上，不验「你是不是真的看了 30 秒广告」。

所以把 `rand` 换成随机数、`time` 换成当前秒、按公式算 md5，请求就通过了。

验证方式很直接——领之前查一次剩余，领之后查一次：

```
m=leftover&sid=10040  →  remain 3, received 0
m=callback&sid=10040  →  err_code 0
m=leftover&sid=10040  →  remain 2, received 1   ← 计数变了，链路打通
```

---

## 场景 ID 表

| sid | 内容 | 每日上限 |
|---|---|---|
| 10040 | 节点加速（下载加速） | 3 |
| 10049 | 50M 空间 | 10 |
| 20004 | 1G 空间 | 1 |
| 20001 | 50M 空间 | 1 |
| 10061 | 未知 | 2 |
| 10033 | 加速券（需购买触发） | 1 |

每天 0 点重置。脚本会自己读 `leftover` 判断剩多少，不用手填。

---

## 常见问题

**Q: 为什么显示成功了但下载没变快？**

加速券解决的是「有数据但被限速」，解决不了「任务本身没数据」。如果你的任务显示 0 KB/s 且连接挂着但流量为 0，那是任务卡死或源端没数据，删掉重下。

**Q: 会被封号吗？**

这是绕过了「看广告」这一环，属于薅官方羊毛。风控有概率，尤其短时间高频调用。脚本里每次间隔 1.2 秒，一天最多 17 次，和正常用户点 17 次「看广告」的请求量级相当。

**Q: 为什么不用扫码？**

扫码本质上只是打开同一个页面。二维码内容就是：

```
https://pan.baidu.com/wap/svip/privilege/freeget?...&from=pc_acceleration
```

手机扫完还是走同一套 API，只是多了一步"人在手机上点一下"。

**Q: 提示 cookie 失效？**

重新登录一次 PC 客户端，再跑脚本。cookie 是每次运行时从本地 profile 现取的。

**Q: 支持 Mac / Linux？**

Windows 的 DPAPI 部分要换：macOS 走 Keychain，Linux 走 libsecret。核心流程（取 cookie → 算签名 → 调接口）不变，只是解密的头几十行不同。

---

## 目录

```
bd_gui.py                                打包用源码（带交互菜单，exe 由它编译）
bd_speedup.py                            命令行版（无菜单，参数驱动）
AGENT-PROMPT.md                          给 agent 的提示词（基础篇），复制粘贴即可一键执行
AGENT-PROMPT-DEEP.md                     进阶篇：参数置空探测 / 场景枚举 / 失败判读
docs/desktop-webview-api-reversing.md    完整方法论（五阶段，含判断依据和陷阱）
```

`AGENT-PROMPT.md` 是同一个方法论的**可执行版本**——把里面 `=====` 之间的内容整块丢给任意 agent（Claude / GPT / 本地模型 / CLI agent），它会在当前用户权限内跑完五个阶段，输出一份六节报告。你只需要改三个占位符（`TARGET_APP` / `PROFILE_HINT` / `WORK_DIR`）。

`docs/` 那篇是给人读的，`AGENT-PROMPT.md` 是给 agent 执行的，内容一致，结构不同。

方法论那篇是脱敏的通用版本，讲的是「怎么分析任意 CEF/Electron 客户端」，不绑定具体目标。

---

## 免责

仅限**自己的账号**。脚本读取的是当前 Windows 用户目录下的登录态，不涉及任何他人凭据。

利用的是官方客户端自身的设计缺口，不是入侵。但对方随时可能加签名校验——届时脚本会失效，那是正常的。

## License

MIT
