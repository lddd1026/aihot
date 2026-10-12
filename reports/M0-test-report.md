# M0 测试报告

- **测试时间**：2026-09-23（测试环境本地时间）
- **被测版本**：`aihot 0.1.0`（editable 安装，`pyproject.toml` version=0.1.0）
- **测试环境**：Windows 10/11 + Python 3.12.10（项目 venv `D:\信息收集整理\aihot\.venv`）；ruff 0.16.8；pytest 9.1.1
- **测试方式**：独立冷启动、黑盒优先；全部结论以实际命令输出为准。测试脚本位于 `tests/independent/`（不以 `test_` 开头，不被 pytest 收集，ruff 全绿），产品代码未做任何修改。
- **首轮结论（2026-09-23 基线）**：FAIL（P0:1, P1:0, P2:9）
- **第 2 轮回归结论（2026-09-23）**：PASS（P0:0, P1:0, P2:5）
- **第 3 轮回归结论（2026-09-23 · 最终）**：**PASS（P0:0, P1:0, P2:4）** —— D2-R/D6-R/D7-R 主路径已修复；无新增 P0/P1；4 项 P2 中 2 项为开发者已接受遗留（D1-R、D9-R），另 2 项为同类极边缘路径（D2-R2、D6-R2），详见下方「回归验证（第 3 轮 · 最终）」

> 说明：全程只使用 GET 探测企业微信 webhook，**未发送任何消息**；真实密钥仅用于“是否存在明文”的程序化扫描，报告与证据中只出现掩码指纹（`<len=.. sha1=..>`），不含密钥原文。

---

## 回归验证（第 3 轮 · 最终）

- **回归时间**：2026-09-23
- **被测版本**：`aihot 0.1.0`（开发者提交 D2-R / D6-R / D7-R 修复）
- **回归方式**：聚焦复验 3 项修复（自建最小复现，未使用开发者提供的命令）+ 全量回归 4 脚本 + `ruff`/`pytest` + 新增对抗观察
- **最终结论：PASS（P0:0, P1:0, P2:4）** —— 无 P0/P1；4 项 P2 中 2 项为已接受遗留（D1-R、D9-R），另 2 项为同类极边缘路径（D2-R2、D6-R2）
- 历史结论：第 2 轮 PASS（P0:0, P1:0, P2:5）；第 1 轮 FAIL（P0:1, P1:0, P2:9）

### 3 项修复复验结果（独立最小复现）

| 项 | 复验方法 | 结果 | 判定 |
|---|---|---|---|
| D2-R | ① `app.log_dir` 指向已存在文件 ② `app.data_dir` 指向已存在文件 ③ 正常已存在目录 / 待创建目录回归 | ①② rc=3 + `app.log_dir/data_dir 指向已存在的文件，应为目录: <绝对路径>`，无 traceback；③ rc=0，无误伤 | ✅ 已修复 |
| D6-R | ① `proxy.url` 含 `user:pass` 且带 `?key=` ② webhook URL 误填 `proxy.url` ③ 裸密钥填入非法 `proxy.url` | ① 摘要输出 `http://***@127.0.0.1:7890?key=***`，用户名/口令/密钥均不出现 ② 输出 `key=***` ③ 仍回显裸密钥（ConfigError 路径，见 D6-R2） | ✅ 主路径修复 |
| D7-R | 无 `llm` 段配置 + 代码核对 `_build` | `_build` 中 `base_url`/`model` 已无默认值（注释说明由 `validate()` 保证非空）；无 `llm` 段 rc=3 且提示明确 | ✅ 已修复 |

### 全量回归（第 3 轮）

| 项目 | 第 2 轮 | 第 3 轮 | 证据 |
|---|---|---|---|
| `ruff check .` | exit 0 | exit 0（All checks passed） | `regression/ruff-final.txt` |
| `pytest` | 65 passed | **68 passed**（+3），exit 0 | `regression/pytest-final.txt` |
| 黑盒矩阵 | 103 项，1 失败 | **105 项，0 失败**（新增 dir-is-file 2 例；llm-required 期望修正） | `regression/cli_summary_r3.json` |
| 日志脱敏/JSON/轮转 | 13/13 | 13/13 | `regression/logging_summary_r3.json` |
| 真实密钥扫描 | 13/13 | 13/13 | `regression/secret_scan_summary_r3.json` |
| YAML 端到端探测 | 5/6 | 5/6（D1-R 遗留，已接受） | `regression/yaml_error_probe_run.txt` |
| 错误信息/摘要脱敏探测 | 2/4 | **3/4**（`key=` 摘要路径已修复；裸密钥回显残留） | `regression/issue_redaction_run.txt` |
| 第 3 轮聚焦探针（新增） | — | 10/10 | `regression/round3/_summary.json` |

### 第 3 轮残留（P2，不阻断）

#### D2-R2 [P2] 嵌套路径位于文件之下时仍 traceback（极边缘）
- 最小复现：存在文件 `blocker-file`，配置 `app.log_dir: blocker-file/sub` → `_resolve_dir` 判定路径不存在（不报错），`setup_logging` 的 `mkdir(parents=True)` 触发 `FileExistsError` traceback，rc=1（`regression/round3/d2r-nested-under-file.txt`）。
- 说明：原始 D2-R 复现（路径本身是文件）已修复；嵌套场景需同时满足“父路径是文件”，触发条件极不自然。
- 建议（可选）：`setup_logging` 捕获 `OSError` 并转为明确错误。

#### D6-R2 [P2] ConfigError 仍回显 `proxy.url` 中的裸密钥
- 最小复现：`proxy.url: not-a-url-<裸密钥>` → rc=3，错误信息 `实际: 'not-a-url-M0R3KEY...'` 含裸密钥（`regression/round3/d6r-bare-key-invalid-url.txt`）。
- 说明：摘要显示路径已修复（`key=***`）；配置校验失败时拿不到 secrets，模式无法识别裸 token。与已接受的 D1-R 同类（需把裸密钥误填进 `proxy.url`）。
- 建议（可选）：校验错误不回显原值，或仅显示前 4 字符。

#### 已接受遗留（沿用第 2 轮）
- **D1-R**：密钥写成 YAML 别名名时 `problem` 文本回显 alias 名（开发者接受，触发极不自然）。
- **D9-R**：`tzdata` 无版本上限（纯数据包，无代码 API，开发者接受）。

### 第 3 轮观察
- 严格布尔、`--net` 退出码语义、目录解析对正常路径均无回归（黑盒 105 项全绿）。
- 测试环境网络由沙箱代理接管，`check --net` 的连通性结论只代表本环境；全程仅 GET，未发送任何企微消息。

### 第 3 轮证据索引

| 证据 | 内容 |
|---|---|
| `regression/round3/_summary.json` | 3 项修复的聚焦探针结果（10/10） |
| `regression/round3/*.txt` | 每例原始命令+输出（d2r/d6r/d7r） |
| `regression/cli_summary_r3.json` | 黑盒 105 项快照 |
| `regression/logging_summary_r3.json` | 日志脱敏/轮转 13 项快照 |
| `regression/secret_scan_summary_r3.json` | 真实密钥扫描 13 项快照 |
| `regression/ruff-final.txt` / `pytest-final.txt` | ruff exit 0 / pytest 68 passed |
| `regression/issue_redaction_run.txt` | 摘要脱敏 3/4（D6-R2 残留） |
| `regression/yaml_error_probe_run.txt` | YAML 端到端 5/6（D1-R 遗留） |

---

## 回归验证（第 2 轮）

- **回归时间**：2026-09-23
- **被测版本**：`aihot 0.1.0`（开发者已提交修复；产品代码未由测试方改动）
- **回归方式**：重跑 4 个独立脚本 + 针对 D1–D9 的定向复验 + 新增对抗探测（严格布尔误伤、`--net` 新语义边界、错误信息脱敏）
- **新结论：PASS（P0:0, P1:0, P2:5）** —— 无 P0/P1；5 项 P2 残留见下（不阻断）
- 首轮结论（保留）：**FAIL（P0:1, P1:0, P2:9）**

### 修复复验结果（逐项独立验证）

| 项 | 声明修复 | 独立复验方法与证据 | 结果 | 判定 |
|---|---|---|---|---|
| D1 | 坏 YAML 不回显原文 | ① 真实 `config.yaml` 派生坏配置（webhook 行加 `: oops`）→ 完整密钥 + 20 字符窗口扫描 ② 假密钥 6 种畸形配置走 CLI（`regression/yaml-e2e-probe.json`） | 真实配置 0 泄漏；输出改为 `YAML 解析失败: mapping values are not allowed here（第 28 行，第 105 列，已隐藏原文以防泄露密钥）`；假密钥 5/6 场景 0 泄漏 | ✅ 已修复 |
| D2 | 类型校验补全 | 黑盒矩阵新增 11 例：smtp_port / to_addrs / use_ssl / data_dir / log_dir / extra / enabled | 全部 rc=3 + 明确错误，不再 traceback | ✅ 已修复（`log_dir` 指向文件的运行期崩溃残留，见 D2-R） |
| D3 | `--net` 捕获非法 URL | `proxy.url` 端口非数字、`llm.base_url` 非法 IPv6 | 均 rc=1 + 逐项 `[ERR]` 明细，无 traceback、无泄漏 | ✅ 已修复 |
| D4 | 严格布尔 | 5 个布尔字段 × 字符串/整数 + YAML `yes/no` / 省略值回归 | 非法值 rc=3；合法值 rc=0，无误伤 | ✅ 已修复 |
| D5 | `--net` 退出码/状态判定 | 全通过 → rc 0；有 key 但 401 → rc 1；不可达 → rc 1；wecom 非 200 → ERR（代码走查） | 与声明一致；真实配置 `check --net` rc=0 | ✅ 已修复 |
| D6 | 代理凭据掩码 | `proxy.url=http://m0user:***@127.0.0.1:7890` | 输出 `http://***@127.0.0.1:7890`，口令与用户名均不出现 | ✅ 已修复（查询串/裸密钥路径残留，见 D6-R） |
| D7 | 默认值一致性 | 无 `llm` 段配置 | 仍 rc=3；`_build` 中 `base_url`/`model` 默认值仍不可达 | ⚠️ 未修复（P2 残留，见 D7-R） |
| D8 | example 开箱 | `check --config config.example.yaml` | rc=0；推送渠道默认关闭并附“填写后改 true”说明 | ✅ 已修复 |
| D9 | 依赖上限 | 解析 `pyproject.toml` 10 项 + dev 依赖 | 9/10 有上限；`tzdata>=2024.1` 无上限（数据包） | ✅ 基本修复（见 D9-R） |
| D10 | README 进度 | 检查进度表 | 仍为「进行中」（开发者声明 PASS 后更新） | ⏳ 待办（不计缺陷） |

### 全量回归对比

| 项目 | 第 1 轮 | 第 2 轮 | 证据 |
|---|---|---|---|
| `ruff check .` | exit 0 | exit 0（All checks passed） | `regression/ruff-final.txt` |
| `pytest` | 53 passed | **65 passed**（+12 回归测试），exit 0 | `regression/pytest-final.txt` |
| 黑盒矩阵 | 87 项，2 失败（P2） | **103 项，1 失败（D7-R）** | `cli/_summary.json` |
| 日志脱敏/JSON/轮转 | 13/13 | 13/13 | `logging/_summary.json` |
| 真实密钥扫描 | 11/13（D1×2 失败） | **13/13** | `secret-scan/_summary.json` |
| YAML 端到端探测（本轮新增） | — | 5/6（bad_alias 残留，见 D1-R） | `regression/yaml-e2e-probe.json` |
| 错误信息脱敏探测（本轮新增） | — | 2/4（proxy.url 两条路径残留，见 D6-R） | `regression/issue-redaction-probe.json` |

### 第 2 轮残留（P2，不阻断）

#### D1-R [P2] YAML `problem` 文本回显 alias 名（极边缘）
- 最小复现：把密钥写成 YAML 别名（`webhook: *<密钥原文>`）→ 输出 `YAML 解析失败: found undefined alias 'M0YAMLKEY...'（第 10 行，第 14 列，已隐藏原文以防泄露密钥）`。
- 影响：真实 webhook URL 无法作为别名，正常拼写错误不会命中；仅当用户把裸密钥写成锚点/别名名时触发。
- 建议：对 problem 中 `alias '...'` / tag 名做掩码。

#### D2-R [P2] `log_dir` 指向已存在文件仍崩溃
- 最小复现：`app.log_dir: blocker`（blocker 为已存在文件）→ `FileExistsError` traceback，rc=1（`regression/logdir-is-file-r2.txt`）。
- 建议：目录创建失败时转为 rc=3 配置错误（含可操作提示）。

#### D6-R [P2] 代理地址显示/回显仅掩码 userinfo
- 最小复现：① `proxy.enabled: true` + `proxy.url` 为含 `key=...` 的 webhook URL（误粘贴）→ `check` rc=0 且摘要明文打印完整 URL；② `proxy.url: not-a-url-<裸密钥>` → 错误信息回显裸密钥（无 `key=` 前缀，模式无法匹配）。
- 影响：需要把 webhook 误填进 `proxy.url`，正常配置无影响。
- 建议：摘要输出对代理地址追加 `redact_text(..., cfg.secrets())`；对无法模式识别的裸密钥，错误信息不回显字段值（或仅显示前 4 位）。

#### D7-R [P2] validate 与 `_build` 默认值仍不一致
- 现状：无 `llm` 段 → rc=3（`llm.base_url 必须是 http(s) 地址，实际: ''`），而 `_build` 的 `base_url`/`model` 默认值不可达（死代码）。
- 建议：二选一——校验采用默认值，或删除 `_build` 死默认值并在文档中明确 `llm` 必填。

#### D9-R [P2] `tzdata` 无版本上限
- `tzdata>=2024.1`（纯数据包，风险极低）；其余 9 项与 dev 依赖均已有 `<` 上限。

### 新增对抗探测（本轮）
- 严格布尔误伤检查：YAML 原生 `yes/no`、省略 `enabled`、email 关闭时的完整类型字段 → 全部 rc=0，无误伤。
- `--net` 新语义边界：无 key 时 401/403 视为可达、有 key 时非 200 视为失败、任一失败 rc=1 → 全部符合声明。
- 错误信息脱敏：`key=`/`sk-` 模式在 `ConfigError` 回显中已脱敏（`webhook_in_timezone`、`sk_key_in_source_url` 通过）。

### 回归观察项（非缺陷）
- `push.wecom` / `push.email` 若写成非映射会被静默忽略（首轮即存在，非本轮新增）。
- 测试环境网络由沙箱代理接管，`check --net` 的连通性结论只代表本环境；全程仅 GET，未发送任何企微消息。

### 第 2 轮证据索引

| 证据 | 内容 |
|---|---|
| `regression/ruff-final.txt` | `ruff check .` exit 0 |
| `regression/pytest-final.txt` | `pytest` 65 passed, exit 0 |
| `regression/pytest-collect.txt` | 65 项收集明细（4 个测试文件） |
| `regression/pip-check.txt` | `pip check` 无冲突 |
| `regression/blackbox_run.txt` | 黑盒矩阵运行日志（103 项，1 失败=D7-R） |
| `regression/inprocess_run.txt` | 日志脱敏/轮转 13 项运行日志 |
| `regression/secret_scan_run.txt` | 真实密钥扫描运行日志（13/13） |
| `regression/yaml-e2e-probe.json` / `yaml-e2e-probe-details.txt` | D1 端到端复验（假密钥，6 场景 5 通过） |
| `regression/yaml_error_probe_run.txt` | 同上运行日志 |
| `regression/issue-redaction-probe.json` / `issue_redaction_run.txt` | 错误信息/摘要脱敏探测（2/4，残留 D6-R） |
| `regression/logdir-is-file-r2.txt` | D2-R 复现（`log_dir` 指向文件） |
| `regression/ruff-independent.txt` | 测试脚本自身 ruff 检查（全绿） |
| `secret-scan/_summary.json`、`secret-scan/malformed-yaml-output.txt` | D1 真实配置复验（0 泄漏，错误信息不含原文） |
| `cli/_summary.json`、`logging/_summary.json` | 回归后的黑盒与日志测试明细 |

> 存档说明：`cli/_summary.json`、`logging/_summary.json`、`secret-scan/_summary.json` 为“最近一次运行”结果（现为第 2 轮）；第 2 轮快照另存为 `regression/cli_summary_r2.json`、`regression/logging_summary_r2.json`、`regression/secret_scan_summary_r2.json`。首轮结论以本报告首轮章节文字为准，首轮原始输出片段仍保留在 `cli/config-*.txt`、`cli/_yaml-error-probe.txt` 等文件。

---

## 首轮验收项结果（基线）

| # | 验收标准 | 执行方法 | 实际结果 | 判定 | 证据 |
|---|---|---|---|---|---|
| 1 | 配置校验：合法退出 0，非法退出 3 且提示可操作 | `python -m aihot check --config config.yaml`；54 例配置校验矩阵（46 例预期非法）逐例执行 | 合法 rc=0；全部非法 rc=3，错误逐条编号、含字段名与期望格式 | PASS | `secret-scan/real-cli-plain.txt`；`cli/config-*.txt` |
| 2 | 校验覆盖面（12 类指定坏配置） | 黑盒逐例：坏 YAML / 顶层非映射 / 缺 id / id 重复 / 非法 URL / 不支持 type / wecom 无 webhook / email 缺凭据 / 非法 timezone / window_hours 与 top_n 越界 / bool 冒充整数 / proxy 空 url | 12 类全部 rc=3 且报错明确；另补充 email 地址/端口、weight、llm 字段、section 类型、BOM、CRLF、Unicode、超长等对抗用例 | PASS | `cli/_summary.json`；`cli/config-bad-yaml.txt` 等 |
| 3 | 密钥不进日志文件/控制台/`check`/`check --net`/异常堆栈/多 handler | ①真实密钥：plain/`--debug`/`--net` 输出 + `logs/aihot.log` + 全仓库扫描（145 文件）②假密钥对抗：对象参数、dict/tuple 参数、bytes、`logging.exception`、httpx/httpcore/httpcore2/httpx2 噪声日志、console+file 双 handler | 正常路径全部干净（无完整、无 20 字符窗口泄漏）；httpx 系 INFO/DEBUG 记录 0 条；**坏 YAML 路径泄漏密钥内容（见 D1）** | **FAIL** | `secret-scan/_summary.json`；`logging/_summary.json`；`cli/yaml-error-probe-details.txt` |
| 4 | CLI 语义：`--help`/`version`/未知命令/退出码；`--config` 前后位置；配置文件不存在 | 逐命令执行并核对 rc：help(0)、version(0)、未知命令(2)、未知选项(2)、多余位置参数(2)、缺文件(3)、目录当配置(3)、`--config` 前置/后置/两处并存(0，子命令后者生效) | 全部符合预期；`python -m aihot` 与控制台脚本 `aihot.exe` 均可用 | PASS | `cli/cli-*.txt`；`cli/_summary.json` |
| 5 | 工具链门槛 | `.venv\Scripts\python.exe -m ruff check .`；`.venv\Scripts\python.exe -m pytest` | ruff：`All checks passed!` exit 0；pytest：**53 passed**，exit 0（无 skip/xfail） | PASS | `ruff-final.txt`；`pytest-final.txt` |
| 6 | 工程完整性 | 文件清单核对 + `.gitignore` 检查 + example 密钥扫描 + 依赖检查 | `pyproject.toml`/`requirements.txt`/`requirements-dev.txt`/`.gitignore`（已排除 `config.yaml`）/`README.md`/`PLAN.md`/`config.example.yaml`（三个密钥字段均为空）齐备；`pip check` 无冲突；依赖全部只有下限（D9）、README 进度滞后（D10）、example 开箱 `check` 失败（D8） | PASS（附 3 项 P2） | `secret-scan/_summary.json`；`cli/config-example-out-of-box.txt`；`pip-list.txt`；`pip-check.txt` |
| 7 | 健壮性（对抗用例） | 空文件/纯注释/超长 5000 字/emoji+引号/BOM/CRLF/section=null；`setup_logging` 重复调用；逐行 `json.loads`；轮转 5MB×7（含强制轮转 6MB）；重定向中文编码 | 空与纯注释 rc=3「配置文件为空」；Unicode/超长/BOM/CRLF rc=0 且输出无乱码；重复调用后 handler 恒为 2、无重复行；6 行日志全部可解析；`maxBytes=5242880, backupCount=7`，强制轮转生成 `aihot.log.1`；重定向文件为合法 UTF-8 且 emoji 完好；另发现类型坏值崩溃（D2/D3）与布尔字符串问题（D4） | PASS（附 3 项 P2） | `logging/_summary.json`；`logging/redaction-file-sample.txt`；`cli/_summary.json`；`cli/cli-redirect-utf8.txt` |

---

## 首轮缺陷列表（基线）

### D1 [P0] 坏 YAML 时 PyYAML 错误回显行内容，`check` 未脱敏直接打印密钥

- **最小复现步骤**：
  1. 构造配置（假密钥即可复现完整泄漏）：
     ```yaml
     push:
       wecom:
         enabled: true
         webhook: https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=M0FAKEKEY0123456789abcdef: oops
     ```
     （即在 webhook 行尾多写 `: oops`；未闭合的 `[`、`&锚点`、`*别名` 等同理）
  2. 执行：`.venv\Scripts\python.exe -m aihot check --config <该配置>`
- **期望 vs 实际**：
  - 期望：rc=3，错误信息中密钥被脱敏（如 `key=***`）。
  - 实际：rc=3，但 stdout 打印 PyYAML 上下文片段，**包含密钥原文**：
    ```
    mapping values are not allowed here
      in "<unicode string>", line 3, column 73:
         ... /x?key=M0FAKEKEY0123456789abcdef: oops
                                             ^
    ```
    用项目真实 `config.yaml`（webhook 长度 89，key 长度 36）复测：输出泄漏该 key 的 **12 个 20 字符窗口（约 30/36 字符）**，剩余字符可暴力枚举，等同于凭据泄露。
- **影响范围**：所有“配置加载失败”路径（`check`、`check --net`、`--debug`）。一旦用户把报错输出粘贴进聊天/工单/日志，密钥即外泄。触发条件：YAML 语法错误且 PyYAML 的报错上下文覆盖密钥行（未闭合引号、Tab 等场景不回显，属部分场景）。
- **根因**：`src/aihot/config.py:118-120` 将 `yaml.YAMLError` 原文拼入 `ConfigError`；`src/aihot/cli.py:81-83` 直接 `print(issue)`，未走 `redact_text`（此时也拿不到 secrets 列表）。
- **修复建议**：对 YAML 异常信息统一脱敏后再输出（现有 `redact_text` 的 `key=...`/`sk-...` 规则可覆盖大部分），或仅输出异常类型+行列号、不回显上下文行。
- **证据**：`reports/evidence/cli/yaml-error-probe-details.txt`（假密钥完整回显，6 个场景中 4 个命中）、`reports/evidence/cli/_yaml-error-probe.txt`、`reports/evidence/secret-scan/_summary.json`（`malformed-yaml-console-no-partial-leak` FAIL）、`reports/evidence/secret-scan/malformed-yaml-output.txt`（真实配置，已整行脱敏）。

### D2 [P2] 部分字段类型未校验，坏值触发未捕获异常（rc=1 + traceback，而非 rc=3 清晰报错）

- **最小复现步骤**（三例）：
  1. `push.email.enabled: false` + `push.email.smtp_port: abc` → `check` 崩溃：`ValueError: invalid literal for int()`（`config.py:346`）
  2. `sources[0].extra: oops`（字符串）或 `extra: [a, b]` → 崩溃：`ValueError: dictionary update sequence ...`（`config.py:330`）
  3. `app.log_dir: blocker`（`blocker` 是已存在的文件）→ 崩溃：`FileExistsError`（`logging_setup.py:91`）
- **期望 vs 实际**：期望与其它坏配置一致 rc=3 + 可操作提示；实际 rc=1 + Python traceback（未发现密钥泄漏）。
- **影响范围**：坏配置下的可用性与脚本可判断性；M0 验收的“非法配置退出码 3”在这些边缘字段上不成立。
- **证据**：`cli/config-email-disabled-bad-port.txt`、`cli/config-extra-string.txt`、`cli/config-extra-list.txt`、`cli/logdir-is-file.txt`。

### D3 [P2] `check --net` 对非法 URL 配置崩溃

- **最小复现步骤**：
  1. `proxy.enabled: true` + `proxy.url: http://127.0.0.1:abc` → `parts.port` 抛 `ValueError` 未捕获（`checks.py:48`）
  2. `llm.base_url: http://[::1/models?key=...` → `httpx.InvalidURL` 未被 `except httpx.HTTPError` 捕获（`checks.py:83-90`）
- **期望 vs 实际**：期望该探测项显示 `[ERR]` 并继续；实际 rc=1 + traceback（已确认 traceback 不含密钥）。
- **证据**：`cli/net-proxy-bad-port.txt`、`cli/net-llm-invalid-url.txt`。

### D4 [P2] 布尔字段 `bool()` 强转，字符串 `"false"` 被静默当作 true

- **最小复现步骤**：source 配置 `enabled: "false"`（带引号）→ `check` rc=0，摘要显示 `[on ] qbitai`。
- **期望 vs 实际**：期望类型校验报错或按 false 处理；实际源被启用。`wecom.enabled: "false"` 会因缺 webhook 报错（不静默），`source.enabled` 无任何校验，静默反向。
- **证据**：`cli/config-enabled-quoted-false.txt`、`cli/_summary.json`。

### D5 [P2] `check --net` 探测失败仍返回 0；任意 HTTP 状态（含 502）视为可达

- **最小复现步骤**：`llm.base_url: http://127.0.0.1:1` + 假 key → 输出 `[ERR] LLM API: 鉴权失败 HTTP 502`，但 rc=0。
- **期望 vs 实际**：期望失败探测体现在退出码（供 `check --quick`/任务计划判断）；实际恒为 0。另 `probe_wecom` 把任意响应（含网关 502）标 `[OK]`（`checks.py:71-72`）。
- **影响范围**：PLAN §1.6「预检失败立即告警」在 M1/M4 无法依赖退出码；建议此时明确约定。
- **证据**：`cli/net-llm-unreachable.txt`。

### D6 [P2] `check` 摘要明文回显带凭据的 `proxy.url`

- **最小复现步骤**：`proxy.enabled: true`，`proxy.url: http://m0user:M0PROXYPASS0123456789@127.0.0.1:7890` → `check` 输出 `代理 : http://m0user:M0PROXYPASS...@127.0.0.1:7890`。
- **期望 vs 实际**：期望凭据脱敏（或只显示 host:port）；实际原样打印。当前真实配置未启用代理，无实际泄漏；**M4 启用带认证代理前必须处理**（届时按 P1/P0 对待）。
- **证据**：`cli/proxy-credentials-echo.txt`。

### D7 [P2] `validate` 与 `_build` 默认值不一致（llm 段）

- **最小复现步骤**：配置仅含 `sources`（无 `llm` 段）→ rc=3：`llm.base_url 必须是 http(s) 地址，实际: ''`、`llm.model 不能为空`；而 `_build` 对这两项定义了默认值（`https://api.deepseek.com` / `deepseek-chat`）但永远不可达。
- **期望 vs 实际**：二者应一致——要么校验应用默认值（llm 基础项可省略），要么删除 `_build` 的不可达默认值并文档明确 llm 必填。错误提示本身清晰可操作，故为 P2。
- **证据**：`cli/config-valid-minimal.txt`。

### D8 [P2] `config.example.yaml` 开箱 `check` 失败

- **最小复现步骤**：`.venv\Scripts\python.exe -m aihot check --config config.example.yaml` → rc=3（`push.wecom.enabled 为 true 但未配置 webhook`）。
- **期望 vs 实际**：模板复制后第一步即红灯；提示明确可操作，但建议模板将 `wecom.enabled` 设为 `false`，或在 README 标注“补齐 webhook 后才通过”。
- **证据**：`cli/config-example-out-of-box.txt`。

### D9 [P2] 依赖无版本上限

- **现状**：`pyproject.toml` 依赖全部为 `>=`（httpx>=0.27、openai>=1.40、playwright>=1.45 等）；`requirements-dev.txt` 为 `pytest>=8.0`、`ruff>=0.6`；无任何 `<` 上限或锁文件。当前 `pip check` 无冲突、53 项测试通过。
- **影响**：大版本升级可能破坏兼容（如 openai 1.x→3.x 已发生 API 变化）；建议对关键依赖加 `<下一大版本` 上限或引入锁文件。
- **证据**：`pip-list.txt`、`pip-check.txt`。

### D10 [P2] README 进度表滞后

- **现状**：`README.md:74` 仍标 M0「🔄 进行中」，与“已交付待门禁”状态不符；门禁通过后建议更新为 ✅。
- **证据**：`README.md`。

---

## 其他观察（不计缺陷）

1. **重复顶层 YAML 键**静默取后者（PyYAML 默认行为），无告警；建议后续加重复键检测（可选）。
2. `setup_logging` 之后**自行插入的无过滤 handler** 可写出明文（属调用方责任）；产品自带的 console+file 两个 handler 均已脱敏，重复调用幂等。
3. 本测试环境的网络被沙箱代理接管：所有外部 GET 均由代理响应（qyapi 返回 200、`127.0.0.1:1` 返回 502），因此 `check --net` 的连通性结论只代表本环境；全程仅 GET，未发送任何企微消息。
4. D1 的触发有场景限制：未闭合引号、Tab 缩进等 PyYAML 报错不回显内容（见 probe 的 6 个场景，4 个泄漏、2 个不泄漏）。

---

## 回归建议（修复后增量验证）

修复 D1 后重跑以下脚本即可（预计 2 分钟内）：

```powershell
.venv\Scripts\python.exe tests\independent\m0_secret_scan.py   # 期望 13/13
.venv\Scripts\python.exe tests\independent\m0_yaml_error_probe.py  # 期望全部 leaks_secret=False
.venv\Scripts\python.exe tests\independent\m0_blackbox.py      # 期望仅剩已知 P2 项
.venv\Scripts\python.exe tests\independent\m0_inprocess.py     # 期望 13/13
```

建议顺带修复 D2（在 `validate` 中补齐 `smtp_port`/`extra`/目录可创建性校验并统一 `int()` 转换的异常处理）、D3（捕获 `ValueError`/`httpx.InvalidURL`）与 D4（布尔字段类型校验），可一并回归。

---

## 证据索引

| 证据 | 内容 |
|---|---|
| `reports/evidence/ruff-final.txt` | `ruff check .` 全量结果（exit 0） |
| `reports/evidence/pytest-final.txt` | `pytest` 结果（53 passed，exit 0） |
| `reports/evidence/pip-list.txt` / `pip-check.txt` | 已安装版本与依赖一致性 |
| `reports/evidence/cli/_summary.json` | 黑盒 87 项检查明细 |
| `reports/evidence/cli/config-*.txt` | 每个配置校验用例的原始命令+输出（54 例主矩阵 + 对抗用例） |
| `reports/evidence/cli/cli-*.txt` | CLI 语义用例（help/version/未知命令/--config 位置/重定向编码等） |
| `reports/evidence/cli/net-*.txt` | `check --net` 用例（假 webhook、不可达 LLM、非法代理/URL） |
| `reports/evidence/cli/yaml-error-probe-details.txt` | D1 机制：假密钥被 PyYAML 错误完整回显 |
| `reports/evidence/cli/config-example-out-of-box.txt` | example 模板开箱检查（rc=3） |
| `reports/evidence/cli/proxy-credentials-echo.txt` | D6 代理凭据回显 |
| `reports/evidence/cli/logdir-is-file.txt` | D2 复现 3 |
| `reports/evidence/logging/_summary.json` | 脱敏/JSON lines/幂等/轮转 13 项明细 |
| `reports/evidence/logging/redaction-file-sample.txt` | 落盘 JSON 样例（密钥已 `***`） |
| `reports/evidence/secret-scan/_summary.json` | 真实密钥全路径扫描 13 项（含 D1 判定） |
| `reports/evidence/secret-scan/real-cli-*.txt` | 真实配置 plain/`--debug`/`--net` 输出（已脱敏） |
| `reports/evidence/secret-scan/malformed-yaml-output.txt` | D1 真实配置复现（含密钥行已整行脱敏） |
| `reports/evidence/ruff-independent.txt` | 测试 Agent 脚本自身 ruff 检查（全绿） |
