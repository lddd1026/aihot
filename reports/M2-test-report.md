# M2 测试报告

- **测试时间**：2026-09-24（本机）
- **被测版本**：`aihot 0.1.0`（editable 安装）；**第 1 轮**指纹 `sha256=5867c83e…`、**第 2 轮**指纹 `sha256=5f3abd6e…`、**第 3 轮（最终）**指纹 `sha256=39efbe26258f7502658fa7faa69078b743954649be72cb7c90163e5be5e9255d`；每轮测试结束后比对均为 **0 个文件变化**（`evidence/m2/revision-fingerprint.json`、`revision-compare.txt`）
- **被测范围**：M2 加工层 —— `pipeline/`（prefilter / dedup / enrich / prompts / rank）、`store` 的加工读写（`unenriched_items` / `mark_enriched` / `mark_filtered` / `save_clusters` / `next_cluster_id`）、`cli.py::enrich`、`config` 的 `pipeline` 段
- **测试环境**：Windows 11 + Python 3.12.10（项目 venv）；pytest 9.1.1；ruff 0.16.8；rapidfuzz 3.x；真实网络（仅用于小样本真实 LLM 与 M0/M1 回归）
- **测试方式**：冷启动独立验证；**黑盒优先**（CLI + SQLite 观察，不导入产品代码做断言）；坏数据注入使用本地假 LLM 服务（OpenAI 兼容 `POST /chat/completions`，按请求动态生成响应并统计调用次数）；真实库只读守护贯穿全部脚本
- **首轮结论（2026-09-24）**：**FAIL（P0:0, P1:2, P2:5）**
- **第 2 轮回归结论（2026-09-24）**：**FAIL（P0:0, P1:1, P2:2）** —— 首轮 D1 与 P2-1~P2-5 已修复并复验通过；D2 在**默认配置（`llm_refine=false`，真实 `config.yaml` 现值）下已验证修复**，但 `llm_refine=true` 路径仍复现（新 P1 = D3）。默认生产路径不受影响；若维护者将 `llm_refine` 标注为实验特性/不支持，D3 可降级为 P2 并判定 PASS
- **第 3 轮聚焦复验结论（2026-09-24 · 最终）**：**PASS（P0:0, P1:0, P2:1）** —— D3 已修复并复验（`llm_refine=true` 精修合并后簇编号仍全局单调）；真实库历史数据已用修复后的默认配置重建（176 条 / 101 簇 / 0 悬空 / 0 mention 不一致，2 个多条目簇人工核验正确）；P2-C 与 P2-1 补充已复验；全量回归 19 个脚本全绿、`ruff` exit 0、`pytest 237 passed`；遗留 P2-1（预筛词表误杀 8.5%）按 `PLAN.md` §6 归入观察期调优

---

## 回归验证（第 3 轮 · 最终 · 2026-09-24）

- **回归范围**：D3 复验（`m2r2_d2.py`）+ 真实库数据状态复核（`m2r3_real_db.py`）+ P2 复验（`m2r2_p2.py`、`m2r3_p2.py`）+ 全量回归（19 个脚本：10 个 `m2_*.py`、4 个 `m2r2_*.py`、2 个 `m2r3_*.py`、`m2_regression.py` + M0/M1）
- **被测修订版**：指纹 24 文件 `sha256=39efbe26…`，测试后 **0 变更**
- **最终结论**：**PASS（P0:0, P1:0, P2:1）**
- **关键实测**：`ruff` exit 0；`pytest` **237 passed / 0 failed / 0 errors**；19 个脚本 **0 FAIL**；D2/D3 复验 **16/16**；聚类准确率（默认配置）**99.79%**；真实库 **176 / 176 / 0 / 101 / 101 / 0 / 0**（items/已处理/待处理/簇数/max_cluster_id/悬空/mention 不一致）且多条目簇 2/2 人工核验正确

### R3.0 本轮修复项复验总览

| 编号 | 修复声明 | 独立复验结果 | 判定 |
|---|---|---|---|
| D3 | `_remap_cluster_ids()` 移到 LLM 精修之后 + 注释 + 单测 | `llm_refine=true` 场景：精修合并后新簇编号 **[7,8,9] > 历史最大 6**；历史簇逐行不变；无悬空；mention 一致 | ✅ 已修复 |
| P2-C | `--no-llm` 的 runs 记录补齐统计字段（`_no_llm_stats()`） | `--no-llm` 记录含 `no_llm: true` + `batches=0/cost_cny=0.0/tokens=0` 等全部字段；LLM 路径字段齐备 | ✅ 已修复 |
| P2-1 补充 | 词表新增 `\bNPUs?\b`、`\bVLM\b`、`\bMLOps\b` | NPU/NPUs/"Show HN: Npunlock…NPUs"/MLOps/VLM 全部过门槛，控制组仍淘汰；真实库预筛保留数 104 → **105** | ✅ 已修复（遗留见 P2-1） |
| P2-A 数据清理 | 真实库旧版过度合并结果清空并用默认配置重建 | 176 条全部重新加工（真实 LLM，¥0.089）、101 簇、编号 1..101 全局单调、0 悬空、0 mention 不一致；多条目簇从 19 个降至 **2 个**（人工核验 2/2 正确） | ✅ 已清理 |

### R3.1 D3 复验（`m2r2_d2.py` 16/16）

| 场景 | 实测 | 判定 |
|---|---|---|
| 3a 合成库两轮（--no-llm） | 新增簇 **[7,8,9,10]**；无悬空；mention 一致；历史 6 簇与旧条目分配不变 | ✅ |
| 3b 真实库副本（--no-llm，历史 101 簇） | 新增簇 **[102,103,104,105,106,107]**（均 >101）；无悬空；mention 一致；历史 101 簇逐行不变 | ✅ |
| 3c **精修路径**（`llm_refine=true`） | 精修提示词使用规则层编号（listing 1..4）；假 LLM 返回 high 组 [1,2] → 精修合并发生（2 条合 1 簇）；**合并后新簇编号 [7,8,9] 均 > 历史最大 6**；无悬空；mention 一致；历史 6 簇行内容 0 变化 | ✅ **D3 已修复** |
| 3z 真实库现状 | 101 簇、0 悬空、mention 全部一致 | ✅ |

证据：`evidence/m2/regression3/d2.json`、`d2/3a-synthetic-two-rounds.txt`、`d2/3b-real-copy.txt`、`d2/3c-refine-path.txt`、`d2/3z-real-db-integrity.json`

### R3.2 真实库数据状态复核（只读，`m2r3_real_db.py` 7/7）

| 指标 | 开发者声明 | 实测 | 判定 |
|---|---|---|---|
| items | 176 | **176** | ✅ |
| 已处理 / 待处理 | 176 / 0 | **176 / 0**（另 71 条预筛淘汰已标记，105 条加工） | ✅ |
| clusters / max_cluster_id | 101 / 101 | **101 / 101**（min=1，单调 1..101） | ✅ |
| 悬空 cluster_id | 0 | **0** | ✅ |
| mention_count 不一致 | 0 | **0**（空簇 0） | ✅ |
| 簇表自洽（item_hashes） | — | 全部与实际成员集合一致（cluster 79 的 item_hashes 含重复 URL 哈希属跨源同 URL 的正常现象） | ✅ |
| 多条目簇抽样 | — | **2 个**：Claude Opus 5.5 ×3、GPT-6 Sol and Luna ×3，人工核验 2/2 为同一事件（跨源）；最大簇 3 条，单条目簇 99 个 | ✅ |

> 旧版 19 个过度合并簇（如 11 条 OpenAI 混簇）已从真实库消失；重建运行记录：`pending=176 / kept=105 / clusters=101 / enriched=105 / filtered=71 / batches=7 / token 6779+9433 / cost ¥0.089 / refine_calls=0`（`evidence/m2/regression3/real-db/6a-real-db-state.json` 同目录 runs 证据）

### R3.3 P2 复验（`m2r2_p2.py` 19/19 + `m2r3_p2.py` 10/10）

| 场景 | 实测 | 判定 |
|---|---|---|
| NPU / NPUs / "Npunlock…NPUs" / MLOps / VLM 过门槛 | 5 条全部保留；控制组 "Rust 1.99 released" 仍淘汰 | ✅ |
| `use_builtin_blacklist` / runs / time_budget（第 2 轮用例重跑） | 全部通过；`--no-llm` 记录字段与 LLM 路径同构、值全 0 | ✅ |
| `runs` LLM 路径 | `stage=enrich`、16 字段齐备、`cost_cny=0.006` 与假 usage 复算一致 | ✅ |

### R3.4 全量回归（19 个脚本，0 FAIL）

| 脚本 | 结果 | 脚本 | 结果 |
|---|---|---|---|
| m2_prefilter.py | PASS 12/12 | m2r2_d2.py | **PASS 16/16** |
| m2_dedup.py | PASS 16/16 | m2r2_p2.py | PASS 19/19 |
| m2_llm_robust.py | PASS 60/60 | m2r3_p2.py | PASS 10/10 |
| m2_cache.py | PASS 8/8 | m2r3_real_db.py | PASS 7/7 |
| m2_cost.py | PASS 9/9 | m2_regression.py | PASS 25/25 |
| m2_rank.py | PASS 17/17 | m2r2_d1.py | PASS 15/15 |
| m2_cli.py | PASS 26/26 | m2r2_accuracy.py | PASS 7/7 |
| m2_real_llm.py | PASS 7/7 | m2_perf.py | PASS 5/5 |
| 真实库快照 | 前后一致（176/5/101，`real_db_unchanged=true`） | INFO 脚本 ×3 | 正常 |

| 工具链 | 实测 |
|---|---|
| `ruff check .` | **exit 0** |
| `pytest` | **237 passed / 0 failed / 0 errors**（4.0s，与开发者声明一致） |
| M0 六脚本 | 105/105、13/13、13/13、5/6（已接受 `bad_alias`）、4/4、10/10 |
| M1 十脚本 | acceptance 19/19、adversarial 11/11、url_edges 4/4、units 19/19、contracts 6/6、check_net 6/6、proxy_bad_port 4/4、socks5 4/4、slow_source 1/1、perf 6/6 |
| 密钥扫描 | 0 命中 |
| 聚类准确率（默认配置，54 条 / 1431 对） | accuracy **99.79%**、precision **100%**、recall 87.5% |
| 真实库多条目簇 | 2 个 / 对级 100%（人工核验） |
| 真实 LLM | 6 条 ¥0.0057 → 200 条/天 **¥0.19** |
| 性能 | 176 条 **4.4s**、400 条最坏 **11.3s**（阈值 720s） |

### R3.5 最终判定与遗留

**PASS（P0:0, P1:0, P2:1）** —— 按 `PLAN.md` §5.4「P0、P1 必须清零」，门禁通过，可进入 M3。

- **P2-1（遗留，观察期调优）**：真实数据预筛误杀 **6/71（8.5%，占全库 3.4%）**，较首轮 7 条减少 1 条（NPU 已修复）。剩余：`Meta's Muse`×2、`LensVLM`（`\bVLM\b` 不匹配连写形式）、`Comma's hands-off driving tech`（自动驾驶口语）、`Tokens too cheap to meter` / `The Price of Intelligence…`（边缘）。`PLAN.md` §6 已将该类问题列为「观察期按真实数据调优」，不阻断 M3。
- **观察项**：① `llm_refine=false`（默认）下「改写措辞」的同事件对会漏合并（本轮样本 recall 87.5%，3 对；如中英混合/改写标题），如需更高召回可开启精修（D1 防护已就绪）或人工合并；② LLM 路径的 runs 记录不含 `no_llm` 键（缺省即视为 false），字段与 `--no-llm` 路径略不对称，属文档级细节。
- 首轮 P2-2~P2-5、第 2 轮 P2-A/P2-B/P2-C 均已修复或清理完毕。

### R3.6 第 3 轮证据索引（`reports/evidence/m2/regression3/`）

| 证据 | 内容 |
|---|---|
| `run-all-summary.json` / `.txt` / `logs/` | 19 个脚本总览（0 FAIL）+ 真实库前后快照 |
| `d2.json` + `d2/` | D3 复验（3a/3b/3c/3z + 精修路径合并后编号证据） |
| `real-db.json` + `real-db/6a-real-db-state.json`、`6b-real-multi-clusters.json` | 真实库计数/完整性/簇抽样核验 |
| `p2.json` + `p2/` | P2 复验（GPU/黑名单/runs/时间预算） |
| `p2-supplement.json` + `p2-supplement/` | NPU/VLM/MLOps 词表与 `--no-llm` runs 字段 |
| `regression.json` + `regression/` | ruff、pytest 237、M0 六脚本、M1 十脚本、密钥扫描、真实库快照 |
| `dedup.json`、`accuracy/`、`prefilter.json` 等 | 第 3 轮重跑原有套件（生产库 2m/2n 已按重建后数据核验） |
| `revision-fingerprint.json` / `revision-compare.txt` | 第 3 轮指纹 `39efbe26…`，测试后 0 变更 |

---

## 回归验证（第 2 轮 · 2026-09-24）

- **回归范围**：D1 / D2 / P2-1~P2-5 逐项独立复验 + 全量回归（原有 10 个 `m2_*.py`、新增 4 个 `m2r2_*.py`、M0 六脚本、M1 十脚本、ruff、pytest）
- **被测修订版**：指纹 24 文件 `sha256=5f3abd6e…`（`evidence/m2/revision-fingerprint.json`），测试后 **0 变更**
- **新结论**：**FAIL（P0:0, P1:1, P2:2）**
- **关键实测**：D1 复验 **15/15**；聚类准确率（默认配置）**99.79%**（54 条 / 1431 对）；D2 复验 13/16（3a/3b 通过、3c 精修路径失败）；P2 复验 **19/19**；`ruff` exit 0；`pytest` **236 passed / 0 failed**；M0/M1 全绿

### R2.0 修复项复验总览

| 编号 | 修复声明 | 独立复验结果 | 判定 |
|---|---|---|---|
| D1 | llm_refine 默认 false + 提示词收紧 + `confidence` 门控 + `validate_merged_groups` 规则校验 | 默认配置零精修调用；high 但互不相关 → 规则拆散（`refine_rejected=1`）；medium → 不合并；真相关（相似度 83.6）→ 正常合并；模块级 >4 拆散 / 混合组拆连通分量 / <55 拒绝 | ✅ 已修复 |
| D2 | `next_cluster_id()` 全局单调编号（`_remap_cluster_ids`），历史簇不被覆盖 | `--no-llm` 路径：合成库新簇 7~10（>6）、真实库副本新簇 65~70（>64）、无悬空、mention 一致、历史簇逐行不变 ✅；`llm_refine=true` 路径：合并后编号回到 1,2,3 → 覆盖历史簇、mention 不一致 ❌ | ⚠️ 部分修复（D3） |
| P2-1 | `\bGPUs?\b`（TPU 同理） | GPU/GPUs/TPU/TPUs 全部过门槛；控制组仍淘汰 | ✅ 已修复 |
| P2-2 | （声明未列，隐性）版本号护栏 `VERSION_CONFLICT_CAP=60` | Llama-4 vs Llama-5 相似度降至 60 → 不再误合并；Sol/Luna 仍正确区分 | ✅ 已修复 |
| P2-3 | `pipeline.prefilter.use_builtin_blacklist` | 缺省/true=内置+自定义；false=仅自定义；false+空=停用（4 组语义全对） | ✅ 已修复 |
| P2-4 | `enrich` 写 `runs`（含成本/条数/统计） | `stage=enrich`；16 字段齐备；`cost_cny=0.006` 与假 usage 复算一致；`--no-llm` 有基础记录 | ✅ 已修复（1 观察项见 P2-C） |
| P2-5 | `enrich.time_budget_seconds`（默认 600） | 59/7201/"abc"/60.5 → rc=3；60/7200 → rc=0；60s 预算下 2/3 批后停止 + `time_budget_hit` + 不丢数据 | ✅ 已修复 |

### R2.1 D1 复验（假 LLM 注入，`m2r2_d1.py` 15/15）

| 场景 | 注入内容 | 实测 | 判定 |
|---|---|---|---|
| 1a 默认配置 | 不写 `dedup.llm_refine` | LLM 请求=1（仅条目加工）、`refine_calls=0`、10 条 → 10 簇 | ✅ |
| 1b high 但互不相关 | `{"groups":[{"indexes":[1..10],"confidence":"high"}]}` | 10 条 → 10 簇；`refine_rejected=1` 且写入 runs；日志含「聚类精修被规则拒绝」 | ✅ |
| 1c medium | 同组 `confidence=medium` | 不合并；`refine_rejected=0`、`refine_calls=1` | ✅ |
| 1d 确实相关 | 相似度 83.6 的一对，LLM 报 high | 规则层 2 条 → 2 簇（前置校验）；精修后 1 簇、`mention=2`、无拒绝 | ✅ |
| 1e 模块级 | `validate_merged_groups` | >4 拆散、混合组拆分量、≥55 放行、<55 拒绝（相似度 1-2=94.4、1-3=24.6） | ✅ |

证据：`evidence/m2/regression2/regression2/d1.json`、`d1/1a~1e*.txt`、`d1/1e-module-checks.json`

### R2.2 聚类准确率重测（默认配置 `llm_refine=false`，`m2r2_accuracy.py` 7/7）

**样本**：自建跨源样本集 **54 条 / 1431 对**（12 组同事件：9 组近重复 + 3 组改写措辞；24 条独立事件负样本，含 Sol/Luna 与 Llama-4/5 对照），临时库 + `enrich --no-llm`。

| 指标 | 实测 |
|---|---|
| 对级 accuracy | **99.79%**（1428/1431），阈值 ≥90% |
| precision（同事件类） | **100%**（TP=21，FP=0） |
| recall（同事件类） | **87.5%**（TP=21，FN=3；3 对全部来自「改写措辞」组，属默认关闭精修时的预期召回缺口） |
| 最大簇 | 3 条（无超大簇） |

**真实库副本（默认配置，规则层）**：176 条 → 预筛保留 104 条 → **100 簇**；多条目簇 **2 个**（Claude Opus 5.5 ×3、GPT-6 Sol and Luna ×3），人工复核 2/2 为同一事件、跨源（正类对 6 个，precision 100%）。

> 真实库 `clusters` 表仍有 19 个修复前产生的多条目簇（历史遗留，对级精度 24.6%，见 P2-A）；本轮验收准确率以默认配置重新聚类结果为准。

证据：`regression2/regression2/accuracy.json`、`accuracy/2a-pair-stats.json`、`2a-group-status.json`、`2b-real-clusters.json`

### R2.3 D2 复验（`m2r2_d2.py` 13/16）

| 场景 | 实测 | 判定 |
|---|---|---|
| 3a 合成库两轮（--no-llm） | 第一轮 max_id=6；第二轮新增 **[7,8,9,10]**；无悬空；mention 全一致；历史 6 簇逐行不变、旧条目 cluster_id 不变 | ✅ |
| 3b 真实库副本（--no-llm） | 新增 **[65,66,67,68,69,70]**（>64）；无悬空；mention 一致；历史 64 簇逐行不变 | ✅ |
| 3c 精修路径（llm_refine=true） | 规则簇 7~10 精修合并后编号回到 **[1,2,3]** → 覆盖历史簇 1~3、mention 不一致（cluster 1: mention=1 vs 2 条；cluster 3: mention=2 vs 3 条） | ❌ **D3** |
| 3z 真实库现状 | 64 簇 0 悬空、mention 全部与实际条目数一致（派生字段重算成立；仅 1 簇 item_hashes 残留，见 P2-B） | ✅ |

证据：`regression2/regression2/d2.json`、`d2/3a-synthetic-two-rounds.txt`、`d2/3b-real-copy.txt`、`d2/3c-refine-path.txt`、`d2/3z-real-db-integrity.json`

### R2.4 P2 复验（`m2r2_p2.py` 19/19）

| 场景 | 实测 | 判定 |
|---|---|---|
| 4a GPU/GPUs/TPU/TPUs | 4 条全部保留；控制组 "Rust 1.99 released" 仍淘汰 | ✅ |
| 4b `use_builtin_blacklist` | 缺省=true（招聘+辟谣淘汰）；true 同；false=仅自定义（辟谣淘汰、招聘保留）；false+空=停用（0 淘汰） | ✅ |
| 4c `runs` | `stage=enrich`、status=ok、16 字段齐备；`cost_cny=0.006` = 独立复算；`--no-llm` 记录 `no_llm=true`（LLM 统计字段缺省，见 P2-C） | ✅ |
| 4d `time_budget_seconds` | 59/7201/"abc"/60.5 → **rc=3**；60/7200 → rc=0；60s 预算 + 31s/批 → 执行 2/3 批后停止、`time_budget_hit=true`、1 条降级原文、3 条全部落库、rc=0、日志「已达加工时间上限」 | ✅ |

证据：`regression2/regression2/p2.json`、`p2/4a~4d*.txt`、`p2/4b-blacklist-matrix.json`、`p2/4c-runs.txt`、`p2/4d-time-budget-validation.json`

### R2.5 全量回归（`m2_run_all.py`，17 个脚本）

| 脚本 | 结果 | 脚本 | 结果 |
|---|---|---|---|
| m2_prefilter.py | **PASS 12/12** | m2r2_d1.py | **PASS 15/15** |
| m2_dedup.py | **PASS 16/16**（2i/2m/2n/2o/2p 已按第 2 轮口径更新） | m2r2_accuracy.py | **PASS 7/7** |
| m2_llm_robust.py | **PASS 60/60** | m2r2_d2.py | **FAIL 13/16**（D3 证据） |
| m2_cache.py | **PASS 8/8** | m2r2_p2.py | **PASS 19/19** |
| m2_cost.py | **PASS 9/9** | m2_regression.py | **PASS 25/25** |
| m2_rank.py | **PASS 17/17** | m2_translation_sample.py / m2_dbview.py / m2_dedup_probe.py | INFO |
| m2_cli.py | **PASS 26/26** | 真实库快照 | 前后一致（176/4/64，`real_db_unchanged=true`） |
| m2_real_llm.py | **PASS 7/7** | | |
| m2_perf.py | **PASS 5/5** | | |

工具链门槛（`m2_regression.py` 内执行）：

| 项目 | 实测 |
|---|---|
| `ruff check .` | **exit 0**（All checks passed） |
| `pytest` | **236 passed / 0 failed / 0 errors**（3.4s；与开发者声明一致，首轮 226） |
| M0 六脚本 | 105/105、13/13、13/13、5/6（已接受 `bad_alias`）、4/4、10/10 |
| M1 十脚本 | acceptance 19/19、adversarial 11/11、url_edges 4/4、units 19/19、contracts 6/6、check_net 6/6、proxy_bad_port 4/4、socks5 4/4、slow_source 1/1、perf 6/6 |
| `config.example.yaml` 开箱 | rc=0 |
| 密钥扫描（M2 证据/测试脚本） | 0 命中 |
| 真实库 | items=176 / runs=4 / clusters=64 / max_id=176，未变 |

真实链路与性能（第 2 轮复测）：真实 DeepSeek 6 条 **¥0.0057**（104+92 token/条）→ 200 条/天 **¥0.19**、400 条/天 **¥0.38**（< ¥1/天）；176 条全量 **4.2s**、400 条最坏 **10.2s**（阈值 720s）；翻译摘要抽检 20 条仍 **19/20**（唯一不通过项为第 1 轮遗留簇造成）。

证据：`regression2/run-all-summary.json`、`regression2/regression.json`、`regression2/regression/*`、`regression2/real-llm.json`、`regression2/perf.json`

### R2.6 新缺陷（第 2 轮）

#### D3 [P1] `llm_refine=true` 时精修合并会重置簇编号，仍覆盖历史簇（D2 修复不完整）

- **最小复现**（`tests/independent/m2r2_d2.py::case_3c`，全程临时库）：
  1. 6 条条目跑 `enrich --no-llm` → 簇编号 1~6（历史）；
  2. 追加 4 条（其中 2 条相似度 83.6、需精修合并）；
  3. 配置 `dedup.llm_refine: true` + 假 LLM 返回 `{"groups":[{"indexes":[7,8],"confidence":"high"}]}`（7/8 即 `_remap_cluster_ids` 分配的全局编号）跑 `enrich`；
  4. 结果：精修把 4 个规则簇合并为 3 个，编号回到 **1,2,3** —— `clusters` 表 1~3 行被 `INSERT OR REPLACE` 覆盖、历史条目与新条目共用同一 `cluster_id`、`mention_count` 与实际条目数不一致。
- **原始输出**：`聚类精修完成: 4 → 3 簇（跨源簇 1）` / `LLM 精修后: 3 簇` / `已写回: 4 条加工 · 3 个事件簇`；核对：`mention 不一致=[{cluster_id:1, mention_count:1, items:2}, {cluster_id:2, mention_count:1, items:2}, {cluster_id:3, mention_count:2, items:3}]`，历史簇行内容变化 `[1,2,3]`。
- **根因**：`cli._cmd_enrich` 在聚类后立即 `_remap_cluster_ids(clustering, first_cluster_id)`（全局编号生效），随后 `enricher.refine_clusters()` → `dedup.merge_clusters_by_groups()` 内 `enumerate(sorted_groups, start=1)` **重新从 1 编号**；`save_clusters`/`mark_enriched` 随即将这些 1..N 编号写库。
- **影响范围**：仅 `llm_refine: true`（默认 false；真实 `config.yaml` 现为 false；`--no-llm` 路径不受影响）。开启时与首轮 D2 相同：历史簇元数据被覆盖、跨轮 `cluster_id` 冲突、`mention_count` 失真。
- **建议修复（二选一）**：① 把 `_remap_cluster_ids` 调用移到精修之后（对最终 clustering 统一编号）；② 给 `merge_clusters_by_groups` 传入 `base_id` 偏移（如 `base_id=first_cluster_id-1`）。并补一条「开启精修时簇编号仍全局单调」的单测。
- **严重度说明**：默认生产路径（`llm_refine=false`）已由 3a/3b 验证修复；D3 是同一不变式在**非默认、但被加固并保留支持**的开关下的破口。若维护者将 `llm_refine` 标注为实验特性/暂不支持，可将 D3 降级为 P2 并判定 PASS；否则按 P1 处理（当前判定 FAIL）。
- **证据**：`evidence/m2/regression2/regression2/d2.json`（3 项 FAIL）、`d2/3c-refine-path.txt`

### R2.7 第 2 轮遗留 P2（允许遗留）

| 编号 | 标题 | 事实与影响 | 建议 |
|---|---|---|---|
| P2-A | 真实库历史簇仍为修复前的过度合并结果 | 19 个多条目簇（如 cluster 7 含 11 条不同 OpenAI 事件），对级精度 24.6%；系第 1 轮真实运行（修复前）产生的数据，开发者仅重算派生字段（mention_count/source_ids 已自洽）。后续 `enrich` 只处理当日待加工条目、不读历史簇，故不影响未来运行；但直接查询历史簇的消费方会看到错误归并 | 可选：用规则层对历史条目零成本重算一次簇，或文档标注为历史数据 |
| P2-B | cluster 7 的 `item_hashes` 为历史残留 | `mention_count=11` 与实际条目一致，但 `item_hashes` 仅 10 个（无消费方，仅审计字段） | 重算 item_hashes 或忽略 |
| P2-C | `--no-llm` 运行的 `runs` 记录不含 LLM 统计字段 | LLM 路径 16 字段齐备；`--no-llm` 仅基础字段（`batches`/`cost_cny` 缺省），审计时无法区分「0 成本」与「字段缺失」 | 补 `batches=0`/`cost_cny=0.0` 等默认值 |

> 首轮 P2 状态：P2-1（词表）**部分修复**（GPU/TPU 复数已修；Muse/VLM/NPU/comma 自动驾驶等误杀仍在，等观察期调词表）；P2-2、P2-3、P2-4、P2-5 **已修复**。

### R2.8 第 2 轮实测数字汇总

| 指标 | 实测值 |
|---|---|
| `ruff check .` | exit 0 |
| `pytest` | **236 passed / 0 failed / 0 errors**（3.4s） |
| D1 复验 | **15/15** |
| 聚类准确率（默认配置） | **99.79%**（54 条 / 1431 对）；precision 100%、recall 87.5% |
| 真实库副本（默认配置） | 104 条 → 100 簇；多条目簇 2 个（复核 2/2 正确） |
| D2 复验 | 默认路径 ✅（新簇 7~10 / 65~70，历史不变）；精修路径 ❌（编号回到 1,2,3） |
| P2 复验 | **19/19** |
| 全量回归 | 17 脚本：14 PASS + 1 FAIL（D3 证据）+ 3 INFO；M0 6/6、M1 10/10 |
| 真实 LLM | 6 条 ¥0.0057 → 200 条/天 ¥0.19（单次预算 ¥0.2；两轮累计 ≈ ¥0.03） |
| 性能 | 176 条 4.2s；400 条最坏 10.2s（阈值 720s） |
| 真实库 | 前后一致（176 items / 4 runs / 64 clusters） |

### R2.9 第 2 轮证据索引（`reports/evidence/m2/regression2/`）

| 证据 | 内容 |
|---|---|
| `run-all-summary.json` / `.txt` / `logs/` | 17 个脚本总览 + 真实库前后快照 |
| `regression2/d1.json` + `d1/` | D1 复验（1a~1e 原始输出 + 模块级判定） |
| `regression2/accuracy.json` + `accuracy/` | 聚类准确率重测（样本真值、对级混淆矩阵、逐组状态、真实库副本簇） |
| `regression2/d2.json` + `d2/` | D2 复验（3a/3b/3c/3z + D3 复现原始输出） |
| `regression2/p2.json` + `p2/` | P2 复验（GPU / 黑名单矩阵 / runs / 时间预算） |
| `regression2/regression.json` + `regression/` | ruff、pytest、M0 六脚本、M1 十脚本、密钥扫描、真实库快照、`pre-m2-snapshot/` |
| `regression2/*.json`（prefilter/dedup/llm-robust/cache/cost/rank/cli/real-llm/perf） | 第 2 轮重跑原有 `m2_*.py`（全部 PASS） |
| `revision-fingerprint.json`（第 2 轮）/ `revision-compare.txt` | 产品指纹 `5f3abd6e…`，测试后 0 变更 |

---

## 一、结论（第 1 轮）

| 项 | 结果 |
|---|---|
| M2 验收 9 项 | **8 项 PASS、1 项 FAIL**（第 2 项「聚类正确性」） |
| 缺陷 | **P0 = 0，P1 = 2，P2 = 5** → 按 `PLAN.md` §5.4 放行规则 **不允许进入 M3** |
| 工具链门槛 | `ruff check .` exit 0；`pytest` **226 passed / 0 failed / 0 errors** |
| M0/M1 回归 | 全部通过（M0 六脚本 105/105、13/13、13/13、5/6（已接受遗留）、4/4、10/10；M1 十脚本 19/19、11/11、4/4、19/19、6/6、6/6、4/4、4/4、1/1、6/6） |
| 真实库 | 测试前后快照一致（items=176、enriched=176、runs=4、clusters=64、max_id=176），全程只读 |
| 真实 LLM 花费 | 3 次真实小样本调用（每次 6 条，¥0.0065 / ¥0.0067 / ¥0.0067），**累计 ≈ ¥0.02**（预算 ≤ ¥0.2） |
| 密钥 | 证据与报告中无真实密钥；密钥扫描 13/13（M0）+ M2 证据/测试脚本扫描 0 命中；**全程未向企业微信发送任何消息** |

**两处 P1 均集中在「聚类」环节**，且相互叠加：

1. **D1 [P1] LLM 精修过度合并** —— 真实库（`llm_refine: true` 的默认配置）里 10 条互不相关的 OpenAI 新闻被并成 1 个「同一事件」簇；全库多条目簇**对级准确率 39.4%**，远低于验收要求的 ≥90%。
2. **D2 [P1] 跨轮 cluster_id 复用 + 簇表覆盖** —— 每轮 `enrich` 的簇编号都从 1 重新开始、`INSERT OR REPLACE` 覆盖 `clusters` 表，旧条目的 `cluster_id` 指向被覆盖后的新簇；真实库 17/64 个簇的 `mention_count` 与实际成员数不一致（如 cluster 1 `mention=1` 实际 4 条）。

其余 7 项验收全部通过，健壮性（60/60 坏数据注入）、幂等（二次运行零 LLM 调用）、成本核算与上限、排序可解释性、CLI 退出码语义、性能（真实 176 条 3.7s；400 条最坏 8.9s）、真实链路与成本折算（200 条/天 ≈ ¥0.22）均达标。

---

## 二、验收项结果

| # | 验收标准 | 执行方法（脚本/命令） | 实际结果 | 判定 | 证据 |
|---|---|---|---|---|---|
| 1 | 预筛正确性 | `m2_prefilter.py`：fixture 种子库 + `enrich --no-llm` + SQLite；真实库 176 条全量人工核验 | ① 门槛仅对 `gate_sources` 生效（hn 无关键词淘汰 / 非门控源保留）；② 招聘/课程/webinar/促销 4 条全部淘汰（含非门控源）；③ 批内 `(source_id,url_hash)` 重复去重 1 条；④ `gate_sources: []` 放行且垃圾过滤仍生效；⑤ 真实库淘汰 72 条（垃圾 6 + 与 AI 无关 66），与 CLI 路径**完全一致**；⑥ 人工核验误杀 **7 条（占淘汰 9.7%、占全库 4.0%）** → 登记 P2-1 | **PASS**（含 P2-1） | `evidence/m2/prefilter.json`、`prefilter/1h-real-prefilter-review.json`、`prefilter/1m-real-cli.txt` |
| 2 | 聚类正确性 | `m2_dedup.py`：合成 9 组用例（黑盒）+ 真实库规则层/生产库双审计 | 合成全部通过：同 URL 必合并（跨源/标题不同/时间差 30h）、URL 归一化（utm/尾斜杠）、相似度 ≥88、**Sol/Luna 不误合并**、包含关系、超 `max_hours_apart=72` 不合并、同 URL 超时间窗仍合并、三源 mention=3。真实规则层：104 条 → 100 簇、多条目簇 2（人工核验 2/2 合理）、跨簇高相似对 0。**真实生产库（LLM 精修）：对级准确率 39.4%（28/71）、簇级 3/5；17/64 簇自洽性被破坏** → D1/D2 | **FAIL** | `evidence/m2/dedup.json`、`dedup/2n-prod-cluster-verdicts.json`、`dedup/2m-prod-cluster-consistency.json`、`dedup/2p-refine-overmerge.txt` |
| 3 | LLM 加工健壮性（假 LLM 注入） | `m2_llm_robust.py`：15 类坏数据 × 4 项断言（不崩溃/落库完整/统计计入/降级或成功） | **60/60 通过**：正常 JSON、Markdown 围栏、截断 JSON、非法 JSON、空 items、index 缺失/重复/越界(0,99)、category 非法值、`is_ai:false`、HTTP 500（3 次退避重试）、choices 为空、**超时（61s 读超时 + 重试）**、连接拒绝 —— 全部无 traceback、rc ∈ {0,1}、全部条目 `enriched_at` 非空并降级为原文（category=其他 / importance=30）、统计行完整（批次/失败/token/成本） | **PASS** | `evidence/m2/llm-robust.json` + 每个场景 `<场景>.txt` |
| 4 | 缓存与幂等 | `m2_cache.py`：假 LLM 计数 + 队列/库前后对比 | 首轮 2 批调用 → 第二轮 **0 次 LLM 调用**且输出「没有待加工的条目」；12 条全部 `enriched_at` 非空（10 加工 + 2 过滤标记）；预筛淘汰条目已标记且不再入队；`--dry-run` 前后库完全一致（items/clusters 未变）且 dry-run 未消耗队列（随后真实运行仍加工 12 条） | **PASS** | `evidence/m2/cache.json`、`cache/4a~4g-*.txt` |
| 5 | 成本核算与上限 | `m2_cost.py`：假 usage 注入 | 2 批 × (100k in/50k out) → CLI 成本 **¥1.2000 = 独立复算 ¥1.2000**；限额 ¥0.3 时执行 2 批后停止（成本 ¥0.4）、输出「已达每日成本上限…部分条目降级为原文」、15 条全部落库（10 加工 + 5 降级）、`已写回: 15 条`；限额小于单批成本时仅首批执行且不丢数据 | **PASS** | `evidence/m2/cost.json`、`cost/5a~5c.txt` |
| 6 | 排序正确性 | `m2_rank.py`：假 LLM 控制 importance/category + 独立复算（文档公式） | 打印的 6 条 Top 分数**全部等于独立复算**（误差 <0.06）；分项可解释：importance 项差 38.5、跨源 mention=3 → 13.33、社区热度 log 归一 0.9446 → 14.17、源权重 1.2/1.0/0.8 → 10/8.33/6.67、兴趣加成 +8/+5；边界：importance 截断、源权重上限、`select_top(<=0)=[]`、同分时更新优先 | **PASS** | `evidence/m2/rank.json`、`rank/6b-expected-score.json` |
| 7 | CLI 行为 | `m2_cli.py` | `enrich --no-llm --dry-run` 免费可用（rc=0、0 次 LLM 调用、不写库、LLM 指向不可达端点仍成功）；`--limit/--top` 6 类非法值全部 **rc=2** 且无堆栈；`--limit 0/--top 0` 合法；**13 类 pipeline 越界配置全部 rc=3**（未回显密钥/无堆栈）；4 组合法边界值 rc=0；缺失 `pipeline` 段可用默认值 | **PASS** | `evidence/m2/cli.json`、`cli/7b-bad-args.json`、`cli/7c-bad-pipeline.json` |
| 8 | 性能与成本 | `m2_perf.py`（假 LLM，0.15s/次模拟延迟）+ `m2_real_llm.py`（真实小样本） | 真实 176 条全量 **3.7s**（LLM 8 次调用）；400 条最坏（每簇 1 条、27 批）**8.9s**；阈值 720s；`max_items` 截断保护生效；真实 DeepSeek 6 条样本 **¥0.0067** → 168+97 token/条 → **200 条/天 ¥0.2229、400 条/天 ¥0.4459**（< ¥1/天）；翻译摘要抽检 20 条 **19/20 通过（95%）**，唯一不通过项由 D1 导致 | **PASS** | `evidence/m2/perf.json`、`perf/8c-cost-extrapolation.json`、`real-llm.json`、`real-llm/real-llm.json`、`real-llm/sample-20.json` |
| 9 | 工具链与回归 | `m2_regression.py`：ruff + pytest + 重跑 M0（6）/M1（10+3）脚本 + 密钥扫描 + 真实库快照 | `ruff check .` **exit 0**；`pytest` **226 passed / 0 failed / 0 errors**（2.7s）；M0 六脚本对齐第 3 轮基线（105/105、13/13、13/13、5/6 已接受 `bad_alias`、4/4、10/10）；M1 全部通过（`m1_acceptance` 19/19 等）；`config.example.yaml` 开箱 rc=0；密钥脱敏 0 命中；真实库 0 变化 | **PASS** | `evidence/m2/regression.json`、`regression/ruff.txt`、`regression/pytest-numbers.json`、`regression/m0_*.txt`、`regression/m1/*.json` |

---

## 三、缺陷列表

### D1 [P1] LLM 聚类精修过度合并，真实全库聚类准确率 39.4%（验收要求 ≥90%）

- **最小复现（真实数据，只读）**：

  ```sql
  -- 真实库 data/aihot.db
  SELECT cluster_id, mention_count, canonical_title FROM clusters WHERE mention_count > 1;
  SELECT title FROM items WHERE cluster_id = 7 ORDER BY published_at;   -- 10 条互不相关的 OpenAI 新闻
  ```

  实测 cluster 7（`mention_count=10`，同一轮次写入）包含：GPT-6 Sol/Luna 发布（3 条）、Better prompt caching for GPT-6、GPT-6 Astra 破解 Enigma、ChatGPT 语音 agent、Harvey/invideo 的 GPT-6 案例、MentalHealthBench、GPT-6 开车 —— 至少 8 个不同事件。
- **受控复现（不依赖真实 LLM）**：`python tests/independent/m2_dedup.py` 的 `2p` —— 假 LLM 对 10 个完全不同的事件返回 `{"groups":[[1..10]]}`，产品直接合并为 1 簇、`mention_count=10`（`evidence/m2/dedup/2p-refine-overmerge.txt`）。
- **期望 vs 实际**：期望「同一事件才合并」（`prompts.CLUSTER_REFINE_SYSTEM` 的规则 1/2/3），且合并后准确率 ≥90%；实际 `Enricher.refine_clusters` 只做结构校验（`_valid_groups`：编号范围、不重复、全覆盖），对「这些簇是否真是同一事件」**零校验**，真实模型（deepseek-chat，单次输入 84 个簇）输出了按主题粗粒度归组的结果，产品照单全收。
- **影响范围（实测）**：
  - 聚类验收失败：生产库 5 个多条目簇中仅 3 个簇级合理，**对级精度 28/71 = 39.4%**；受影响的条目 24 条。
  - 加工结果串台：同簇成员共享代表条目的 `cn_title/cn_summary` —— 真实库 id=45「Better prompt caching for GPT-6」的 `cn_title` 被写成「OpenAI 发布 GPT-6 Sol 和 Luna」（翻译抽检 20 条中唯一不通过项，`evidence/m2/real-llm/sample-20.json`）。
  - 排序失真：`mention_count` 虚高使无关条目获得跨源加成（最多 +20 分）。
  - M3 日报将显示错误的「提及 N 条 / N 家媒体报道」并吞掉独立事件。
- **根因**：`src/aihot/pipeline/enrich.py::refine_clusters`（结构校验 `_valid_groups`）+ `prompts.CLUSTER_REFINE_SYSTEM`（要求过宽，无「必须共享稀有实体」约束）+ 单次投喂全部簇（最多 150 个，本轮真实运行为 72 个）。
- **建议修复（任一组合）**：① 对 LLM 返回的分组做规则复核，只接受「组内至少一对标题相似度 ≥ 阈值 或 共享稀有实体（模型名/公司名/数字）」的分组，否则退回规则簇；② 把 `llm_refine` 默认改为 false，或仅用于「同事件跨语言」候选（这是模块注释声明的本意）；③ 分块投喂（如每次 ≤30 簇）并收紧提示词（明确禁止按主题归并）；④ 合并后重新计算代表条目与 `mention_count`。
- **证据**：`evidence/m2/dedup.json`、`dedup/2n-prod-cluster-verdicts.json`（5 个多条目簇逐簇裁定）、`dedup/2p-refine-overmerge.txt`、`real-llm/sample-20.json`（id=45）

### D2 [P1] 跨轮 cluster_id 复用 + 簇表覆盖，导致 `clusters` 与 `items` 不一致

- **最小复现（受控，`m2_dedup.py` 的 `2o`）**：同一临时库先后执行 `enrich --limit 3` 与 `enrich --limit 5`（各自独立编号 1..N），随后

  ```sql
  SELECT c.cluster_id, c.mention_count,
         (SELECT COUNT(*) FROM items i WHERE i.cluster_id = c.cluster_id) AS actual
  FROM clusters c;
  ```

  实际：cluster 1/2/3 均出现 `mention_count=1` 但 `actual=2`（第一轮条目的 `cluster_id` 未清理，指向第二轮被覆盖的同号簇）。
- **真实库同现象（只读）**：**17/64 个簇**自洽性被破坏，例如 cluster 1 `mention=1` 实际 4 条、cluster 7 `mention=10` 实际 11 条（混入了第一轮残留条目 "VSCode's SSH Agent Is Bananas"）、cluster 6 `mention=1` 实际 3 条（`evidence/m2/dedup/2m-prod-cluster-consistency.json`）。
- **期望 vs 实际**：期望簇编号跨轮全局唯一（`store.next_cluster_id()` 正是为此实现），或每轮清理/重映射旧 `cluster_id`；实际 `dedup.cluster_items` 每轮从 1 编号，`cli._cmd_enrich` 用 `mark_enriched` 写入这些编号、`save_clusters` 以 `INSERT OR REPLACE` 覆盖 `clusters` 表，且 **`next_cluster_id()`（`src/aihot/store.py:327`）在全项目没有任何调用点**（`grep -rn "next_cluster_id" src/` 仅命中定义处）。
- **影响范围**：`clusters` 表只保留最后一轮的簇；历史簇丢失；`mention_count` 与成员不一致 → 任何 join `items.cluster_id = clusters.cluster_id` 的消费方（M3 日报「提及 N 条/来源 N 家」、`resend`、存档查询）都会报错数据；排序的跨源分在跨轮场景下同样不可信。
- **建议修复**：入队时用 `base = store.next_cluster_id() - 1` 偏移本轮编号（并在 `clusters` 记录 `run_id`/日期），或在 `enrich` 开始时清空上一轮 `items.cluster_id` 并重建；补一条跨轮一致性的单元测试。
- **证据**：`evidence/m2/dedup/2o-stale-repro.txt`、`2o-stale-issues.txt`、`2m-prod-cluster-consistency.json`

### P2（允许遗留、登记 backlog）

| 编号 | 标题 | 事实与影响 | 建议 |
|---|---|---|---|
| P2-1 | 预筛 AI 词表误杀 9.7%（真实数据） | 真实库淘汰 72 条中 7 条经人工核验属误杀：`Meta's Muse`×2、`LensVLM`（VLM 论文）、`Comma's hands-off driving tech`（自动驾驶）、`Show HN: Npunlock ... Intel NPUs`、`Tokens too cheap to meter`、`The Price of Intelligence Is Falling Rapidly`（后 3 条为边缘）。另发现英文词表复数/变体不全：`\bGPU\b` 不匹配 "GPUs"（实测 "Nvidia unveils Rubin GPUs" 被淘汰） | 扩词表：VLM、NPU、Muse、agentic、slop；统一 `(?:s|es)?` 复数形式；`gate_sources` 的 HN 可在适配器加 AI 查询条件 |
| P2-2 | 阈值 88 对「型号版本数字」无区分力 | `Meta 开源 Llama-4 系列` 与 `Llama-5 系列`（1 字符差异）被判同簇；Sol/Luna（3 字符差异）可正确区分（验收要求的场景通过） | 对「品牌+数字」做数字差异保护（数字不同则禁止合并） |
| P2-3 | `blacklist_keywords: []` 不能禁用内置黑名单 | 代码 `settings.blacklist_keywords or DEFAULT_BLACKLIST_PATTERNS`：空列表回退内置词表，只能「替换」不能「关闭」；与 `gate_sources: []`（禁用）语义不一致，配置校验提示「可为空列表」易误解 | 增加显式开关（如 `blacklist_enabled`）或文档明确说明 |
| P2-4 | `enrich` 不写 `runs` 表 | token/成本/错误只存在于 stdout；PLAN §1.5 将 `runs` 定义为「运行记录（耗时/条数/token/错误）」，M2 无法审计成本（缓存幂等性也无法从库内证明） | 在 enrich 结束时写 `RunRecord`（stage_stats 含 token/cost/批次/成本上限） |
| P2-5 | LLM 持续超时无整体时限 | 单批最坏 3 次 × 60s 读超时 = 180s；400 条全失败 ≈ 27 批 → 最坏 ~80 分钟，超出 12 分钟预算；因不消耗 token，成本上限不会兜底 | 连续 N 批失败后熔断，或给整体加工加 deadline |

### 观察项（不计缺陷）

- 成本上限在**批次前**检查，故最后一个批次可超出上限（最多一批成本）；本次验收按「停止后续批次」判定通过。
- 聚类阈值导致的合并（含 `title_similarity: 88`）在真实规则层非常保守：104 条仅 2 个多条目簇，跨簇高相似对（独立归一化 + rapidfuzz ≥78）为 0，即规则层精度高、召回靠 LLM 精修补足 —— 这也解释了 D1 的诱因。
- M1 回归首跑 `m1_check_net.py` 5/6：真实源 `hackernews` 单次 `ConnectTimeout`（10s），立即复跑 **6/6**（`evidence/m2/regression/m1-retry/check_net.json`），判定为网络抖动而非回归。
- M0 证据目录（`reports/evidence/{cli,logging,secret-scan,regression}`）被本轮 M0 重跑覆盖属既有约定；已按要求先快照到 `evidence/m2/regression/pre-m2-snapshot/`，M0 权威基线仍取 `regression/*_r3.json`。

---

## 四、实测数字汇总

| 指标 | 实测值 |
|---|---|
| `ruff check .` | **exit 0**（All checks passed） |
| `pytest` | **226 passed / 0 failed / 0 errors**（2.7s；M1 为 132，M2 新增 94） |
| 预筛（真实 176 条） | 保留 104 / 淘汰 72（垃圾 6 · 与 AI 无关 66 · 批内重复 0）；人工核验误杀 7（9.7% of 淘汰、4.0% of 全库） |
| 聚类（真实规则层 `--no-llm`） | 104 条 → 100 簇；多条目簇 2（核验 2/2 合理）；跨簇高相似对 0 |
| 聚类（真实生产库，LLM 精修） | 多条目簇 5；**对级准确率 39.4%（28/71）**；簇级 3/5；簇表不一致 **17/64** |
| LLM 坏数据注入 | **60/60**（15 场景 × 4 断言）；HTTP 500 重试 3 次；超时场景 61s 读超时后降级 |
| 幂等 | 第二轮 LLM 调用 **0**；队列 12/12 清空；dry-run 前后库 0 变化 |
| 成本核算 | 100k in + 50k out ×2 → ¥1.2000（与独立复算一致）；限额 ¥0.3 → 2 批后停止、`cost_limit_hit`、0 丢数据 |
| 排序 | 6/6 条 Top 分数与独立复算一致；4 类分项 + 兴趣加成逐一验证 |
| CLI | `--no-llm --dry-run` rc=0/0 调用；非法参数 6/6 → rc=2；越界配置 13/13 → rc=3；合法边界 4/4 → rc=0 |
| 性能 | 真实 176 条 **3.7s**；400 条最坏 **8.9s**（阈值 720s） |
| 真实成本 | 6 条样本 ¥0.0067 → **200 条/天 ¥0.2229**；400 条/天 ¥0.4459（阈值 ¥1/天） |
| 真实 LLM 累计花费 | ≈ **¥0.02**（3 次运行 × 6 条；预算 ¥0.2） |
| 翻译/摘要抽检 | 20 条 **19/20 通过**（95%；唯一失败由 D1 造成） |
| M0 回归 | 105/105、13/13、13/13、5/6（已接受 `bad_alias`）、4/4、10/10 |
| M1 回归 | acceptance 19/19、adversarial 11/11、url_edges 4/4、units 19/19、contracts 6/6、check_net 6/6、proxy_bad_port 4/4、socks5 4/4、slow_source 1/1、perf 6/6 |
| 真实库快照 | 测试前/后一致：items=176、enriched=176、runs=4、clusters=64、max_id=176（`run-all-summary.json` 的 `real_db_unchanged=true`） |

---

## 五、证据索引（`reports/evidence/m2/`）

| 证据 | 内容 |
|---|---|
| `run-all-summary.json` / `.txt` / `logs/` | 13 个脚本总览（含真实库前后快照与每个脚本原始输出） |
| `revision-fingerprint.json` / `revision-compare.txt` | 产品 24 文件指纹；测试后 0 变更 |
| `prefilter.json` + `prefilter/` | 验收 1（合成 + 真实全量核验 + 误杀清单 + CLI 交叉验证） |
| `dedup.json` + `dedup/` | 验收 2（合成 9 组、规则层真实核验、生产库审计、`2o`/`2p` 缺陷复现） |
| `llm-robust.json` + `llm-robust/` | 验收 3（15 场景 × 原始输出与落库明细） |
| `cache.json` + `cache/` | 验收 4（两轮调用计数、dry-run 前后对比） |
| `cost.json` + `cost/` | 验收 5（usage 复算、上限停止、不丢数据） |
| `rank.json` + `rank/6b-expected-score.json` | 验收 6（打印分数 vs 独立复算） |
| `cli.json` + `cli/` | 验收 7（参数矩阵、13 类坏配置、边界值） |
| `perf.json` + `perf/8a~8d` | 验收 8（真实 176 / 合成 400 / 成本折算 / 截断保护） |
| `real-llm.json`、`real-llm/real-llm.json`、`real-llm/sample-20.json`、`real-llm/real-llm-cli.txt` | 真实链路小样本、成本折算、翻译抽检 20 条 |
| `regression.json` + `regression/` | 验收 9（ruff/pytest 原文、M0 六脚本、M1 十脚本、密钥扫描、真实库快照、`pre-m2-snapshot/`） |

---

## 六、测试方法与可复现性

- **一键复现**：

  ```powershell
  cd D:\信息收集整理\aihot
  .venv\Scripts\python.exe tests\independent\m2_run_all.py        # 全量（含 M0/M1 回归，约 10 分钟）
  .venv\Scripts\python.exe tests\independent\m2_dedup.py          # 聚类专项（含 D1/D2 复现）
  .venv\Scripts\python.exe tests\independent\m2_llm_robust.py     # 坏数据注入（约 2 分钟，含 61s 超时用例）
  .venv\Scripts\python.exe tests\independent\m2_revision.py compare
  ```

- **隔离**：全部写操作在 `%TEMP%\aihot-m2-tests\...` 的临时配置/临时库；真实库仅以 `mode=ro` 打开；每个脚本都以真实库快照前后比对收尾。
- **假 LLM**：`tests/independent/m2_support.py::FakeLLM` —— 本地线程 HTTP 服务（`POST /chat/completions`），支持预设队列响应（状态码/围栏/截断 JSON/延迟/断连）与按请求动态生成响应，并统计请求次数用于「零调用」断言。
- **独立复算**：预筛淘汰集合、聚类成员关系、排序分数、成本公式均按规格文档独立实现后与产品输出比对，不引用产品内部函数。
- **不依赖开发自测结论**：`pytest` 226 用例只作为工具链门槛记录，不作为验收判定依据；所有判定来自上述独立脚本的 CLI/SQLite 观测。

## 七、安全与边界声明

- 全程**未向企业微信发送任何消息**（webhook 仅存在于真实配置，M2 的 `enrich` 无推送路径，且临时配置一律 `push.*.enabled=false`）。
- 真实密钥仅用于真实小样本调用（经环境变量传入，不落盘、不打印）；报告与证据中**不含密钥原文**；M2 证据/脚本密钥扫描 0 命中。
- 真实 `data/aihot.db` 全程只读，测试前/后快照一致（176 items / 4 runs / 64 clusters）。
- 测试期间**未修改任何产品代码**（指纹 24 文件、`sha256=5867c83e…`，测试后比对 0 变更）。
