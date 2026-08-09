# Three Journals RSS Tracker

RSS-first、DOI 去重的 `Nature`、`Science`、`Cell` 主刊追踪系统。GitHub Actions 负责确定性的发现、历史状态与批次冻结；ChatGPT 后续负责资料富化、优先级判断、中国团队识别和中文日报。


## 公开仓库与数据边界

本仓库公开用于运行 GitHub Actions，并提供机器可读的三刊追踪状态和正式批次。

- 仓库中不持久化 API token、访问凭据或个人认证信息。
- `CROSSREF_MAILTO` 和 `EUROPE_PMC_EMAIL` 等运行参数应通过 GitHub Actions Secrets 提供；如使用 Variables，其值不应包含敏感信息。
- `data/` 和 `public/` 中保存生成的文献元数据、来源状态、去重状态、队列状态和正式批次，因此这些内容均视为公开数据。
- 部分记录包含来自官方 RSS、Crossref 或 Europe PMC 的摘要或摘要性文本，用于证据富化和日报筛选；这些第三方内容的版权与许可仍由原作者、出版商或相应数据提供方决定，本仓库不主张对其拥有额外权利。
- `runs/` 属于一次性运行诊断，不纳入版本控制。
- 私有开发阶段的一次性 probe 文件和原始诊断日志仅保留在 private archive，不进入公开生产仓库。
- 不应把个人笔记、非公开研究判断、凭据或其他敏感内容写入 `data/`、`public/` 或工作流文件。

## 发现源与降级策略

- 每个期刊始终先请求官方 RSS。
- Nature RSS 当前可从 GitHub Actions 直接访问。
- Science 与 Cell RSS 若返回 HTTP 403 或解析失败，自动使用 Crossref journal works API 按 ISSN 回退。
- 回退首次启用时只建立近 60 天基线，标记为 `bootstrap_seed`，不会冒充当日新增。
- 后续回退按 Crossref `from-created-date` 增量同步，并保留 48 小时重叠窗口；DOI 历史负责去重。
- 使用 Crossref 回退的运行标记为 `degraded_fallback_sources`，日报批次标记为 `degraded_sources`。
- Crossref 覆盖弱于官方 RSS，尤其可能遗漏无 DOI 的新闻、社论和前置内容，因此不会被表述为 RSS 全量成功。
- 可通过 Actions 环境变量 `CROSSREF_MAILTO` 进入 Crossref polite pool；未配置时仍使用公开 API。

## 已实现

- 三刊官方 RSS 抓取，单源独立失败披露和 Crossref 回退
- DOI 提取、标准化和历史去重
- 无 DOI 条目的临时键与待补队列
- 首次发现时间、上次未见时间和出现时间窗口
- Bootstrap 初始化，不把当前存量冒充为正式新增
- RSS/回退更新时间观测与按期刊、小时、星期统计
- 跨日深度解读队列
- 每天 10:50 后冻结结构化批次，供 15:15 ChatGPT 日报读取
- 全部来源失败时不生成“零新增”结论
- 幂等批次生成和自动测试
- 显式 UTC cron、上午发现与冻结冗余触发
- discovery 完成后的缺失批次补偿和延迟补录重建
- 调度理论时间、实际触发时间和延迟分钟数记录

## 调度

GitHub Actions 文件全部使用显式 UTC cron；下列均为北京时间。

发现任务：

- 06:30：清晨发现
- 08:47：主要截止前发现
- 09:47：截止前备份
- 10:37：最后一次截止前发现
- 16:30：日报交付后的下午刷新
- 20:30：晚间刷新

冻结任务：

- 每次 `RSS discovery` 成功完成后触发一次 `workflow_run` 冻结或补偿检查，这是主要冻结路径
- 10:50：固定主冻结
- 11:20：固定最终备份

富化任务：

- 07:45：Europe PMC 完整性审计
- 08:15：Crossref 空摘要 D+3、D+7、D+14 延后富化重试

ChatGPT 日报在 15:15 运行。11:20 至 16:30 之间不安排常规 GitHub cron，形成日报读取的安静窗口，减少 Actions 延迟、并发写入和日报读取正式批次之间的竞态。

每次 `RSS discovery` 完成后还会触发一次补偿检查：若当天批次在截止后仍不存在，则直接创建；若批次已经存在，但该 discovery 本应在 10:50 前运行、实际却被 GitHub 延迟到截止后，并发现了新内容，则自动重建批次并把这些记录标记为 `late_discovery_recovery`。

## 调度健康与延迟补录

- `scheduled_for`：理论触发时间，北京时间。
- `triggered_at`：实际进入脚本的时间。
- `scheduler_delay_minutes`：实际相对理论时间的延迟。
- 延迟超过 15 分钟时标记 `scheduler_delayed`。
- 延迟的截止前 discovery 所发现的内容保存 `intended_batch_date`，可通过补偿冻结纳入原定日报。
- 批次保留来源状态 `status`，另以 `flags` 和 `scheduler_status` 披露调度异常，避免把调度延迟误写成来源失败。

## 第一次运行

Actions 页面选择 `RSS discovery`。系统已有 Nature 基线时可选择 `auto`；Science 和 Cell 尚无历史时，Crossref 回退会自动按期刊分别建立基线，不会把近 60 天文章加入正式日报。

本地运行：

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt pytest
pytest -q
python scripts/discover_v2.py --mode auto
python scripts/build_daily_batch.py
```

## 关键文件

- `data/doi_index.json`：DOI 当前状态索引
- `data/discovery_events.jsonl`：只追加的首次发现事件
- `data/feed_observations.jsonl`：每次 RSS/回退观测
- `data/scheduler_events.jsonl`：每次调度的理论时间、实际时间和结果
- `data/deep_analysis_queue.json`：跨日深度解读队列
- `public/feed_statistics.json`：更新节律统计
- `public/scheduler_health.json`：调度延迟汇总与各工作流最新事件
- `public/latest_run.json`：最近一次发现运行
- `public/latest_batch.json`：ChatGPT 每日优先读取入口
- `public/batches/YYYY-MM-DD.json`：冻结后的历史批次

## 深度解读策略

所有新增文章均进入当天全量清单。深度解读是跨日队列：正常目标 15 篇，硬上限 20 篇；高峰日保留 P0/P1，当天无法完成的文章延后；零新增或低新增日优先消化积压。

ChatGPT 或人工完成优先级判断后，可生成决策文件并运行：

```bash
python scripts/queue_update.py decisions.json
```

## 状态语义

发现运行：

- `success_new_items`
- `success_zero_new`
- `success_seeded_items`
- `degraded_fallback_sources`
- `partial_success`
- `failed_all_sources`

日报批次：

- `success_new_items`
- `success_zero_new`
- `degraded_sources`
- `partial_sources`
- `blocked_failed_sources`

调度和补录标志：

- `scheduler_delayed`
- `late_discovery_recovery`

`failed_all_sources` 绝不能解释为零新增；`degraded_fallback_sources` 也不能描述成官方 RSS 全量成功。

## ChatGPT 15:15 日报

任务读取仓库中的 `public/latest_batch.json`，将全部新文章纳入全量清单，同时按队列动态完成当日或历史积压的深度解读。若批次状态为 `degraded_sources`，日报顶部必须披露具体使用了哪些回退来源；若存在 `scheduler_delayed` 或 `late_discovery_recovery`，也必须披露调度异常和补录数量。
