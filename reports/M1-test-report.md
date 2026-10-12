# M1 测试报告

- **测试时间**：2026-09-23（本机）
- **被测版本**：`aihot 0.1.0`（editable 安装，`pyproject.toml version=0.1.0`）
- **被测范围**：M1 采集层 —— `net.py`、`sources/`（base/registry/rss/hackernews）、`store.py`、`cli.py::fetch`、`checks.py::probe_sources`
- **测试环境**：Windows 11 + Python 3.12.10（项目 venv）；pytest 9.1.1；ruff 0.16.8；真实网络（5 个启用源均可达）
- **测试方式**：冷启动独立验证；**黑盒优先**（只用 CLI + SQLite 文件观察，不导入产品代码做断言），模块边界用白盒复算独立校验；断言脚本位于 `tests/independent/m1_*.py`，证据位于 `reports/evidence/m1/`；**未修改任何产品代码**
- **首轮结论（2026-09-23 基线）**：**FAIL（P0:0, P1:1, P2:4）**
- **第 2 轮回归结论（2026-09-23 · 最终）**：**PASS（P0:0, P1:0, P2:2）** —— 首轮 D1–D5 全部复验通过；第 2 轮预检新增发现的 socks5 代理 ImportError（D6）经修复后复验通过；无新增 P0/P1；残留 2 项 P2 均为 M0 已接受遗留
- **最终被测修订版指纹**：17 个文件整体 `sha256=ef0b95b768b4377c5bb879652f8bd7b91ccd2686433a4795ecfabedc25fc18b7`（`evidence/m1/regression2/revision-fingerprint.json`）；测试结束后比对 **0 个文件变化**（`revision-compare.txt`）

**首轮失败原因**（历史记录）：11 项验收中 10 项通过；第 11 项「工具链与回归」不通过 —— M0 已修复并验收通过的黑盒用例 `net-proxy-bad-port` 在 M1 回归失败（`proxy.url` 端口非法时 `check --net` 与 `fetch` 抛 traceback），定性为 **D1 [P1] 回归**；该缺陷已在第 2 轮复验通过。

> **安全与边界声明**
> - 全程**未向企业微信发送任何消息**（只对企业微信 webhook 做 GET 探测）；
> - 真实密钥（DeepSeek API key、企微 webhook）仅用于内存比对与脱敏扫描，报告与本目录证据中**不含密钥原文**；
> - 真实 `data/aihot.db` **只做只读检查**：测试前后快照一致（items=106，runs=2，无重复键）；
> - 所有写操作测试使用系统临时目录中的临时配置（`data_dir`/`log_dir` 指向 `%TEMP%\aihot-m1-tests\...`）。

---

## 回归验证（第 2 轮 · 最终）

- **回归时间**：2026-09-23（本机）
- **被测修订版**：首轮 FAIL 后开发者提交 D1–D5 修复与 socks5（D6）修复；最终修订版指纹 17 文件整体 `sha256=ef0b95b7…`（`regression2/revision-fingerprint.json`），测试结束后比对 **0 个文件变化**（`regression2/revision-compare.txt`）
- **回归方式**：D1–D6 逐项独立最小复现 + 全量重跑 14 个 `m1_*.py` + 6 个 `m0_*.py` + `ruff check .` + `pytest`
- **新结论：PASS（P0:0, P1:0, P2:2）** —— 无 P0/P1；2 项 P2 均为 M0 已接受遗留（`bad_alias`、`tzdata` 无上限）

> 说明：首轮测试进行中（21:23–21:25）开发者曾提交修复，导致首轮 proxy/contracts 两处运行结果不可用（已在首轮章节标注作废）；本轮全部结论基于指纹化后的修订版重新执行。

### R2.1 D1–D6 复验结果（独立最小复现）

| 缺陷 | 复验方法 | 实际结果 | 判定 |
|---|---|---|---|
| D1 [P1] 非法 `proxy.url` 崩溃 | 6 个非法变体（坏端口/IPv6/缺主机/端口越界/socks5 坏端口/非支持协议）× `check`、`check --net`、`fetch --dry-run` 三条路径 | 全部 **rc=3**、提示含 `proxy.url 无效: …`、**0 traceback**；合法但不可达代理仍干净 `[ERR] 代理` + rc=1 | ✅ 已修复 |
| D2 [P2] `--window-hours` 无范围校验 | `--window-hours 0 / -5 / 169 / abc`；`--limit -1` | 全部 **rc=2**（argparse 明确报错）；1/24/168 小时正常生效（168h 正确排除 200h 条目） | ✅ 已修复 |
| D3 [P2] 非法链接落库 | feed 注入相对、协议相对、`javascript:`、`mailto:`、含空格文本链接 | 仅 http(s)+主机名+无空白可落库；相对/协议相对按源地址补全；其余丢弃并记 debug 日志 | ✅ 已修复 |
| D4 [P2] naive datetime 本地时区偏移 | 直接写入 naive `2026-01-01T00:00:00` | 落库 **`2026-01-01T00:00:00+00:00`**（按 UTC 解释） | ✅ 已修复 |
| D5 [P2] 坏 IPv6 使普通 `check` 崩溃 | `proxy.url: "http://[::1"` 走 `check` | rc=3、无 traceback（配置校验 + 摘要整体打码双保险） | ✅ 已修复 |
| D6 [P1] socks5 代理 `ImportError`（第 2 轮预检新增发现，已修复） | `socks5://127.0.0.1:<closed>` 走 `check` / `check --net` / `fetch` | `socksio` 已安装；check rc=0、`check --net` 干净 `[ERR] 代理` + rc=1、`fetch` rc=1，**均无 ImportError/堆栈/`socksio` 字样** | ✅ 已修复 |

- D6 修复前证据：`regression2/prefix-socks-import-error.txt`（`ImportError: Using SOCKS proxy, but the 'socksio' package is not installed`）；修复后：`regression2/socks5.json`、`socks5/`。
- D1 修复为三层防护（config 校验端口/协议/主机 + `HttpClient` 捕获 `InvalidURL/ValueError/ImportError` + `fetch`/`check --net` 兜底），本轮同时验证了 6 个非法变体与"合法但不可达"路径均不误伤。

### R2.2 全量回归结果

| 项目 | 结果 | 证据 |
|---|---|---|
| `ruff check .` | **exit 0**（All checks passed） | `regression2/ruff.txt` |
| `pytest` | **132 passed / 0 failed / 0 errors**（3.0 s） | `regression2/pytest.txt`、`pytest-numbers.json` |
| `m0_blackbox.py` | **105/105**（M0 基线 105/105；`net-proxy-bad-port` 用例期望已按新契约更新为 rc=3 + `proxy.url 无效`） | `regression2/m0_blackbox.py.txt` |
| `m0_inprocess.py` | 13/13 | `regression2/m0_inprocess.py.txt` |
| `m0_secret_scan.py` | 13/13 | `regression2/m0_secret_scan.py.txt` |
| `m0_yaml_error_probe.py` | 5/6（唯一失败 `bad_alias` = M0 已接受 P2） | `regression2/m0_yaml_error_probe.py.txt` |
| `m0_issue_redaction_probe.py` | **4/4**（首轮 3/4 的 `webhook_in_proxy_url` 已随新校验恢复） | `regression2/m0_issue_redaction_probe.py.txt` |
| `m0_round3_probe.py` | 10/10 | `regression2/m0_round3_probe.py.txt` |
| `config.example.yaml` 开箱 | rc=0 | `regression2/11d-config.example开箱校验.txt` |
| `m1_acceptance.py` | **19/19** | `regression2/acceptance.json` |
| `m1_adversarial.py` | 11/11 | `regression2/adversarial.json` |
| `m1_url_edges.py` | 4/4 | `regression2/url_edges.json` |
| `m1_net_store_units.py` | 19/19（含 D4、URL 白名单、代理 ValueError） | `regression2/units.json` |
| `m1_contracts.py` | 6/6（HN 请求参数 / 不调 LLM / `--config` 位置 / `--help`） | `regression2/contracts.json` |
| `m1_check_net.py` | 6/6（真实配置 5/5 源 OK、rc=0、输出无密钥） | `regression2/check_net.json` |
| `m1_proxy_bad_port_repro.py` | 4/4（D1/D5 修复复验） | `regression2/proxy_bad_port.json` |
| `m1_socks_probe.py` | 4/4（D6 修复复验） | `regression2/socks5.json` |
| `m1_slow_source.py` | 1/1（挂起源 65.5 s 有界隔离，runs=partial） | `regression2/slow_source.json` |
| `m1_perf.py` | 6/6（真实 5 源采集、二次幂等、真实库零写入、无密钥） | `regression2/perf.json` |
| `m1_regression.py` | **9/9**（ruff/pytest + 6 个 M0 脚本基线对比 + example 开箱） | `regression2/regression.json` |
| 真实库只读守护 | 测试前后 items=106 / runs=2 / 无重复键 | `regression2/real-db-readonly-state.txt` + 各套件 guard 断言 |

> **测试脚本自身修正（透明记录）**：首次全量运行中 `m1_acceptance.py` 18/19、`m1_regression.py` 4/9，经查为**测试侧缺陷**，非产品问题 ——
> ① 窗口用例原用 `--window-hours 720`，新契约合法范围 1~168，产品正确返回 rc=2；已改为 168h（期望 7 条）并复跑 **19/19**；
> ② M0 证据路径拼接在多轮证据目录下取错位置，导致基线比对读取失败；修复后复跑 **9/9**。
> 两次修正只改测试脚本，产品代码未动；`run-all-summary.json` 保留首跑（含这两个测试侧失败）的原始记录。

### R2.3 本轮新增缺陷

- **无新增 P0/P1**；D6（socks5 代理 ImportError）在预检发现后由开发者修复并复验通过，不遗留。
- **残留 P2（均为 M0 已接受遗留，非 M1 新引入）**：
  - `D1-R`：YAML 坏别名时 `problem` 文本回显 alias 名（M0 已接受，触发极不自然）；
  - `D9-R`：`tzdata` 依赖无版本上限（纯数据包，M0 已接受）。
- **观察项（非缺陷）**：窗口只做下界过滤（未来时间条目保留）；429 不重试；`--source` 未知 ID 返回 rc=1；HN 缺 `objectID` 时生成半空 URL；`check --net` 以配置裸 URL 探测（已在输出标注「（探测）」）。

### R2.4 流程建议

门禁测试应在**代码冻结**后启动。本轮出现两次"测试期间产品代码变更"（首轮 21:23–21:25 修复 D1–D5；第 2 轮 21:33 修复 socks5），分别导致部分证据作废与重跑；最终结论基于指纹化修订版，并以 `revision-fingerprint.json` + `revision-compare.txt`（0 变更）证明测试期间代码未再变动。建议后续里程碑：冻结 → 通知测试 Agent → 开跑。

### R2.5 第 2 轮证据索引（`reports/evidence/m1/regression2/`）

| 证据 | 内容 |
|---|---|
| `revision-fingerprint.json` / `revision-compare.txt` | 被测修订版 17 文件指纹；测试后比对 0 变更 |
| `run-all-summary.json` / `run-all-summary.txt` / `run-all-console.txt` | 全量 20 个脚本首跑汇总（含 2 处测试侧失败） |
| `acceptance.json` / `acceptance/` / `acceptance_run.txt` | 验收 19 项断言（测试用例修正后复跑） |
| `regression.json` / `m0_*.py.txt` / `pytest.txt` / `pytest-numbers.json` | ruff / pytest / 6 个 M0 脚本基线对比（9/9） |
| `socks5.json` / `socks5/` / `prefix-socks-import-error.txt` | D6 修复复验 + 修复前 ImportError 证据 |
| `proxy_bad_port.json` / `proxy_bad_port/` | D1/D5 修复复验（6 非法变体矩阵，含逐变体原始输出） |
| `units.json`、`url_edges.json`、`adversarial.json`、`check_net.json`、`contracts.json`、`slow_source.json`、`perf.json` | 各专项回归 |
| `real-db-readonly-state.txt` | 真实库只读快照（106 items / 2 runs） |
| `ruff.txt`、`pytest.txt`、`pytest-numbers.json` | ruff exit 0；pytest 132 passed |

---

## 首轮测试记录（第 1 轮 · 基线）

> 以下为第 1 轮的完整记录（保留历史判定）。首轮被测修订版为 2026-09-23 21:23 之前；其中 proxy 矩阵与 contracts 两项运行受"测试期间代码变更"影响已标注作废，其余结论仍有效。

## 一、验收项结果

| # | 验收标准 | 执行方法（脚本/命令） | 实际结果 | 判定 | 证据 |
|---|---|---|---|---|---|
| 1 | 单源失败隔离 | 本地服务：1 坏源（HTTP 500）+ 2 好源，`python -m aihot fetch` | **rc=0**；好源落库 3 条；坏源 `[ERR] bad500`；`runs.status=partial`，`sources_ok=2 / sources_failed=1` | **PASS** | `evidence/m1/acceptance/1-单源失败隔离.txt` |
| 2 | 幂等（连跑 3 次） | 同配置连续 `fetch` 3 次；真实 5 源二次采集 | 新增序列 **[3, 0, 0]**，库内 **[3, 3, 3]**，`COUNT(*) = COUNT(DISTINCT source_id\|\|url_hash) = 3`，runs=3；真实源二次采集**新增 0**、唯一键=总数 | **PASS** | `evidence/m1/acceptance/2-连跑3次幂等.txt`、`evidence/m1/perf.json` |
| 3 | 时间窗口 | 同一 feed 含 30m/59m/61m/3h/25h/200h/无日期/坏日期 条目，`--window-hours 1 / 24 / 720` + 配置默认窗口 | 1h→4 条（含无日期与坏日期条目，61m 被排除）；24h→6 条；720h→8 条；默认窗口=配置 24h；**无日期条目未被误杀** | **PASS** | `evidence/m1/acceptance/3-时间窗口.txt` |
| 4 | 性能（5 源全量 < 2 分钟） | 真实 `config.yaml` 的 5 个启用源，`fetch`（窗口 24h，临时库） | **实测 3.7 s**（阈值 120 s），5/5 源 OK，抓取 108 条 → 新增 107 条（批内/跨源去重 1） | **PASS** | `evidence/m1/perf/per-source-stats.json`、`evidence/m1/perf_run.txt` |
| 5 | 落库正确性 | SQLite 只读查询 + 独立复算：字段完整、extra 合法 JSON、`url_hash=sha1(url)`、`PRAGMA index_list`、重复插入 | 四表齐备；107 行真实数据 + 合成数据字段无缺失；`extra` 全部可 `json.loads`；hash 与 URL 一致；唯一索引 `(source_id, url_hash)` 生效且重复插入被拒；**同 URL 跨源保留 2 行互不覆盖**；HN extra 含 `points/num_comments/hn_id/hn_url` | **PASS** | `evidence/m1/acceptance.json`、`evidence/m1/perf.json`、`evidence/m1/units.json` |
| 6 | `--dry-run` 不写库 | 临时库运行前后行数对比；随后真实运行对照 | dry-run 前/后 `items=0, runs=0`（库文件都未创建）；随后真实运行落库 2 条、runs=1；真实配置 dry-run 后真实库 106/2 不变 | **PASS** | `evidence/m1/acceptance/6-dry-run不写库.txt`、`evidence/m1/perf/perf.json` |
| 7 | `--source` 过滤 | 3 源配置，`--source s1 --source s3`；未知 ID | 仅 s1/s3 落库（3 条），s2 无行；未知 ID → rc=1 + `没有可用的信息源` | **PASS** | `evidence/m1/acceptance/7a---source过滤.txt`、`7b---source未知ID.txt` |
| 8 | 退出码语义 | 全部失败 / 配置错误 / 用法错误 / 部分失败 / 无启用源 / dry-run 全失败 | 全失败=**1**（runs.status=failed）；缺失配置与坏 YAML=**3**；未知参数/非法取值/未知命令=**2**；部分失败=**0**；无启用源=1；dry-run 全失败=1 | **PASS** | `evidence/m1/acceptance.json` |
| 9 | `check --net` 源健康度 | 本地好/坏源混合；真实配置 | 逐源 `[OK ] 源 x` / `[ERR] 源 y`；有失败项 → **rc=1**；全通过 → rc=0；真实配置 5/5 源 OK、rc=0；未启用源不进入探测行；探测前后真实库不变；输出无真实密钥 | **PASS** | `evidence/m1/check_net_run.txt`、`evidence/m1/check_net.json` |
| 10 | 对抗性测试 | 24 个对抗用例（见第三节） | 全部按预期隔离/落库；含挂起源 65.8s 有界超时、4000 条超大 feed 3.8s、断连/非法 XML/GBK/emoji/SQL 注入标题等 | **PASS（另登记 4 项 P2）** | `evidence/m1/adversarial.json`、`evidence/m1/url_edges.json`、`evidence/m1/slow_source.json`、`evidence/m1/contracts.json` |
| 11 | 工具链与回归 | `ruff check .`；`pytest`；重跑 M0 的 6 个独立脚本（对照 M0 第 3 轮基线）；`config.example.yaml` | ruff **exit 0**；pytest **102 passed / 0 failed**；密钥脱敏 13/13、配置校验类用例全通过、`config.example` rc=0；**但 `net-proxy-bad-port` 新增失败（104/105）→ D1** | **FAIL** | `evidence/m1/regression_run.txt`、`evidence/m1/regression/*` |

---

## 二、缺陷列表

### D1 [P1] 非法 `proxy.url` 使 `check --net` 与 `fetch` 崩溃（M0 已验收用例回归）

- **最小复现**（独立复现，未使用产品单测）：
  1. 配置 `proxy.enabled: true`、`proxy.url: "http://127.0.0.1:abc"`（端口非数字，`validate()` 只校验 `http(s)://` 前缀，判为合法）；
  2. `python -m aihot check --net --config <cfg>` → **rc=1、Python traceback**，无 `[ERR] 代理` 明细；
  3. `python -m aihot fetch --config <cfg> --dry-run` → **rc=1、同样 traceback**（`HttpClient.__init__` 即在任何采集之前失败）。
- **期望 vs 实际**：期望与 M0 一致 —— 干净的 `[ERR] 代理` + rc=1（或 rc=3 配置错误）；实际是 `httpx.InvalidURL: Invalid port: 'abc'` 未捕获，输出堆栈。
- **影响范围**（矩阵实测）：`http://127.0.0.1:abc`、`socks5://127.0.0.1:abc`、`http://[::1` 三类变体均崩溃；`fetch`（M1 主命令）与 `check --net` 同时受影响；`check`（不带 `--net`）对 `:abc` 仍判合法 rc=0，用户无法自诊断。`http://127.0.0.1:99999`、`http://:7890` 与"合法但不可达代理"行为正常（隔离成功）。
- **根因**：`checks.probe_sources()`（M1 新增）与 `cli._cmd_fetch()`（M1 新增）都直接 `HttpClient(proxy=cfg.proxy.url)`，而 `HttpClient.__init__` 中的 `httpx.Client(proxy=...)` 会解析代理 URL；`httpx.InvalidURL`（不继承 `httpx.HTTPError`）在构造期抛出，未被任何一层捕获（M0 只在 `probe_proxy` 内做了 `urlsplit().port` 防护）。
- **M0 回归证据**：M0 第 3 轮独立黑盒矩阵 105/105 全绿（权威副本 `reports/evidence/regression/cli_summary_r3.json`，9253 B）；本轮 104/105，唯一新增失败即 `net-proxy-bad-port`（`evidence/m1/regression/m0_blackbox.py.txt`）。M0 报告 D3「`--net` 捕获非法 URL」声明已修复并通过。
- **建议修复**（任一即可闭合，建议 1+3）：
  1. `config.validate()`：对 `proxy.url` 用 `urlsplit(...).port` 试解析并校验端口范围，非法直接报配置错误（rc=3，含可操作提示）；
  2. `net.HttpClient.__init__`：捕获 `httpx.InvalidURL/ValueError`，转成项目内异常或 `ValueError` 的语义化封装；
  3. `checks.probe_sources()` 与 `cli._cmd_fetch()`：构造客户端失败时降级为「`[ERR] 代理` + rc=1」/「rc=3 配置错误」，绝不输出 traceback。
- **证据**（第 1 轮有效证据为 21:03 复现文件，晚于 21:23 的矩阵运行受代码变更影响仅作参考）：`evidence/m1/proxy_bad_port/repro-check-proxy-bad-port.txt`、`repro-fetch-proxy-bad-port.txt`、`evidence/m1/regression/m0_blackbox.py.txt`（`net-proxy-bad-port` 失败明细）

### D2 [P2] `--window-hours` 缺少范围校验，0 被静默忽略、负值语义混乱

- **最小复现**：`fetch --window-hours 0` → 静默回退到配置 `window_hours=24`（落库 6 条 = 24h 结果），无任何提示；`fetch --window-hours -5` → rc=0，`since` 变成未来时间，最终落库 2 条（仅"无日期"条目），语义不可解释。
- **期望 vs 实际**：期望与 `app.window_hours` 一致（1~168 整数，非法值 rc=2）；实际无上下界校验（`--window-hours 100000` 也可用）。
- **影响**：参数错误时静默产生错误窗口，可能导致漏采或错采。
- **建议**：argparse 层加 `type=positive_int` + 范围校验；`0/负值/超上限` 直接 rc=2。
- **证据**：`evidence/m1/acceptance/obs-window-hours.txt`

### D3 [P2] 非 http(s)/相对链接被规范化成非法 URL 并落库

- **最小复现**：feed 中 link 为 `/relative/path` → 落库 `https:///relative/path`；`not a url` → `https:not a url`；`javascript:alert(1)`、`mailto:a@b.com` 原样落库（`build_item` 只判"规范化结果非空"）。
- **期望 vs 实际**：期望只接受 `http(s)://` 且 host 非空的 URL，其余丢弃并记录 debug 日志；实际 4/4 落库。
- **影响**：坏数据会进入 M2/M3 流水线（点击不可达链接、渲染层需额外防御）；数据源可控时概率低，但 HN 等社区源的 URL 属外部提交。
- **建议**：`sources.base.build_item()` 增加 scheme/host 白名单（`http`,`https`）；`normalize_url` 对空 netloc 保持原样时不要伪造 `https:` 前缀。
- **证据**：`evidence/m1/url_edges_run.txt`、`evidence/m1/url_edges/`

### D4 [P2] `Store._iso()` 把 naive datetime 按本地时区解释（潜在 8 小时偏移）

- **最小复现**：`Store.save_items(RawItem(published_at=datetime(2026,1,1,0,0,0)))` → 落库 `2025-12-31T16:00:00+00:00`（本机 UTC+8 被当作本地时间）。
- **期望 vs 实际**：期望 naive 输入按 UTC 处理或显式报错；实际 `astimezone(UTC)` 隐式本地化。
- **影响**：当前 4 个适配器全部产出 aware UTC datetime，暂无实际偏差；属契约防御缺口（M2 起会有更多写入方）。
- **建议**：`_iso()` 对 naive 值补记 `tzinfo=UTC` 或拒绝并抛清晰异常。
- **证据**：`evidence/m1/units.json`（note）、`evidence/m1/units_run.txt`

### D5 [P2] `proxy.url` 为非法 IPv6 时普通 `check` 也崩溃（M1 前既有）

- **最小复现**：`proxy.url: "http://[::1"` → `check`（不带 `--net`）在摘要输出阶段 `mask_url_credentials()` → `urlsplit()` 抛 `ValueError`，rc=1 + traceback。
- **说明**：该路径为 M0 代码（`utils.mask_url_credentials` + `_print_summary`），非 M1 引入；与 D1 同根因（代理地址未严格校验），建议随 D1 一并修复。
- **证据**：`evidence/m1/proxy_bad_port/proxy-invalid-matrix.json`（bad-ipv6 行）

### 观察项（不计缺陷，供后续里程碑参考）

- `check --net` 的源探测使用配置中的裸 URL（GET），与适配器真实请求不同（HN 实际带 `tags/numericFilters/hitsPerPage` 参数）；且"已启用但尚无适配器"的类型（html/reddit/hf_papers/arxiv）也会被探测。语义可接受，建议在输出中标注 `(探测)`。
- 4xx（含 429 限流）不重试，仅 5xx 与网络错误重试退避；建议 M4 之前给 429 增加退避。
- 窗口只做下界过滤：发布时间在未来的条目会被保留（`published >= since`）。
- HN 响应缺 `objectID` 时会拼出 `https://news.ycombinator.com/item?id=`（半空 URL），与 D3 同族。
- 标题统一被 `clean_html` 截断到 1000 字符（设计如此，已验证 100k 字符标题安全落库）。
- `--source` 指向未启用/不存在的源 → rc=1（"没有可用的信息源"），而非用法错误 rc=2；语义可接受，但 `--help` 未说明。
- M0 证据目录（`reports/evidence/{cli,logging,secret-scan,regression}`）会被 M0 回归脚本重跑覆盖（本轮重跑后 `cli/_summary.json` 已变为 104/105 M1 状态）；M0 第 3 轮权威副本 `regression/*_r3.json`（105/105、13/13、13/13、10/10）未被覆盖，可继续引用；建议后续给 M0 脚本增加"证据目录参数化/时间戳"能力，避免跨里程碑互相覆盖。

---

## 三、对抗性测试明细（验收项 10）

| 用例 | 输入 | 实际结果 | 判定 | 证据 |
|---|---|---|---|---|
| 空 feed | 合法 RSS 无条目 | 源 OK、0 条、整体 rc=0 | PASS | `evidence/m1/adversarial/adv-空feed.txt` |
| 非法 XML / HTML / 空响应 / 断连 | 4 个坏源 + 1 好源 | 4 个全部 `[ERR]` 且互不影响，好源落库 1 条，rc=0 | PASS | `adv-非法XML_HTML_空响应_断连.txt` |
| 超大 feed | 4000 条 × 200 字符标题（1.2 MB） | rc=0，落库 4000/唯一 4000，**3.8 s** | PASS | `adv-超大feed(4000条).txt` |
| 挂起超时源 | 服务端 120s 不响应 + 1 好源 | 3 次 20s 超时 + 退避 = **65.8 s** 有界结束，rc=0，runs=partial，好源正常落库 | PASS | `evidence/m1/slow_source_run.txt` |
| 不可达域名 | `*.invalid` + 1 好源 | 坏源 `[ERR]`，好源落库 1 条，rc=0 | PASS | `adv-不可达域名.txt` |
| URL 规范化 | utm/锚点/大小写/尾斜杠/默认端口/参数排序/去重 | 5 条落库，URL 全部符合预期，同源重复链接（尾斜杠/大小写/锚点）合并为 1 条 | PASS | `adv-URL规范化*.txt`、`adv-同源内重复条目*.txt` |
| RSS 缺 title/link/date | 7 条畸形条目 | 缺 title(2)/空 title(1) 丢弃；无日期/坏日期保留并落当前时间；`<script>` 摘要清洗为纯文本 | PASS | `adv-缺title_link_date+坏日期+HTML清洗.txt` |
| HN 缺 hits / 非对象响应 | `{"nbHits":0}`、`[1,2,3]` | 均隔离为 `[ERR]`，好源不受影响 | PASS | `adv-HN缺少hits_非对象响应.txt` |
| HN 字段容错 + extra | 完整/空标题/无 objectID/仅 `created_at_i` | 空标题丢弃；`points=42`、`num_comments=7`、`hn_id`、`hn_url`、`author` 全部进 extra | PASS | `adv-HN字段缺失容错+extra热度信号.txt` |
| SQL 注入式标题 | `'); DROP TABLE items;--` | 原样落库、items 表完好、无异常 | PASS | `adv-SQL注入式标题_emoji_超长标题.txt` |
| emoji 标题 | `🧠🔥 Emoji 标题 test` | UTF-8 完整往返 | PASS | 同上 |
| 超长标题 | 100,000 字符 | 落库被 `clean_html` 截断为 1000 字符，无崩溃 | PASS | 同上 |
| 重复条目跨源 | 同一 URL 出现在 s1/s2 | 2 行（source_id 不同），互不覆盖；同源重复合并为 1 行 | PASS | `evidence/m1/acceptance/5c-同URL跨源不覆盖.txt` |
| GBK 编码源 | `charset=gbk` 中文标题 | 落库中文无乱码 | PASS | `adv-GBK编码源中文不乱码.txt` |
| 契约：HN 请求参数 | 本地 HN 假服务 | 第 1 轮该次运行受"测试期间代码变更"影响，结果作废；第 2 轮复验通过（`tags=story`、`numericFilters=created_at_i>…,points>30`、`hitsPerPage=100`） | ⚠️ 作废→R2 PASS | `evidence/m1/regression2/contracts.json` |
| 契约：采集层不调 LLM | 本地 LLM 端点计数 | 同上作废；第 2 轮复验：fetch 全程 LLM 端点请求数 = 0 | ⚠️ 作废→R2 PASS | `evidence/m1/regression2/contracts.json` |
| 契约：`--config` 位置 | `--config` 在子命令前后 | 同上作废；第 2 轮复验：两种位置均 rc=0；`fetch --help` 列出 4 个参数 | ⚠️ 作废→R2 PASS | `evidence/m1/regression2/contracts.json` |

---

## 四、实测数字汇总

| 指标 | 实测值 |
|---|---|
| `ruff check .` | **exit 0**（All checks passed） |
| `pytest` | **102 passed / 0 failed**（1.31 s；M0 第 3 轮为 68 passed，M1 新增 34 个用例） |
| 5 源真实全量采集 | **3.7 s**（阈值 120 s；5/5 OK；抓取 108 → 新增 107，去重 1） |
| 单源失败隔离 | rc=0，`runs.status=partial`，`sources_ok/failed = 2/1` |
| 幂等（合成） | 新增序列 [3,0,0]，库内序列 [3,3,3]，重复键 0 |
| 幂等（真实源二次） | 新增 0，唯一键=总数=107 |
| 挂起超时源 | 65.8 s 有界（3×20 s 超时 + 1+2 s 退避），rc=0 |
| 超大 feed（4000 条） | 3.8 s，落库 4000/唯一 4000 |
| Store 2 万条批量 | 写 1.04 s；`filter_new` 查 0.78 s / 复核 0.77 s |
| M0 回归 | 黑盒 **104/105**（新增失败 `net-proxy-bad-port`）；其余 5 脚本：13/13、13/13、5/6（已接受 bad_alias）、3/4（已接受 webhook_in_proxy_url）、10/10 |

### 各源真实采集（本轮，24h 窗口）

| 源 | 结果 | 条数 | 耗时 | HTTP |
|---|---|---|---|---|
| qbitai | OK | — | — | 200（1839 ms 单请求探测） |
| infoq_cn | OK | — | — | 200（1497 ms） |
| techcrunch_ai | OK | — | — | 200（1612 ms） |
| openai_news | OK | — | — | 200（745 KB，3012 ms） |
| hackernews | OK | — | — | 200（3001 ms） |

> 分源条数与毫秒级耗时见 `evidence/m1/perf/per-source-stats.json`、`evidence/m1/perf/per-source-rows.json`。

---

## 五、证据索引（`reports/evidence/m1/`）

| 证据 | 内容 |
|---|---|
| `recon.txt` | 5 个真实源单次请求可达性与时延 |
| `acceptance_run.txt` / `acceptance.json` / `acceptance/` | 验收 1/2/3/5/6/7/8 的 17 项断言与原始输出 |
| `adversarial_run.txt` / `adversarial.json` / `adversarial/` | 对抗用例 11 项断言与原始输出 |
| `check_net_run.txt` / `check_net.json` / `check_net/` | `check --net` 6 项断言（含真实配置） |
| `units_run.txt` / `units.json` | net/store/utils 白盒复算 16 项 |
| `url_edges_run.txt` / `url_edges.json` | 非法/相对链接落库观察（D3） |
| `slow_source_run.txt` / `slow_source.json` | 挂起超时源有界隔离 |
| `contracts_run.txt` / `contracts.json` | ⚠️ 第 1 轮该次运行受"测试期间代码变更"影响（中间态模块导入失败），**结果作废**；第 2 轮复验见 `evidence/m1/regression2/` |
| `perf_run.txt` / `perf.json` / `perf/` | 真实 5 源性能与落库正确性、真实库零写入、输出无密钥 |
| `proxy_bad_port_run.txt` / `proxy_bad_port.json` / `proxy_bad_port/` | D1 复现与 6 变体影响面矩阵 + 逐变体原始输出 |
| `regression_run.txt` / `regression.json` / `regression/` | ruff/pytest 原文、6 个 M0 脚本重跑原文与基线对比；`pre-m1-snapshot/` 为快照（cli 子目录为 M1 重跑后的状态，非权威；权威基线取 `regression/*_r3.json`）。⚠️ 该目录后续被第 2 轮复跑刷新（现值 = 第 2 轮 9/9），首轮 `net-proxy-bad-port` 失败明细见 `m1/proxy_bad_port/repro-*.txt` |
| `real-db-readonly-state.txt` | 真实库只读快照（106 items / 2 runs，无重复键） |
| `ruff-indep.txt`、`ruff-check-raw.txt` | 未格式化前的问题清单（已修复，用于说明测试脚本亦纳入 ruff 门槛） |

---

## 六、测试方法与可复现性

- 依赖：仅使用 venv 内 python（`.venv\Scripts\python.exe`）与项目既有依赖；对抗测试通过线程内 `http.server` 在 `127.0.0.1` 上模拟源，不依赖外网；真实源用例走真实网络。
- 隔离：所有临时配置的 `data_dir`/`log_dir` 均指向 `%TEMP%\aihot-m1-tests\...`；真实库仅以 `mode=ro` 打开。
- 复现命令（每个脚本均自带断言与证据落盘）：
  - `tests/independent/m1_acceptance.py`、`m1_adversarial.py`、`m1_check_net.py`、`m1_net_store_units.py`、`m1_url_edges.py`、`m1_contracts.py`、`m1_slow_source.py`、`m1_proxy_bad_port_repro.py`、`m1_perf.py`、`m1_regression.py`、`m1_real_db_state.py`
- 未依赖开发者的单元测试结论：全部断言来自上述独立脚本的 CLI 输出与 SQLite 文件观测。
