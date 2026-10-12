# M5 可用性修复 · 独立验收测试报告（第 3 轮 / 含第 2 轮复验 / 黑盒）

| 项目 | 内容 |
|---|---|
| 验收对象 | aihot「M5 可用性修复」（代理解耦 / 预检分级 / 跳过留痕 / 告警降级 / 报告区分） |
| 验收方式 | 黑盒：只看 CLI 退出码、stdout/stderr、真实 `run_daily.bat` 行为、假服务收到的请求、DB 表与文件系统 |
| 测试脚本 | `tests/independent/m5r_support.py`、`m5r_preflight.py`、`m5r_fetch.py`、`m5r_skip.py`、`m5r_alert.py`、`m5r_report.py`、`m5r_render_alert.py`、`m5r_bat_local.py`、`m5r_round2_proxy.py`、`m5r_run_all.py` |
| 证据目录 | 第 1 轮：`reports/evidence/m5r/`；**第 2 轮复验：`reports/evidence/m5r/round2/`** |
| 环境 | Windows / 项目 venv（`.venv\Scripts\python.exe`）/ Clash **未运行**（127.0.0.1:7897 无监听、注册表 ProxyEnable=0） |
| 隔离手段 | 临时配置 + 临时 data/log 目录 + 本地假服务（假 RSS / 假 LLM / 假企微 / 假 SMTP / 假 HTTP 代理）；staged `run_daily.bat`（字节复制 + `.venv` 联接）；第 2 轮起企微/LLM/邮件在配置中关闭或用本地 webhook 的 Config 副本，**不再借道代理截获真实域名** |
| 真实资产保护 | 全程未改 `src/**`、`config.yaml`（mtime 2026-09-28）、`run_daily.bat`（sha256 `355bca17…`，ASCII + 31 行 CRLF）与真实 `data/`、`logs/`（每套件前后快照比对均一致） |
| 测试脚本 lint | ✅ `ruff check .` 全绿（本轮从 23 条告警修到 0，仅改 `tests/independent/m5r_*.py`，未动 `src/**` 与旧脚本） |

> 硬性约束：不修改 `src/**`；不向真实企微/邮箱/LLM 发送任何消息；只连 localhost。
> 免责声明（如实记录）：
> ① 第 1 轮调试期间，Windows 环境变量大小写覆盖曾使本机对真实 `qyapi.weixin.qq.com:443` 发出 TLS 握手
> （证书校验即失败，**未发送 HTTP 请求或消息**）；已定位并修正。
> ② 第 2 轮按要求重跑既有 `m4_alert.py` 时，因其"用代理截获假企微"的手段被 P1 修复淘汰，其告警发送
> **直连真实 qyapi 域名**（使用测试假 key，服务端返回 `errcode=93000` 拒绝，**无任何消息送达**）；
> 该脚本此后无法在"只连 localhost"约束下运行，已从默认全量运行中加 `AIHOT_M5R_SKIP_M4_ALERT=1` 开关隔离。

## 一、总体判定

| # | 声明 | 判定 |
|---|---|---|
| 1 | 代理不可用不再拦截运行（`check --quick` rc=0，代理为可降级提示） | ✅ **PASS** |
| 2 | 企微 / LLM 必须直连（不走配置代理，也不走环境代理） | ✅ **PASS**（第 2 轮复验加强：环境代理同样被忽略） |
| 3 | 代理可用时采集确实走代理 | ✅ **PASS** |
| 4 | 代理不可用时采集降级直连而非跳过 | ✅ **PASS** |
| 5 | 跳过必须留痕（rc=2 / 不进入 run / `aihot skip` / runs 记录 / 告警尝试） | ✅ **PASS** |
| 6 | 告警三级降级、绝不静默 | ✅ **PASS**（P2-1 修复后 `alert` 输出不再静默，第 2 轮复验通过） |
| 7 | 报告区分「跳过」与「没运行」 | ✅ **PASS** |
| 8 | 不破坏既有行为（回归） | ⚠️ **部分符合**：无 M5 引入的功能性回归；但 `m4_alert.py` 的本地截获手段已被修复淘汰（见 §五） |

**第 2 轮复验要点（P1 / P2-1）：P1 ✅ 已修复、P2-1 ✅ 已修复 → P0 / P1 清零。**

```
第 2 轮复验全量（localhost-only，8 套件 75 项断言）：
[PASS] m5r_preflight rc=0 (9.1s)      [PASS] m5r_alert rc=0 (5.0s)
[PASS] m5r_fetch rc=0 (14.3s)         [PASS] m5r_report rc=0 (3.6s)
[PASS] m5r_skip rc=0 (17.6s)          [PASS] m5r_render_alert rc=0 (2.0s)
[PASS] m5r_round2_proxy rc=0 (13.4s)  [PASS] m5r_bat_local rc=0 (6.8s)
TOTAL: 8 passed / 0 failed
（`m4_alert.py` 单独执行结果见 §五；因会直连真实域名，默认全量运行已用
 AIHOT_M5R_SKIP_M4_ALERT=1 隔离，证据见 reports/evidence/m5r/round2/regression/）
```

第 1 轮（修复前状态）全量结果保留在 `reports/evidence/m5r/run-all-summary.json`：7 套件通过，
`m4_alert.py` 11/14（3 项旧断言失败，triage 见 §五）。

## 二、第 2 轮复验（P1 / P2-1 修复验证）

### P1 — 企微 / LLM / 邮件显式直连，忽略环境变量代理：✅ PASS

复验方式：进程环境同时设置 `HTTPS_PROXY` / `HTTP_PROXY` / `ALL_PROXY = http://127.0.0.1:1`（坏端口）。

| 检查 | 结果 | 证据 |
|---|---|---|
| R2.1 CLI：`check --quick` rc=0 且 LLM `[OK ]`（环境代理若生效必然失败） | ✅ | `round2/m5r_round2_proxy/1-check-quick-env-proxy-ignored.txt`、`m5r_round2_proxy.json` R2.1/R2.1b |
| R2.2 `checks.probe_wecom` 仍到达本地假企微（`errcode=0`） | ✅ | 同上 R2.2（假企微 GET 记录） |
| R2.3 `checks.probe_llm` 仍成功 | ✅ | R2.3 |
| R2.4 `net.HttpClient(proxy=None)` 仍直连本地假 RSS（HTTP 200） | ✅ | R2.4 |
| R2.5 `WeComNotifier` 推送仍到达本地假企微（收到 text） | ✅ | R2.5 |
| R2.6 反证：目标端口关闭时 `probe_wecom` 失败、`WeComNotifier` 抛 `NotifyError` | ✅ | R2.6 / R2.6b（不是"永远成功"） |
| R2.7 函数级：企微不可用（本地关闭端口）→ 自动改用邮件（假 SMTP 收到） | ✅ | `round2/m5r_round2_proxy/2-send-alert-wecom-down-email.txt`（`channels=['邮件']`） |

代码侧确认（只读，未改动）：`net.HttpClient`、`delivery.wecom.WeComNotifier`、`checks.probe_wecom`、
`checks.probe_llm` 均显式 `trust_env=False`。

### P2-1 — `aihot alert` 兜底不再静默：✅ PASS

| 检查 | 结果 | 证据 |
|---|---|---|
| `aihot alert` 渠道全灭：rc=1 且输出 `[WARN] 告警未送达：可用通道均失败或未启用，已写入本地兜底（本地文件(ALERTS.log)）` | ✅ | `round2/m5r_round2_proxy/3-p21-alert-and-skip.txt`、`m5r_round2_proxy.json` P2-1 |
| `aihot skip` 路径不受影响：rc=0 且打印 `[OK] 已记录跳过（通道: 本地文件(ALERTS.log)）` | ✅ | 同上 P2-1b |
| 两条命令的兜底内容均写入 `logs/ALERTS.log`（含哨兵） | ✅ | 同上 P2-1c |
| CLI 套件复验：渠道全灭 rc=1 且含「未启用」「本地兜底」字样 | ✅ | `round2/m5r_alert/2-alert-fallback-file.txt`、`m5r_alert.json` 6.4 |
| 既有 `m4_alert.py` 4.5（企微未启用时 rc=1 且提示明确）**恢复通过** | ✅ | `round2/regression/m4_alert.json` 4.5 |

## 三、逐条判定与证据（第 1 轮 + 第 2 轮复验）

### 声明 1 — 代理不可用不再拦截运行：✅ PASS

场景：配置代理 = `http://127.0.0.1:1`（关闭端口）。

* 第 1 轮（CLI + 假服务）：`check --quick` rc=0；代理项 `[WARN]`"将降级为直连"；同一次运行企微、LLM 均 `[OK ]`
  （`evidence/m5r/m5r_preflight/1-check-quick-proxy-down.txt`、`m5r_preflight.json` 1.1–1.5）。
* 第 2 轮（环境代理也是坏端口）：rc=0、代理 `[WARN]`、LLM `[OK ]`、无 `[ERR]`
  （`round2/m5r_preflight/1-check-quick-proxy-down.txt`、`m5r_preflight.json` 1.1–1.5c）。

### 声明 2 — 企微 / LLM 直连：✅ PASS（配置代理 + 环境代理双重解耦）

* 第 1 轮正向：配置代理坏端口时企微探测到达本地假企微（CONNECT+GET）、LLM `/v1/models` 命中，两项 `[OK ]`；
  反证：假服务关闭后两项 `[ERR]` 且 rc=1（`m5r_preflight.json` 1.4/1.5/2.1–2.5）。
* 第 2 轮加强：环境 `HTTPS_PROXY/HTTP_PROXY/ALL_PROXY` 全为坏端口时，`probe_wecom` / `probe_llm` /
  `WeComNotifier` / `net.HttpClient` 仍全部直达本地假服务（`m5r_round2_proxy.json` R2.1–R2.5），
  反证 R2.6 关闭端口必失败。
* 说明：P1 修复后，CLI 级已无法用"代理截获 qyapi"的方式做本地观察（这正是修复的目标）；
  企微探测的直连验证改为**本地 webhook 的 Config 副本**进程内调用（夹具用途，见脚本注释）。

### 声明 3 — 代理可用时采集走代理：✅ PASS

假代理记录到绝对 URI 请求行 `GET http://127.0.0.1:1/feed.xml`（目标端口关闭）且 `fetch --dry-run` rc=0、
抓到 8 条（`m5r_fetch.json` 3.1–3.3、`raw/1-fake-proxy-requests.json`；第 2 轮复跑同样通过）。

### 声明 4 — 代理不可用时降级而非跳过：✅ PASS

rc=0、直连假 RSS 抓到 8 条、日志 `代理不可用…降级为直连`；反证：源也不可达时 rc=1 且提示"全部信息源采集失败"
（`m5r_fetch.json` 4.1–4.6；第 2 轮复跑同样通过）。

### 声明 5 — 跳过必须留痕：✅ PASS

staged `run_daily.bat`：rc=2、无 run 段、`PREFLIGHT FAILED - skip today`、`runs` 表 `status='skipped'` 且
`stage_stats.reason` 含原因、告警已尝试（假 SMTP 收到含原因的跳过告警）、无日报半成品；
告警全灭与配置损坏两种对抗场景均兜底写 `logs/ALERTS.log`（`m5r_skip.json` 5.0–5.12；第 2 轮 14/14 通过）。

### 声明 6 — 告警三级降级、绝不静默：✅ PASS

* 企微不可用 → 邮件：第 1 轮 CLI（假企微不可达）+ 第 2 轮函数级本地验证（R2.7，`channels=['邮件']`，假 SMTP 收到）。
* 企微与邮件都不可用 → `logs/ALERTS.log`：CLI rc=1、文件含哨兵（`m5r_alert.json` 6.4/6.5；第 2 轮复跑通过）。
* 邮件认证失败 → 本地文件（6.7）。
* P2-1 修复后，兜底时命令行有明确输出，不再出现"rc=1 但零输出"（R2.8）。

### 声明 7 — 报告区分「跳过」与「没运行」：✅ PASS

构造数据后：`有运行记录: 4 天（其中跳过 2 天）（缺 1 天）`、跳过明细含两条原因、`无运行记录` 仅真正的空白天；
对抗：同一天既有 skipped 又有 delivered → 按送达计（`m5r_report.json` 7.1–7.9、`m5r_report/2-…txt`；第 2 轮复跑通过）。

### 声明 8 — 回归：⚠️ 部分符合（无本轮新增功能回归；详见 §五）

* `m5r_bat_local.py`（`m4_bat.py` 的 localhost 断言子集）10/10 PASS：staged `run_daily.bat --dry-run` rc=0、
  预检无 ERR、日报三件套、dry-run 不推送、配置损坏 rc=3、真实资产未动（第 1、2 轮均通过）。
* staged 环境 `aihot run --dry-run` 正常。
* `m4_alert.py`：第 2 轮 7/14（4.5 因 P2-1 恢复通过；其余失败全部源于"代理截获"手段被修复淘汰，见 §五）。
* `m4_bat.py` 第 4 段（真实公网源采集）未原样执行：其通过前提是 Clash 代理可用，与本轮"禁止公网"约束冲突。

## 四、发现问题

### P1 —（已修复）企微/LLM/邮件路径仍受环境变量代理接管

* 第 1 轮复现：环境 `HTTPS_PROXY = http://127.0.0.1:1` 时企微探测 `[ERR]`（`evidence/m5r/m5r_preflight/3-check-quick-env-proxy-down.txt`）。
* 主控修复：`net.HttpClient`、`delivery.wecom.WeComNotifier`、`checks.probe_wecom`、`checks.probe_llm` 显式 `trust_env=False`。
* **第 2 轮复验：✅ PASS**（R2.1–R2.7，证据见 §二）。**P1 清零。**

### P2-1 —（已修复）`aihot alert` 兜底时静默退出

* 第 1 轮复现：渠道全灭时 rc=1 且 stdout/stderr 全空，仅 `logs/ALERTS.log` 有记录。
* 主控修复：`cli._cmd_alert` 在未送达但已写兜底时输出 `[WARN] 告警未送达：可用通道均失败或未启用，已写入本地兜底（…）`。
* **第 2 轮复验：✅ PASS**（R2.8、`m5r_alert` 6.4、`m4_alert` 4.5 恢复通过）。**P2-1 清零。**

### P2-2 —（未解决，M4 遗留，不在本轮范围）仅长图降级（status=partial）不触发告警

* 复验：`m5r_render_alert.json` 8b.3：`status=partial；新增告警=0`；对照 `M4-test-report.md` P2-6。

### P2-3 —（未解决，测试资产债务，本轮不允许改旧脚本）`m4_alert.py` 的本地截获手段已失效

* P1 修复后 `m4_alert.py` 的告警发送直连真实 qyapi 域名（假 key 被 `errcode=93000` 拒绝，无消息送达），
  其 4.1–4.3 / 4.8 / 4.9 / 4.11 / 4.13 断言在 localhost-only 前提下**无法再通过**；
  4.5 已恢复通过。建议下一轮把这几项替换为 `m5r_*` 的本地验证方式（`local_wecom_config` 夹具）。
* 同时 `m4_bat.py` 的 staged 场景也依赖同类截获，按当前约束无法原样运行（`m5r_bat_local.py` 已覆盖）。

### P2-4 —（新，信息性）`trust_env=False` 同时会忽略 `SSL_CERT_FILE` / `SSL_CERT_DIR`

* httpx 在 `trust_env=False` 时不再读取 SSL_CERT_FILE/SSL_CERT_DIR；若部署环境依赖企业 MITM 证书
  （常见于同时设置环境代理的公司网络），企微/LLM 直连将改用 certifi 内置 CA。
* 当前部署（国内直连服务 + 公网 CA）不受影响；仅作记录，不建议在本轮改动。

## 五、回归 triage（声明 8）

| 对象 | 第 1 轮 | 第 2 轮 | 结论 |
|---|---|---|---|
| `m5r_bat_local.py`（m4_bat 的 localhost 子集） | 10/10 | 10/10 | 无回归 |
| staged `run --dry-run` | 正常 | 正常 | 无回归 |
| `m4_alert.py` | 11/14 | 7/14 | **非产品回归**：4.5 恢复通过；其余失败均为"截获手段失效" |
| `m4_bat.py`（真实公网段） | 未执行 | 未执行 | 约束冲突（禁止公网），以 localhost 子集替代 |

`m4_alert.py` 第 2 轮失败项明细（`round2/regression/m4_alert.json`、`run-m4_alert_regression.log`）：

| 项 | 现象 | triage |
|---|---|---|
| 4.5 | rc=1 且提示明确 | ✅ **恢复通过**（P2-1 修复） |
| 4.1 / 4.2 / 4.3 | alert 未到达假企微（真实域名直连，假 key 被拒 errcode=93000） | 截获手段失效（测试资产），非产品缺陷 |
| 4.8 / 4.9 | 推送失败告警未到达假企微 | 同上 |
| 4.11 | 渲染失败告警未到达假企微（产品侧仍会调用 `_alert`，`m5r_render_alert` 8b.2 本地验证通过） | 同上 |
| 4.13 | 长图降级不告警 | M4 遗留 P2-2 |

## 六、放行意见

**建议放行**（进入第 2 轮观察期），理由：

1. 8 条声明中 1–7 全部 PASS（含独立反证）；第 2 轮复验中 P1、P2-1 两项修复验证通过 → **P0 / P1 清零**；
2. 声明 8 的失败项全部 triage 为"测试手段被修复淘汰 + M4 已知遗留"，无 M5 引入的功能性回归；
3. 测试脚本 `ruff check .` 全绿；真实资产（`data/`、`logs/`、`config.yaml`、`run_daily.bat`）零改动。

遗留（不阻塞放行）：

* P2-2：长图降级不告警（M4 遗留，内容照常送达、有 `[WARN]` 与 runs 记录）；
* P2-3：`m4_alert.py` / `m4_bat.py` 的旧截获手段需更新（下一轮用 `m5r_*` 的本地夹具体系替代）；
* P2-4：`trust_env=False` 会忽略 `SSL_CERT_FILE/SSL_CERT_DIR`（信息性，当前部署无影响）。

观察期建议：首日人工确认 `aihot report --days 2`（若跳过应显示"跳过明细（含原因）"）；
严格本地回归使用
`.\.venv\Scripts\python.exe tests\independent\m5r_run_all.py`（默认已隔离 m4_alert）；
需要复跑 `m4_alert.py` 时须接受其直连真实域名（假 key、无消息送达）。

## 七、复现方式

```powershell
cd D:\信息收集整理\aihot
$env:PYTHONIOENCODING = "utf-8"
# 严格 localhost-only 全量（跳过会直连真实域名的 m4_alert）：
$env:AIHOT_M5R_SKIP_M4_ALERT = "1"
.\.venv\Scripts\python.exe tests\independent\m5r_run_all.py
# 指定本轮证据目录（默认 m5r；第 2 轮复验用 m5r/round2）：
$env:AIHOT_M5R_EVIDENCE = "m5r/round2"
Remove-Item Env:AIHOT_M5R_SKIP_M4_ALERT
```

单套件（例如）：`.\.venv\Scripts\python.exe tests\independent\m5r_round2_proxy.py`
