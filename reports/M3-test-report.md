# M3 测试报告

- **测试时间**：2026-09-24（本机）
- **被测版本**：`aihot 0.1.0`（editable 安装）
- **被测范围**：M3 呈现 + 推送 —— `presentation/`（compose / render_text / render_image / templates）、`delivery/`（wecom / email）、`runner.py`、CLI 的 `run` / `preview` / `resend`、`store` 的日报存档、`config` 的 `push.email.attach_image`
- **测试环境**：Windows 11 + Python 3.12.10（项目 venv）；pytest 9.1.1；ruff 0.16.8；Playwright 1.63 + 系统 Edge（`msedge` 通道）；Windows 内置 OCR（`zh-Hans-CN`）
- **测试方式**：冷启动独立验证；**全假服务**——假 RSS、假 LLM（OpenAI 兼容，统计调用次数）、假企微 webhook（HTTP / HTTPS / HTTP CONNECT 代理 + TLS MITM）、假 SMTP（STARTTLS 与 SSL 两条路径，自签 CA）；真实库 `data/aihot.db` 与 `data/digests` 全程只读快照比对
- **安全约束执行**：**未向真实企业微信 webhook 发送任何消息、未向真实邮箱发送任何邮件**（真实 webhook key 在全部证据文件中 0 命中，见验收项 9）；未改动真实配置与真实库
- **真实 LLM 花费**：M3 全部套件使用假 LLM（¥0）；仅回归项 `m2_real_llm.py` 使用真实 DeepSeek（M2 既有脚本，¥0.01 量级，预算内）
- **结论**：**PASS（P0:0, P1:0, P2:5）** —— 9 项验收标准全部满足；5 项 P2 不阻断（2 项为极端/边缘路径，1 项为 CLI 输入健壮性，1 项为 dry-run 空库文件创建，1 项为 M2 旧断言契约漂移；均可绕过且有 resend 兜底），登记 backlog 供 M4/M5 处理

---

## 1. 验收项结果

| # | 验收标准 | 执行方法（脚本） | 实际结果 | 判定 | 证据 |
|---|---|---|---|---|---|
| 1 | 日报组装：Top N + 简讯切分正确；过滤/低分「其他」噪音排除；统计自洽 | `m3_compose.py`（纯函数夹具 + 临时库端到端） | **19/19**：`is_publishable` 6 例边界正确（过滤类/其他≤15 排除、其他>15 保留、空标题排除）；top_n=5/brief_count=3 切分与顺序正确；top_n 超界→全进 Top、brief_count=0、top_n=0 三种边界正确；临时库 18 行（12 正常/4 过滤/1 低分其他/1 高分其他）→ 日报 13 条（5+8），过滤与低分其他 0 泄漏；stats `scanned=14 kept=14 clusters=14 selected=5 briefs=8` 自洽 | ✅ PASS | `evidence/m3/m3_compose.json`、`m3_compose/1-compose-result.json` |
| 2 | 文字渲染：企微 markdown 含综述与全部条目链接；每条 ≤4096 字节（含极端长文）；邮件 HTML 结构正确（`attach_image:false` 无内嵌图） | `m3_render_text.py` | **23/23**：常规日报 1 片 3761 字节（含日期/综述/12 Top 链接/12 简讯链接/页脚统计）；极端长文（12×10KB 摘要）拆 16 片，最大 3857 字节 ≤4096，全部 URL 保留、截断带「…」；续片带「（续）」标题；邮件 HTML doctype/lang/head/body 完整，综述+全部 Top/简讯标题与链接命中，`image_cid=None` 时 `cid:`/`<img` 均 0 命中，传 `digest-image-1` 时 `<img src="cid:digest-image-1">` 命中；标题含 `<script>` 时正确转义（无注入） | ✅ PASS | `evidence/m3/m3_render_text.json`、`2-wecom-*.txt`、`2-email-*.html` |
| 3 | 长图渲染：JPEG、宽 1080px、≤2MB、中文无乱码；超长日报自动拆 2 张且各页完整 | `m3_render_image.py` + `m3_render_image_edges.py` + `m3_ocr_check.py` | **14/14 + 7/7**：常规日报 1 张 494,115 B（483 KB）/1080×3795；超长日报（50 条×200 字）在真实 2MB 阈值下**自动拆 2 张**（1,973,468 B + 1,861,017 B，高度 11,691+11,547 px，覆盖整页无静默截断）；拆图 HTML 上下页并集 = 50/50 条不重不漏、综述仅上页、简讯仅下页；Top3 红 `#e5484d` / 其余蓝 `#3370ff`（DOM 计算色 + JPEG 像素双证：red_px=8500, blue_px=38162）；中文无乱码三重验证——无 U+FFFD、`Microsoft YaHei` 字体实测可用（`measureText` 全角宽度 264px）、**Windows OCR(zh-Hans-CN) 对真实存档图标题 严格 22/24 + 放宽 24/24 命中、字符召回 1.000**（测试渲染图 24/24、召回 1.000） | ✅ PASS | `evidence/m3/m3_render_image.json`、`3-image-metrics.json`、`evidence/m3/m3_render_image_edges.json`、`evidence/m3/ocr/real-digest-2026-09-24.md`、`evidence/m3/ocr/test-normal-12top12brief.md` |
| 4 | 企微推送（假服务）：图片 base64+md5 正确；markdown 分片；errcode≠0/HTTP 502/连接失败 → NotifyError 不崩溃；条数不超 20/分钟 | `m3_wecom.py` + `m3_e2e.py`（HTTPS CONNECT 代理 MITM，官方域名前缀 + 本地代理） | **22/24 + 22/22**：base64 解码 == 文件字节、md5 == 文件 md5（合成字节与真实渲染 JPEG 双证）；端到端真实渲染 JPEG 519,783 B 图片消息 TLS 送达；先图后文顺序正确；markdown 与渲染分片逐字一致；errcode≠0 / HTTP 502 / 非 JSON / 连接被断 / 端口拒连 **5 类错误全部 → NotifyError 且不崩溃**；>2MB 图片、>4096B markdown、空 webhook、文件缺失 4 项客户端守卫全部拒绝；真实形态日报 1 图 + 1 文 = 2 条（≤20）；编排层推送失败仅记录错误、邮件照常、日报已存档 | ✅ PASS（2 项 P2 见 D2/D3） | `evidence/m3/m3_wecom.json`、`m3_e2e/e2e-metrics.json`、`m3_e2e/1-run1.txt` |
| 5 | 邮件推送（假 SMTP）：主题/收件人/HTML 正确；`attach_image:true` 时内嵌图 cid 匹配 | `m3_email.py` | **16/16**：STARTTLS(587) 与 SMTP_SSL(465) 双路径可用；主题 `AI 热点日报 · 2026-09-24`、From/To/多收件人正确；纯文本兜底 + HTML 正文；`attach_image:true` → `multipart/related` 内嵌 `image/jpeg`，Content-ID == `digest-image-1` == HTML 中 `cid:`，字节与文件完全一致（314,342 B）；`attach_image:false` → 无 `image/*` 部件；认证失败/收件人为空/连接失败 → NotifyError | ✅ PASS | `evidence/m3/m3_email.json` |
| 6 | 降级与恢复：图片渲染失败→纯文字推送不中断且内容不丢；推送失败→已存档→resend 补发且不重跑 LLM | `m3_degrade.py` | **16/16**：① 真实渲染失败（病态超长→RenderError）→ 无图、7 片纯文字推送、综述与全部 Top/简讯链接完整、邮件照常、md/html 落盘；② 注入渲染失败 → `run_all` status=partial、错误可见、文字结果不受影响；③ 企微 errcode 推送失败 → run rc=0/partial、邮件仍送达、三件套 + digests 行完整存档；④ `resend` 补发成功且 **LLM 调用数 4→4 不变**；⑤ 邮件失败不影响企微；⑥ LLM 全批次失败仍出日报并推送（原文降级、链接不丢） | ✅ PASS | `evidence/m3/m3_degrade.json`、`1-run-wecom-fail.txt`、`2-resend-after-fail.txt` |
| 7 | 幂等：同日重复 run 不重复推送；`--dry-run` 不发送但产出三件套 | `m3_idempotent.py` + `m3_e2e.py` | **11/11 + 22/22**：连续 3 次 run 推送数 2→2、邮件 1→1、LLM 3→3、条目 10→10、日报 1→1 全部不增长（runs 表按阶段留痕 run/fetch 各 +2）；dry-run 不发送、产出 jpg/md/html 三件套、不写 runs 行、重复执行仍不发送；fetch 重复执行 0 新增；`resend` 按「补发」语义每次发送（实测记录，非缺陷） | ✅ PASS | `evidence/m3/m3_idempotent.json` |
| 8 | CLI：`run`/`preview`/`resend` 行为与退出码；`--no-image`/`--top`/`--channel`；非法参数 rc=2 | `m3_cli.py` | **23/27**：help/version/check rc=0；`run --top -1/abc`、未知选项、未知子命令、`resend --channel bogus` → **rc=2 无 traceback**；配置缺失 rc=3；`preview`（有/无存档）rc=0 且展示三件套；`run --no-image` 纯文字无 jpg；`--top 3` 精选 3 条、`--top 0` 回退配置 5 条；`--config` 前置/后置与 `--debug` 均可用；`resend` 无存档 → rc=1 且提示明确；**`--date` 非法值 → traceback（P2，见 D1）** | ✅ PASS（4 项 P2 见 D1） | `evidence/m3/m3_cli.json`、`m3_cli/*.txt` |
| 9 | 工具链与回归：`ruff check .` 零告警；`pytest` 全绿（记录实际数字）；M0/M1/M2 脚本保持通过；密钥不泄漏 | `m3_toolchain.py` + `m3_run_all.py` | **10/10**：`ruff check .` **exit 0**；`pytest` **293 passed / 0 failed / 0 errors**，与开发者声明 293 一致；真实 LLM key / 企微 webhook key / 邮箱授权码在 14 个套件证据 + 日志中 **0 命中**（正向对照扫描器有效）；`config.example.yaml` 三密钥为空；真实库与 `data/digests` 快照前后一致（176/176/5/101/1 + 3 个存档文件 mtime 不变）。回归：M0 5 PASS + 1 已知 P2（`bad_alias`）、M1 12 PASS + 2 OBSERVE + 1 FAIL(P2已知 **D4**)、M2 11 PASS + 3 INFO + 4 FAIL(P2漂移 **D5**)、`m2_regression` 唯一失败为 D4 级联 —— **无新增 P0/P1** | ✅ PASS | `evidence/m3/m3_toolchain.json`、`9-ruff.txt`、`9-pytest.txt`、`9-secret-scan.json`、`run-all-summary.json` |

### 补充：用户反馈修复点独立复验（`m3_fixes.py`，15/15）

| # | 声明 | 独立复验结果 | 判定 |
|---|---|---|---|
| ① | 综述 JSON 包装污染已修复（非 JSON 模式 + `clean_summary` 兜底） | 6 类输入（`type+content`/`content`/`summary`/中文键/Markdown 围栏/纯文本）全部清洗为纯正文；端到端假 LLM 故意返回 `{"type":"json_object","content":"…"}` → 存档 md 与企微预览中**无 `"content"`/`json_object` 残留** | ✅ |
| ② | 序号配色 Top3 红 / 其余蓝 | 模板 CSS 规则 + `loop.index <= 3` 条件命中；DOM 计算色 3 红 9 蓝；JPEG 像素级红/蓝徽标均存在 | ✅ |
| ③ | 邮件改为纯文字（`attach_image:false`） | 真实配置 `push.email.attach_image == false`；渲染 HTML 无 `cid:`；编排层实发邮件无 `image/*` 部件 | ✅ |

---

## 2. 实测数字（关键指标）

| 指标 | 实测值 | 来源 |
|---|---|---|
| 常规日报长图 | 1080×3795 px，494,115 B（483 KB），质量阶梯未触发降质（无「体积超限」日志） | `3-image-metrics.json` |
| 真实 2MB 阈值下超长日报（50 条） | **自动拆 2 张**：1,973,468 B + 1,861,017 B；高度 11,691 + 11,547 px；总高 23,238 px ≥ 页面 22,497 px（无截断） | 同上 |
| 强制阈值拆图（确定性复现） | 阈值 2,971,423 B（介于半张 q88 2,735,111 与整张 q64 3,207,735 之间）→ 2 张 2,735,111 + 2,578,427 B | 同上 |
| 单张渲染耗时 | 常规 3.2–4.1 s；50 条超长（含拆图，2 张）10.3–12.3 s | 同上 |
| 企微 markdown 分片（真实形态日报） | 1 片 / 3702 字节（含综述 + 12 Top + 12 简讯链接） | `m3_e2e/e2e-metrics.json` |
| 企微 markdown 分片（极端 12×10KB 摘要） | 16 片，最大 3857 字节；极端含 400 字简讯标题时 21 片（见 D2） | `m3_wecom.json` |
| 企微单轮条数（真实形态） | 2 条（1 图 + 1 文） | `m3_wecom.json` 4.7 |
| 邮件内嵌图 | 314,342 B 与源文件逐字节一致，cid `digest-image-1` | `m3_email.json` 5.6 |
| 端到端 run（假 RSS 18 条 → 12 精选 + 6 简讯 → 图+文+邮件） | **7.2 s**（首次，含 Edge 启动、假 LLM 5 次调用、假企微/假 SMTP 送达、三件套存档）；推送图片 519,783 B | `m3_e2e/e2e-metrics.json`、`m3_e2e.json` 6.5 |
| 真实库状态（测试前后一致） | items 176 / enriched 176 / runs 5 / clusters 101 / digests 1；`data/digests/2026-09-24.{jpg,md,html}` 791,070/6,572/17,691 B mtime 不变 | 各套件快照 + `m3_toolchain.json` 9.9 |
| 真实存档长图规格（只读复核） | **1080×4731 px，791,070 B（772 KB）≤2MB**；OCR 24/24 标题命中、字符召回 1.000 | `m3_summary.py`、`evidence/m3/ocr/` |
| `pytest` | **293 passed / 0 failed / 0 errors** | `9-pytest.txt` |
| `ruff check .` | exit 0 | `9-ruff.txt` |
| M3 套件合计 | **14 个套件共 217 项检查：211 通过 / 6 失败**；6 项失败全部来自 3 个 P2 缺陷（D1 计 4 项、D2/D3 各 1 项）；另 D4 由 M1 回归脚本暴露（1 项） | `evidence/m3/m3_*.json` |

---

## 3. 缺陷列表

> 判定规则：PASS = P0 清零且 P1 清零。本轮 **P0:0 / P1:0 / P2:5**。

### D1 [P2] `preview/resend --date` 非法值触发 Python traceback（rc=1，非 rc=2）

- **最小复现**：
  ```
  .venv\Scripts\python.exe -m aihot preview --config <临时配置> --date not-a-date
  .venv\Scripts\python.exe -m aihot resend  --config <临时配置> --date 2026-13-45
  ```
- **期望 vs 实际**：期望与 `--top -1` 等非法参数一致 —— rc=2 + 可操作提示（`digest_date_from` 已在 `render_text` 中提供解析函数，仅缺少捕获）；实际 `date.fromisoformat` 抛 `ValueError` 未被捕获 → traceback + rc=1。
- **影响范围**：CLI 手工输入错误时的体验（无数据损坏、无密钥泄漏、不影响 `run` 与定时任务路径）；M0 已登记同类 P2（D2/D2-R），分级保持一致。
- **证据**：`evidence/m3/m3_cli.json` 8.3/8.4（4 项）、`m3_cli/` 对应输出。

### D2 [P2] 极端内容下企微单轮条数可超过 20 条/分钟限流

- **最小复现**：构造 12 条 Top（每条摘要 10KB，被截断至 3800 字节）+ 12 条 400 字标题简讯 → `render_wecom_parts` 产出 **21 片**，加图片消息共 **22 条**（>20）。
- **期望 vs 实际**：期望不超过企微 20 条/分钟；实际无客户端限流/合并逻辑，仅靠内容截断间接控制条数。
- **影响范围**：**真实管线不可达**——`enrich` 将 `cn_summary` 截断为 200 字、`cn_title` 亦远短于 400 字，且 markdown 仅取前 12 条 Top；真实形态日报实测 2 条（图 1 + 文 1），最坏现实情况约 6–9 条。属理论边界，建议 M4 增加条数上限守卫或分批节流。
- **证据**：`evidence/m3/m3_wecom.json` 4.8/4.9。

### D3 [P2] 图片消息发送失败会阻断同轮文字推送

- **最小复现**：假企微服务对 `msgtype=image` 返回 `errcode=93000`（其余消息正常）→ `deliver_digest` 在图片循环抛 `NotifyError`，**markdown 循环未执行**（实测文字请求 0 条），仅记录 1 条错误。
- **期望 vs 实际**：期望「先图后文」中图片失败时文字仍送达（内容不丢、链接可用）；实际该轮企微渠道 0 送达。注意：**渲染失败**（`RenderError`）路径已正确降级为纯文字（验收项 6 通过），本项仅限「图片消息发送失败」。
- **影响范围**：企微渠道单轮不可用，但错误可见、有告警、日报已存档，可 `resend --channel wecom` 补发；邮件渠道不受影响。建议 M4 将文字发送与图片发送解耦（图片失败仅告警）。
- **证据**：`evidence/m3/m3_wecom.json` 4.19/4.20；`m3_degrade.json` 6.10（resend 可恢复）。

### D4 [P2] `fetch --dry-run` 会创建空 `data/aihot.db`（M1 回归项：`m1_acceptance.py` 检查 6 失败）

- **最小复现**：全新 workspace（无 `data/aihot.db`）→ `aihot fetch --config <临时配置> --dry-run` → `data/aihot.db` **被创建**（49,152 B，schema 完整，`items`/`runs` 均为 0 行）；随后真实 `fetch` 正常落库 2 条 + 1 run。
- **期望 vs 实际**：CLI 自述「只抓取展示，不写库」、且 M1 验收脚本 `m1_acceptance.py` 检查 6 断言 dry-run 后 DB 文件**不存在**（该断言在 M1 最终轮 2026-09-23 为 PASS）；现在 `fetch_stage` 在 dry-run 时也会打开 `Store`（`with Store(cfg.app.data_dir / "aihot.db") as store`）→ 文件被创建，检查 6 变 FAIL。
- **影响范围**：无数据写入（0 行）、不影响幂等与推送；仅在「全新环境执行 dry-run」时多出一个空库文件，属行为契约漂移。建议：dry-run 时不打开 Store（或仅用只读连接），或明确接受该行为并同步更新 M1 脚本断言。
- **证据**：`evidence/m3/m3_dryrun_db.json` + `dryrun-db-probe.txt`；`evidence/m3/logs/m1_acceptance.py.txt`；历史对照 `evidence/m1/regression2/acceptance.json`（检查 6 = PASS）。

### D5 [P2] M2 回归脚本 6 项断言过时：`enrich` 阶段新增 1 次综述 LLM 调用（契约漂移）

- **现象**（run-all 中 M2 组 4 个脚本失败，均为同一根因）：
  - `m2_cache.py` 4a：分次加工 LLM 调用 3 次（期望 2 次）
  - `m2_cost.py` 5a：token 200100+100050、成本 ¥1.2006（期望 ¥1.2000，差 1 次综述调用）；5b/5c：批次/成本口径随综述调用偏移
  - `m2r2_d1.py` 1a：LLM 调用 2 次（期望 1 次）
  - `m2r2_p2.py` 4c：成本 ¥0.0066（脚本期望 ¥0.006）
- **根因**：M3 为实现日报综述（`PLAN.md` §1.3「全局综述」）在 `enrich_stage` 末尾新增 `enricher.summarize(...)`；M2 脚本按「`enrich` 只做逐条加工」的旧契约断言 LLM 调用数与成本。
- **产品侧复核（非缺陷）**：成本核算正确——1100×2/1e6 + 550×8/1e6 = **0.0066 == 记录值**；「默认不触发 LLM 精修」的意图检查仍通过（`refine_calls=0`，m2r2_d1 1a-runs PASS）；「无新条目时零 LLM 调用」仍成立（m2_cache 4b/4c PASS）；`run` 全链路综述只生成一次（不重复计费）。
- **影响范围**：仅旧测试断言；`aihot enrich` 单次多 1 次综述调用（¥0.002–0.01 量级）。建议 M4 更新 M2 断言口径，或把综述生成移到 `run_all`（保留 `enrich` 的独立契约）。
- **证据**：`evidence/m3/logs/m2_cache.py.txt`、`m2_cost.py.txt`、`m2r2_d1.py.txt`、`m2r2_p2.py.txt`；`evidence/m2/{cache,cost,d1,p2}.json`。

### 观测项（不构成缺陷，登记备查）

| # | 现象 | 说明 |
|---|---|---|
| O1 | `run --dry-run` 在「无待加工条目」时会补写一次综述（1 次 LLM 调用，¥0.001 量级） | 因 dry-run 重建日报时 DB 存档无综述字段；`resend`/`preview` 不触发（验收项 6 的「不重跑 LLM」仍成立） |
| O2 | `--dry-run` / `preview` 会覆盖当日 `data/digests/` 三件套与 `digests` 行 | 与「产出文件供预览」的设计一致；如需保留当日正式版可先备份 |
| O3 | 企微 markdown 只含前 12 条 Top（`MAX_WECOM_ITEMS`），`--top 50` 时第 13+ 条仅存在于长图与邮件/存档 | 设计内行为（消息体积控制）；如需全量可调常量 |
| O4 | WinRT OCR 对超大图（>1 万 px 高，如拆图后的 11,547 px 页）报参数错误 | OCR 工具限制，非渲染问题；该页内容完整性由 HTML 并集 50/50 + 像素墨迹验证 |
| O5 | `run --top` 无数值上限（config `app.top_n` 校验为 1~50） | CLI 可绕过配置上限；配合超长摘要会触发 D2/D3 边界 |
| O6 | 超长内容仍 >2MB（40 条×780 字，真实管线不可能产出）时抛 `RenderError` 降级为纯文字，不产出 3+ 张拆图 | 拆图固定上限 2 张；真实上限（50 条×200 字）已能拆 2 张且合规 |

---

## 4. 回归验证（M0/M1/M2）

**入口**：`tests/independent/m3_run_all.py`（顺序执行 M3 12 套件 → M0 6 → M1 14 → M2 16+3 INFO），汇总 `evidence/m3/run-all-summary.json` / `.txt`，逐脚本日志 `evidence/m3/logs/`。**真实库与 `data/digests` 前后快照一致**（`real_db_unchanged=true`）。

### 4.1 回归总览

| 组 | 脚本 | 结果 |
|---|---|---|
| M0 | 6 | **5 PASS + 1 已知 P2**（`m0_yaml_error_probe` 5/6：`bad_alias` 回显 alias 名 —— M0/M1/M2 已接受遗留） |
| M1 | 14 | **12 PASS + 2 OBSERVE**；1 FAIL(P2已知) = `m1_acceptance` 18/19（**D4**）；`m1_regression` 首次 8/9 因测试脚手架自身 ruff 问题，修复后复跑 **9/9 PASS** |
| M2 | 16 + 3 INFO | **11 PASS + 3 INFO**；4 FAIL(P2漂移) = `m2_cache` 7/8、`m2_cost` 6/9、`m2r2_d1` 14/15、`m2r2_p2` 18/19（均为 **D5** 同一根因）；`m2_regression` 24/25（唯一失败 9d 复跑 `m1_acceptance` → **D4 级联**） |
| 真实库 | — | `items=176 / enriched=176 / runs=5 / clusters=101 / digests=1`，`data/digests/2026-09-24.*` 三件套 mtime 与体积不变 |

### 4.2 M0（全部通过，除已接受 P2）

| 脚本 | 结果 | 证据 |
|---|---|---|
| m0_blackbox.py | **105/105** | `logs/m0_blackbox.py.txt` |
| m0_inprocess.py | 13/13 | `logs/m0_inprocess.py.txt` |
| m0_secret_scan.py | 13/13 | `logs/m0_secret_scan.py.txt` |
| m0_yaml_error_probe.py | 5/6（`bad_alias` = 已知 P2） | `logs/m0_yaml_error_probe.py.txt` |
| m0_issue_redaction_probe.py | 4/4 | `logs/m0_issue_redaction_probe.py.txt` |
| m0_round3_probe.py | 10/10 | `logs/m0_round3_probe.py.txt` |

### 4.3 M1（12 PASS + 2 OBSERVE + D4）

| 脚本 | 结果 | 脚本 | 结果 |
|---|---|---|---|
| m1_acceptance.py | 18/19 → **D4** | m1_proxy_bad_port_repro.py | PASS 4/4 |
| m1_regression.py | 首次 8/9（脚手架）→ 复跑 **9/9** | m1_socks_probe.py | PASS 4/4 |
| m1_adversarial.py | PASS 11/11 | m1_slow_source.py | PASS 1/1 |
| m1_url_edges.py | PASS 4/4 | m1_perf.py | PASS 6/6 |
| m1_net_store_units.py | PASS 19/19 | m1_revision.py / m1_real_db_state.py / m1_recon.py | OBSERVE（信息脚本，exit 0） |
| m1_contracts.py | PASS 6/6 | m1_check_net.py | PASS 6/6 |

### 4.4 M2（11 PASS + 3 INFO + D5 漂移）

| 脚本 | 结果 | 脚本 | 结果 |
|---|---|---|---|
| m2_prefilter.py | PASS 12/12 | m2r2_d1.py | 14/15 → **D5** |
| m2_dedup.py | PASS 16/16 | m2r2_accuracy.py | PASS 7/7 |
| m2_llm_robust.py | PASS 60/60 | m2r2_d2.py | PASS 16/16 |
| m2_cache.py | 7/8 → **D5** | m2r2_p2.py | 18/19 → **D5** |
| m2_cost.py | 6/9 → **D5** | m2r3_p2.py | PASS 10/10 |
| m2_rank.py | PASS 17/17 | m2r3_real_db.py | PASS 7/7 |
| m2_cli.py | PASS 26/26 | m2_regression.py | 24/25（9d = D4 级联） |
| m2_real_llm.py | PASS 7/7（真实 DeepSeek 小样本） | m2_translation_sample / m2_dbview / m2_dedup_probe | INFO |
| m2_perf.py | PASS 5/5 | | |

> 结论：**无新增 P0/P1**。D4 为 M3 期间引入的行为回归（dry-run 空库文件，P2）；D5 为 M3 综述功能导致的旧断言漂移（P2）；`m0_yaml_error_probe` 的 `bad_alias` 为 M0 已接受遗留。M0/M1/M2 的功能契约（幂等、缓存零调用、成本核算、真实库只读）在独立复验下均成立。

---

## 5. 未覆盖 / 限制说明

1. **真实外发未复验**：按硬约束，未向真实企业微信 webhook 与真实邮箱发送任何消息。开发者声明的「真机推送成功（1 长图 + 2 markdown）」「真实邮件成功（纯文字）」未由本 Agent 复现，但本报告已用本地假服务在**同一条代码路径**（含 HTTPS/TLS 与 SMTP 认证）验证其全部行为与错误分支。
2. **真实 LLM**：M3 渲染/推送链路全部使用假 LLM；真实 DeepSeek 仅在 M2 回归脚本 `m2_real_llm.py` 中小样本调用（既有脚本，预算内）。
3. **人工观感**（图片美观度、排版清晰度、手机端链接可点性）属 `PLAN.md` §5.6 的用户人工验收点，不在自动化判定范围；本报告提供 OCR 可读性与配色像素证据供参考。
4. **HTTPS 拦截说明**：CLI 级企微测试使用 `HTTPS_PROXY=127.0.0.1:<假代理>` + 自签 CA 的 CONNECT MITM，请求的「官方域名前缀」仅用于通过配置校验，流量全部落在本机假服务（`m3_support.py::FakeWeCom(proxy=True)`），未出网。

---

## 6. 证据索引

```
reports/evidence/m3/
├── m3_infra.json / m3_compose.json / m3_render_text.json / m3_render_image.json
├── m3_render_image_edges.json / m3_wecom.json / m3_email.json / m3_e2e.json
├── m3_degrade.json / m3_idempotent.json / m3_cli.json / m3_toolchain.json
├── m3_fixes.json / m3_dryrun_db.json（用户反馈复验 / dry-run 空库复现）
├── m3_infra_probe/ …（各套件目录：命令原文、HTML、指标 JSON）
├── ocr/real-digest-2026-09-24.md（真实存档图 OCR：严格 22/24、放宽 24/24、召回 1.000）
├── ocr/test-normal-12top12brief.md（测试渲染图 OCR 24/24、召回 1.000）
├── run-all-summary.json / run-all-summary.txt / run-all-console.txt
└── logs/（M3 + M0/M1/M2 全部脚本原始输出；含 m1_regression_rerun2.txt）

tests/independent/（本轮新增）
├── m3_support.py          # 假 LLM / 假 RSS / 假企微（HTTP/HTTPS/CONNECT 代理）/ 假 SMTP / 夹具 / 证据
├── m3_infra_probe.py      # 脚手架自检（9/9）
├── m3_compose.py m3_render_text.py m3_render_image.py m3_render_image_edges.py
├── m3_wecom.py m3_email.py m3_e2e.py m3_degrade.py m3_idempotent.py m3_cli.py
├── m3_toolchain.py m3_fixes.py m3_dryrun_db.py
├── m3_ocr_check.py m3_ocr.ps1（Windows OCR 中文可读性验证）
├── m3_summary.py          # 报告数字汇总
└── m3_run_all.py          # 全量入口（M3 14 套件 + M0/M1/M2 回归；--reclassify 重算判定）
```
