# M7 独立验收测试报告（内容范围调整为开发者向）

- 验收对象：`D:\信息收集整理\aihot`（工作区变更未提交）
- 验收类型：**冷启动黑盒独立验收**（只信代码与实测，不信任务书声明）
- 执行人：独立验收测试 Agent（subagent）
- 日期：2026-10-12
- 脚本：`tests/independent/m7r_support.py`、`m7r_prefilter.py`、`m7r_compose_rank.py`、`m7r_e2e.py`、
  `m7r_docs.py`、`m7r_regression.py`、`m7r_run_all.py`；P3 复验轮：`m7r_p3_fixes.py`
- 证据：`reports/evidence/m7r/`（各套件 JSON + 明细）；回归证据 `reports/evidence/m7r/regression/`；
  P3 复验证据 `reports/evidence/m7r/round2/`
- 约束遵守：未改 `src/**`、`config.yaml`、`run_daily.bat`、真实 `data/`、`logs/`；全部假服务只连 localhost；
  无真实推送/邮件/LLM；既有旧脚本零改动（`m5r_*`/`m6r_*` 未适配、直接复跑）

## 0. 结论摘要

| # | 待验证声明 | 判定 | 说明 |
|---|---|---|---|
| 1 | 规则层剔除硬件/商业（中英）且 `dropped_topic` 计数/样本正确 | **PASS** | 13/13 剔除；`dropped_topic=13`；样本原因"主题不符（硬件/商业）"；CLI 可见 |
| 2 | 误杀护栏：含 GPU/显存/型号名的工程内容必须保留 | **PASS** | 20/20 保留（含"可在消费级显卡运行的小模型"等 8 条新构反例零误杀） |
| 3 | 配置行为：追加生效 / 内置开关 / 校验正确 | **PASS** | 追加=内置+自定义；关闭后仅自定义；4 类错误类型校验报错；CLI `check` rc=3 |
| 4 | 组装层：三分区（精选/简讯/论文）排除三个分类 | **PASS** | 4 条技术进精选、1 篇技术论文进论文、三条排除分类零泄漏 |
| 5 | 排序层：商业类分数显著低于技术类 | **PASS** | 同内容差 40.0 分；商业 99 分仍低于技术 60 分；CLI Top 实测负分 −6.2/−16.2 |
| 6 | 端到端：混合条目只出技术类 + 假 LLM 实收新提示词 | **PASS** | 8 条混合 → 规则剔 2 → 组装再剔 2 → 精选 4 条全技术；加工+综述系统提示词均含内容政策 |
| 7 | 回归：pytest / ruff / m5r+m6r 通过 | **PASS** | 第 1 轮 pytest 406；**P3 复验轮 408 passed**、ruff 全绿、m5r 8 套件 76 检查 0 失败、m6r 7 套件 106 检查 0 失败（2 项公网核对跳过） |
| 8 | 文档一致（README / DESIGN §3.8 / PLAN v1.5） | **PASS（含 P3 项，见 §4）** | 四道机制与代码一一对应；口径已统一为「三道过滤 + 一层排序加权」；PLAN 复核证据已落盘 |

**放行意见：可以提交并发布。** 无 P0/P1/P2；P3 复验 4 条中 3 条闭环、1 条部分闭环（`config.example.yaml`
注释仍未注明整体替换语义，见 §4），均为文档类小缺口，不阻断发布。

### 0.1 执行结果总表

| 套件 | 检查项 | 结果 | 证据 |
|---|---|---|---|
| `m7r_prefilter.py`（声明 1/2/3） | 17 | 17 PASS / 0 FAIL | `reports/evidence/m7r/m7r_prefilter.json` |
| `m7r_compose_rank.py`（声明 4/5） | 8 | 8 PASS / 0 FAIL | `reports/evidence/m7r/m7r_compose_rank.json` |
| `m7r_e2e.py`（声明 6 + CLI 佐证 1/5） | 13 | 13 PASS / 0 FAIL | `reports/evidence/m7r/m7r_e2e.json` |
| `m7r_docs.py`（声明 8） | 11 | 11 PASS / 0 FAIL | `reports/evidence/m7r/m7r_docs.json` |
| `m7r_regression.py`（声明 7） | 6 | 6 PASS / 0 FAIL | `reports/evidence/m7r/m7r_regression.json` |
| **m7r 合计（`m7r_run_all.py` 5 套件）** | **55** | **5/5 rc=0，55 PASS / 0 FAIL** | `reports/evidence/m7r/run-all-summary.json` |
| `m7r_p3_fixes.py`（P3 复验，第 2 轮） | 14 | 13 PASS / 1 FAIL（M7-3 部分未闭环，见 §4） | `reports/evidence/m7r/round2/m7r_p3_fixes.json` |

外部回归：`pytest` **408 passed**（P3 复验轮；第 1 轮为 406）｜`ruff check .` → `All checks passed!`｜
m5r 8 套件 rc=0（**76 检查 0 失败**）｜m6r 7 套件 rc=0（**已执行 106 检查 0 失败**；
其中 2 项依赖公网实时页面的核对因网络不可用跳过：`m6r_trending` 1.13、`m6r_p2_fixes` R2-1.5）。

### 0.2 一键入口

`m7r_run_all.py` 顺序执行 5 个套件；最终运行结果：**5/5 rc=0，55 项检查 0 失败**
（`reports/evidence/m7r/run-all-summary.json`，逐套件日志 `run-*.log`）。

## 1. 方法与约束遵守

- 假服务全部 localhost：假 RSS（`127.0.0.1` 随机端口）+ 假 LLM（OpenAI 兼容桩，记录请求体）+ 临时 staged 配置/库；
  未推送、未发邮件、未调真实 LLM；未访问公网（m6r 历史套件内 GitHub Trending 只读核对为既有允许项，失败可跳过）。
- 未改 `src/**`，未改 `config.yaml` / `run_daily.bat` / 真实 `data/` / `logs/`（回归末尾比对真实库快照一致）。
- 既有 `m5r_*` / `m6r_*` 脚本**零改动**复跑（本轮改动未要求适配，详见 §2.7）。
- 中文输出先写 UTF-8 文件再读。

### 1.1 端到端复现步骤（staged）

1. 起假 RSS（8 条：4 技术 + 2 明确硬件/商业 + 2 擦边）与假 LLM；
2. `aihot fetch` 落库 → `aihot run --dry-run --no-image`（完整流水线，不推送）；
3. `aihot enrich --dry-run --top 10` 复核 Top 分数与剔除样本。

## 2. 逐条声明验证

### 2.1 声明 1：规则层剔除 — PASS

33 条混合批次（13 条明确硬件/商业 + 20 条工程）一次跑完：

| 检查 | 结果 | 关键证据 |
|---|---|---|
| 1.1 批量统计 `total=33 / dropped_topic=13 / kept=20` | PASS | `m7r_prefilter.json` |
| 1.2 逐条期望核对 33/33 一致（0 不一致） | PASS | `m7r_prefilter/1-topic-matrix.json` |
| 1.3 样本原因键精确为 `主题不符（硬件/商业）`，≤5 条截断 | PASS | 同上 |
| 1.4 `dropped_topic` 精确等于阈值样本数 | PASS | 13 == 13 |
| 1.5 保留集合恰为工程标题（顺序保持） | PASS | 20 条 |
| CLI 佐证：`enrich --dry-run` 显示 `[主题不符（硬件/商业）] 台积电…` / `…raises $50M…` | PASS | `m7r_e2e/5-enrich-top.txt` |

样本覆盖：`英伟达洽谈收购…`（收购）、`AI startup raises $50M…`（英文融资）、`台积电 2nm 晶圆产能…`（晶圆）、
`液冷散热方案…`（液冷）、`Nvidia data center revenue…`（data center+revenue）、`server racks/chip makers`、
`固态硬盘`、`显卡涨价`、`并购/估值/IPO/财报/裁员`、`acquires/valuation/funding round` 等中英各半。

### 2.2 声明 2：误杀护栏（关键） — PASS

| 检查 | 结果 | 关键证据 |
|---|---|---|
| 2.1 含 GPU/显存/型号名工程内容 20/20 保留 | PASS | `AirLLM：单卡 4GB GPU 跑 70B`、`单张 RTX 3090 上 Qwen 27B 达 140 tok/s`、`vLLM 推理优化`、**`可在消费级显卡运行的小模型：4GB 显存跑 7B`** |
| 2.1 新构反例 8 条（2080 Ti/4090/8GB 内存/国产显卡/A100 vs H100/服务器部署/梯度检查点/single 4090）零误杀 | PASS | `m7r_prefilter/1-topic-matrix.json` |
| 2.2 同批次正对照 13 条硬件/商业全部剔除（排除"模式整体失效"假象） | PASS | 同上 |

反证尝试：把"数据中心"这类**内置高精度词**写进工程标题会被剔除——但这是设计取舍（DESIGN §3.8 明示：
规则层"宁漏勿错"，裸词显卡/GPU/芯片不排除）；本轮所有含 **GPU/显存/型号/服务器** 的工程措辞均保留。

### 2.3 声明 3：配置行为 — PASS

| 检查 | 结果 | 关键证据 |
|---|---|---|
| 3.1 默认配置内置生效；未命中自定义的 web3 标题保留 | PASS | `dropped_topic=1 kept=[web3…]` |
| 3.2 `exclude_keywords` 追加生效（内置+自定义同时剔除） | PASS | `dropped_topic=2 kept=0` |
| 3.3 `use_builtin_exclude: false` 仅自定义生效（内置硬件词放行） | PASS | `kept=[台积电 2nm…]` |
| 3.4 校验接受新键（值正确加载） | PASS | `m7r_prefilter.json` |
| 3.5 四类错误类型报错：`exclude_keywords` 字符串/含非字符串、`use_builtin_exclude` 字符串/数字 | PASS | 报错片段精确点名对应键 |
| 3.6/3.7 CLI `check`：合法 rc=0；错误 rc=3 且点名 `use_builtin_exclude` | PASS | `m7r_prefilter/3.6-*.txt` / `3.7-*.txt` |

### 2.4 声明 4：组装层 — PASS

夹具：4 条技术类 + 3 条排除分类（importance 99）+ 1 篇硬件论文 + 1 篇技术论文，同一期日报：

| 检查 | 结果 | 关键证据 |
|---|---|---|
| 4.1 三分区（精选/简讯/论文）均无 `行业动态/融资商业/政策监管` | PASS | 泄漏=[]，`4-digest-sections.json` |
| 4.2 技术类正常进入：4 条进精选、1 篇技术论文进论文；硬件论文被挡 | PASS | `top=[开源工具,模型发布,研究突破,产品更新]`、`papers=[研究突破]` |
| 4.3 同日混合顺序正确（按综合分降序，商业 99 分沉底被排除） | PASS | `AgentDesk(67.83) > Nova(56.83) > 长上下文(49.33) > 插件更新(41.83)` |
| 4.4 `is_publishable` 三个排除分类均 False、技术类均 True | PASS | 逐条核对 |
| 4.5 论文区同样受排他约束 | PASS | 论文区无硬件论文 |
| 4.6 简讯区非空时同样排除（top_n=2 时技术条目落入简讯） | PASS | `briefs=[研究突破,产品更新]`，`4.6-briefs.json` |
| 端到端佐证：md 与 DB payload 无排除分类标签/条目 | PASS | `m7r_e2e/3-digest-archive.md`、6.7/6.8 |

### 2.5 声明 5：排序层 — PASS

| 检查 | 结果 | 关键证据 |
|---|---|---|
| 5.1 同内容下 `模型发布 62.33` vs `融资商业 22.33`（差 40.0，负分 −30 生效） | PASS | `5-scores.json` |
| 5.2 商业 99 分条目（32.78）仍低于技术 60 分条目（49.33） | PASS | 同上 |
| CLI 佐证：Top 中 `行业动态 −6.2`、`融资商业 −16.2` 低于全部技术条目（52.1~65.1） | PASS | `m7r_e2e/5-enrich-top.txt`、`6-cli-top-rows.json` |

### 2.6 声明 6：端到端 — PASS

staged：假 RSS 8 条（技术 4 + 明确硬件/商业 2 + 擦边 2）→ `fetch` 落库 → `run --dry-run --no-image`：

| 检查 | 结果 | 关键证据 |
|---|---|---|
| 6.1 fetch 8 条 rc=0 | PASS | `m7r_e2e/1-fetch.txt` |
| 6.2 run rc=0、状态 ok、推送已跳过 | PASS | `2-run-dry-run.txt` |
| 6.3 规则层剔 2（8→预筛保留 6） | PASS | 输出行 `② 加工: 8 条 → 预筛保留 6` |
| 6.4 组装层再剔 2 擦边（精选 4 + 简讯 0） | PASS | `③ 日报: … 精选 4 条 + 简讯 0 条` |
| 6.5/6.6 日报 md：4 个技术标题全在，`台积电/raises $50M/RTX 5090/涨价/EuroCloud` 零出现 | PASS | `3-digest-archive.md` |
| 6.7/6.8 md 与 DB payload 无排除分类（结构化核对） | PASS | categories=`[模型发布,开源工具,开源工具,研究突破]` |
| 6.9 **假 LLM 实收加工系统提示词**含 `开发者向 / 不收录硬件资讯 / 公司间商业消息`（2 个批次均含） | PASS | `4-enrich-system-prompt.txt`（请求体原文） |
| 6.10 综述系统提示词含 `只写技术向内容 / 不要写硬件资讯` | PASS | `4-summary-system-prompt.txt` |
| 6.11 擦边条目确实送进 LLM（`RTX 5090`/`EuroCloud` 出现在 user 载荷；被规则剔除的不出现） | PASS | 请求记录（`LLM 请求总次数=3`） |
| 6.12 CLI 预筛样本显示主题剔除原因 | PASS | `5-enrich-top.txt` |
| 6.13 CLI 分数：商业/硬件类低于全部技术类 | PASS | `6-cli-top-rows.json` |

> 流程说明：`run --dry-run` 的采集阶段不落库，故先执行一次 `fetch`（写临时库），再 `run --dry-run`；
> 全程 staged，未触碰真实资产。

### 2.7 声明 7：回归 — PASS

| 检查 | 结果 | 关键证据 |
|---|---|---|
| 7.1 `pytest` 全绿：rc=0、0 failed | PASS | `reports/evidence/m7r/m7r_regression/pytest.log`（`run-m7r_regression.log`） |
| 7.2 用例数：第 1 轮 **406 passed**；P3 复验轮 **408 passed**（`-q` 双静默问题已修正后重新解析） | PASS | 同上 + `reports/evidence/m7r/round2/m7r_p3_fixes/regression_pytest.txt` |
| 7.3 `ruff check .` 零告警（`All checks passed!`） | PASS | `reports/evidence/m7r/m7r_regression/ruff.log` |
| 7.4 m5r 8 套件全部 rc=0（preflight/fetch/skip/alert/report/render_alert/bat_local/round2_proxy），**76 检查 0 失败** | PASS | `reports/evidence/m7r/regression/m5r/run-all-summary.json`、`m7r_regression/m5r-suites.json` |
| 7.5 m6r 7 套件全部 rc=0（p2_fixes/trending/proxy_health/degrade_e2e/degrade_alert/health_latency/regression），**已执行 106 检查 0 失败** | PASS | `reports/evidence/m7r/regression/m6r/run-all-summary.json`、`m7r_regression/m6r-suites.json` |
| 7.6 真实库 / 真实日报存档快照与测试前一致（db 1240 条 / digests 21 份） | PASS | `m7r_regression.json`（7.6） |

- **m5r/m6r 套件零改动直接复跑通过**：夹具未用「行业动态」造 compose 日报（渲染夹具直接构造 `Digest`，
  不经过 `compose.is_publishable`），本轮分类排除为设计变更、无需适配。
- m5r 的 `m4_alert` 回归会直连真实 qyapi 域名，按 localhost-only 约束以 `AIHOT_M5R_SKIP_M4_ALERT=1`
  跳过（其余 8 套件全跑）；m6r 复跑证据重定向到 `reports/evidence/m7r/regression/`，未覆盖历史证据。
- m6r 中 2 项依赖公网实时页面的核对（trending 真实页面解析、p2 误杀核对）本次因网络不可用显示为
  NOTE 跳过（不计入失败）；其余全部执行并通过。

### 2.8 声明 8：文档一致 — PASS（P3 项复验见 §4）

| 检查 | 结果 | 关键证据 |
|---|---|---|
| 8.1 README「内容范围」表：收录 4 类 + 不收录 2 类（硬件/商业）+ 政策监管 + 机制句 | PASS | `m7r_docs/1-readme-content-range.txt` |
| 8.2 DESIGN §3.8 四道机制符号（`exclude_keywords`/`compose.is_publishable`/`interest_bonus`/排除分类）一一对应 | PASS | `3.8` 原文 |
| 8.3 DESIGN 四层描述（①②③④）齐全 | PASS | 同上 |
| 8.4 PLAN 附录 A v1.5：含"内容范围调整为开发者向"+四层要点+4 个序号 | PASS | `3-plan-v1.5-row.txt` |
| 8.5 `config.example.yaml` 与真实 `config.yaml` 均含新键 | PASS | 键列表一致 |
| 8.6 两配置的 `interest_bonus` 与 `DEFAULT_INTEREST_BONUS` 完全一致 | PASS | 7 分类逐项一致 |
| 8.7 "裸词显卡/GPU/芯片不入排除表"承诺与代码事实一致 | PASS | 排除表仅含 `显卡价格/显卡涨价`；无 GPU/裸芯片 |
| 8.8 提示词与文档承诺一致；8.9 组装排除集合与文档一致 | PASS | — |
| 8.10 PLAN v1.5 复核证据落盘且与记述一致（78 条 / 主题不符 1 / 工程在报） | PASS | `m7r_docs/4-plan-evidence-recheck.txt` |
| 8.11 `interest_bonus`「整体替换」：README/DESIGN 已说明且行为一致 | PASS | `6-partial-bonus.json`（部分配置仅保留 1 类） |

## 3. 问题清单（分级）

| 级别 | 编号 | 问题 | 状态（P3 复验后） | 建议 |
|---|---|---|---|---|
| P3 | M7-1 | 文档口径混乱：标题「三层过滤」但列出四道机制 | **已闭环**：README/DESIGN/PLAN 统一为「三道过滤 + 一层排序加权」（§4 P3-1） | — |
| P3 | M7-2 | 主题剔除的可观测性弱：CLI 摘要行与 `runs.stage_stats` 无 `dropped_topic` | **已闭环**：CLI 行含 `主题不符 N`；enrich 记录带 `dropped_*` 四项（§4 P3-2） | — |
| P3 | M7-3 | `interest_bonus` 整体替换语义未在文档说明 | **部分闭环**：README/DESIGN 已注明；`config.example.yaml` 注释**仍未更新**（§4 P3-3） | 给 `interest_bonus` 注释补一行「整体替换，需列全分类」 |
| P3 | M7-4 | PLAN v1.5 的真实数据复核无证据文件 | **已闭环**：落盘 `docs/evidence/content-policy/2026-10-12-recheck.txt` 并在 PLAN 引用（§4 P3-4） | — |
| P3 | M7-5 | （复验新发现）`config.example.yaml` 的 `interest_bonus` 注释仍是旧文案「按分类；可为负数 = 降权」 | **未闭环**（属 M7-3 缺口） | 同 M7-3 |

> 测试侧观察（非产品问题）：
> 1）`m6r_regression` 的 ruff 检查要求"零输出"，首跑时因验收方正在新建 `m7r_*` 脚本
> （当时尚未跑 lint）被误报一次 FAIL；修复后复跑通过。
> 2）`m6r_regression`（上轮套件、按设计以 `m5r/round3` 为 m5r 复跑证据根目录）在复跑时会重写
> `reports/evidence/m5r/round3/**` 的时间戳类内容；复跑完成后已将该目录恢复为仓库既有版本，
> 本轮结果保留在 `reports/evidence/m7r/regression/` 下。
> 3）`pytest -q` 与 `pyproject addopts` 的 `-q` 叠加会吞掉 "N passed" 汇总行，导致解析失败；
> 验收脚本已改为不额外传 `-q`（见 `m7r_regression.py`）。

## 4. P3 修复复验（第 2 轮，2026-10-12）

复验脚本：`tests/independent/m7r_p3_fixes.py`（证据 `reports/evidence/m7r/round2/`，14 项检查）+
一键入口 `m7r_run_all.py` 第 2 轮复跑（5/5 rc=0）。

| 修复项 | 复验方法（黑盒） | 结果 | 证据 |
|---|---|---|---|
| **P3-1** 口径统一 | 读取 README「内容范围」块 / DESIGN §3.8 / PLAN v1.5 行，断言三处均含「三道过滤 + 一层排序加权」且无旧「三层过滤」 | **PASS** | `round2/m7r_p3_fixes/m7-1_*.txt`；主套件 `m7r_docs` 8.1/8.2/8.4 |
| **P3-2a** CLI 摘要行 | staged 假 RSS 6 条（3 技术 + 2 主题不符 + 1 招聘）→ `fetch` → `enrich --dry-run --no-llm`，断言预筛行并列显示四项计数 | **PASS**：`输入 6 → 保留 3（垃圾 1 · 与 AI 无关 0 · **主题不符 2** · 批内重复 0）` | `round2/m7r_p3_fixes/m7-2_2-enrich-dry-run.txt` |
| **P3-2b** runs 记录 | 同数据 `enrich --no-llm`（非 dry-run）→ 查询 `runs.stage_stats LIKE '%"stage": "enrich"%'` | **PASS**：`dropped_blacklist=1 / dropped_offtopic=0 / dropped_topic=2 / dropped_duplicate=0 / prefilter_kept=3` | `round2/m7r_p3_fixes/m7-2_4-enrich-stage-stats.json` |
| **P3-3a** 配置模板注释 | 原文读取 `config.example.yaml` 的 `interest_bonus` 注释段 | **FAIL**：注释仍为「兴趣加成（按分类；可为负数 = 降权）」，无"整体替换/列全" | `round2/m7r_p3_fixes/m7-3_config-example-bonus-comment.txt` |
| **P3-3b** README/DESIGN | 断言两处出现"整体替换" | **PASS** | `m7r_docs` 8.1/8.11 |
| **P3-3c** 行为一致 | 只写 1 个分类的配置 → 加载后确认其余分类未保留默认加成（整体替换） | **PASS**：`{'模型发布': 20.0}` | `round2/m7r_p3_fixes/m7-3_partial-bonus-loaded.json` |
| **P3-4** 复核证据 | 证据文件存在且含 78/主题不符 1/工程样例；PLAN 行引用该路径 | **PASS**（1593 字节，含 AirLLM / RTX 3090 在报样例） | `round2/m7r_p3_fixes/m7-4_recheck.txt` |
| **回归** | `pytest`（应 408）+ `ruff check .` | **PASS**：408 passed / 0 failed；ruff `All checks passed!` | `round2/m7r_p3_fixes/regression_pytest.txt`、`regression_ruff.txt` |

复验结论：**4 条 P3 中 3 条完全闭环、1 条部分闭环**（M7-3/M7-5：仅缺 `config.example.yaml` 注释一行）。
该缺口不影响逻辑正确性（README/DESIGN 已说明，行为已复核），属文档类 P3，不阻断发布。

## 5. 复现方式

```powershell
cd D:\信息收集整理\aihot
.venv\Scripts\python.exe tests\independent\m7r_run_all.py
```

单套件：`m7r_prefilter.py` / `m7r_compose_rank.py` / `m7r_e2e.py` / `m7r_docs.py` / `m7r_regression.py`；
P3 复验：`m7r_p3_fixes.py`（证据落 `reports/evidence/m7r/round2/`）。

## 6. 放行意见

**通过，可提交并发布到 GitHub。** 核心声明 1~8 全部 PASS：规则层高精度剔除且工程内容零误杀（护栏 20/20）、
配置开关与校验完整、组装/排序双层兜底有效、端到端混合数据只出技术类且 LLM 实收提示词含内容政策、
回归全绿（P3 复验轮 pytest **408 passed** / ruff 零告警 / m5r 76 检查 / m6r 106 检查 0 失败）。
P3 复验 4 条中 3 条完全闭环、1 条部分闭环（`config.example.yaml` 的 `interest_bonus` 注释未注明
"整体替换、需列全分类"，见 M7-3/M7-5），该缺口为文档类 P3，不影响行为正确性，建议随下次小改动补上。
