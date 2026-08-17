# Three Journals RSS Tracker

RSS-first、DOI 去重的 `Nature`、`Science`、`Cell` 主刊追踪系统。GitHub Actions 负责确定性的发现、历史状态与批次冻结；ChatGPT 后续负责资料富化、重点解读资格判断、中国团队识别、中文日报和深度解读状态写回。


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
- 显式判定后才进入的跨日深度解读队列
- 当日及历史积压重点解读完成后的 `completed` 状态写回
- 每天 12:17 后冻结结构化批次，供 15:15 ChatGPT 日报读取
- 全部来源失败时不生成“零新增”结论
- 幂等批次生成和自动测试
- 显式 UTC cron、上午发现与冻结冗余触发
- discovery 完成后的缺失批次补偿和延迟补录重建
- 调度理论时间、实际触发时间和延迟分钟数记录

## 调度

GitHub Actions 文件全部使用显式 UTC cron；下列均为北京时间。计划分钟主动避开整点、整半点和常见刻钟，以降低与平台高负载窗口重叠的概率；这只是降低延迟风险的调度策略，不视为 GitHub Actions 的准点 SLA。

发现任务：

- 06:23：清晨发现
- 09:07：主要上午发现
- 10:23：截止前备份
- 11:07：最后一次计划内截止前发现
- 16:37：日报交付后的下午刷新
- 20:43：晚间刷新

冻结任务：

- 每次 `RSS discovery` 成功完成后触发一次 `workflow_run` 冻结或补偿检查，这是主要冻结路径
- 12:31：固定截止后第一保险
- 13:11：固定最终备份

富化任务：

- 07:41：Europe PMC 完整性审计
- 08:13：Crossref 空摘要 D+3、D+7、D+14 延后富化重试

正式批次 cutoff 为 12:17，北京时间 15:15 运行 ChatGPT 日报。计划层面 13:11 至 16:37 之间不安排常规 GitHub cron，形成日报读取的安静窗口，减少 Actions 延迟、并发写入和日报读取正式批次之间的竞态。

每次 `RSS discovery` 完成后还会触发一次补偿检查：若当天批次在截止后仍不存在，则直接创建；若批次已经存在，但该 discovery 本应在 12:17 前运行、实际却被 GitHub 延迟到截止后，并发现了新内容，则自动重建批次并把这些记录标记为 `late_discovery_recovery`。

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

- `data/doi_index.json`：DOI 当前状态索引，同时保存每个正式新 DOI 的持久化重点解读 disposition
- `data/discovery_events.jsonl`：只追加的首次发现事件
- `data/feed_observations.jsonl`：每次 RSS/回退观测
- `data/scheduler_events.jsonl`：每次调度的理论时间、实际时间和结果
- `data/deep_analysis_queue.json`：仅保存真正待后续重点解读的活跃项目，以及已完成项目的审计状态；`completed` 不计入 backlog
- `data/deep_analysis_history.jsonl`：重点解读 disposition 和队列状态变更历史
- `public/feed_statistics.json`：更新节律统计
- `public/scheduler_health.json`：调度延迟汇总与各工作流最新事件
- `public/latest_run.json`：最近一次发现运行
- `public/latest_batch.json`：ChatGPT 每日优先读取入口
- `public/batches/YYYY-MM-DD.json`：冻结后的历史批次

## 深度解读策略

所有正式新增文章仍无条件进入当天“今日全部新增”清单，但 discovery **不再自动把所有新 DOI 写入 `deep_analysis_queue.json`**。日报必须对当天正式批次中的每一个新 DOI 做一次明确、可持久化的重点解读判断：

- `completed`：本次日报已经完成重点解读。保留完成记录，但 `analysis_status=completed`，以后不再进入活跃 backlog。
- `queued`：文章确实值得重点解读，但本次因容量或证据尚未富化而未完成。只有这类项目进入活跃 backlog；可使用 `pending` 或 `deferred`。
- `not_selected`：经过主题相关性、文章类型、证据和投资价值判断后，不需要重点解读。这类 DOI 仍保留在 `doi_index.json` 和正式批次中，但不写入 `deep_analysis_queue.json`。

对 Crossref-only、摘要为空但主题高度相关、值得等待 D+3/D+7/D+14 富化的项目，应判为 `queued` 并使用类似 `awaiting_enrichment` 的 queue reason；如果即使未来取得摘要也不值得重点解读，则直接判为 `not_selected`。

当日报处理历史积压时，实际完成重点解读的历史 DOI 也必须写回 `completed`。对已经实际检查并明确判定以后也无需重点解读的历史积压，可判为 `not_selected` 并从活跃队列移除；不得批量删除尚未实际审阅的积压项目。

决策文件示例：

```json
{
  "batch_id": "daily-2026-08-11",
  "decisions": [
    {
      "doi": "10.xxxx/example-a",
      "disposition": "completed",
      "reason": "deep analyzed in today's report",
      "priority_level": "P0"
    },
    {
      "doi": "10.xxxx/example-b",
      "disposition": "queued",
      "reason": "high relevance but awaiting evidence enrichment",
      "priority_level": "P1",
      "analysis_status": "deferred",
      "queue_reason": ["awaiting_enrichment"]
    },
    {
      "doi": "10.xxxx/example-c",
      "disposition": "not_selected",
      "reason": "excluded article type and low investment relevance"
    }
  ]
}
```

应用并写回：

```bash
python scripts/apply_daily_analysis_decisions.py decisions.json
```

该命令强制要求正式批次中的**每一个新 DOI**都出现且只出现一次；遗漏任何当天正式新增 DOI 会直接报错。它同时允许附加本次实际处理过的历史 backlog DOI，并把所有变更追加到 `data/deep_analysis_history.jsonl`。旧的 `scripts/queue_update.py` 仍可用于对既有队列项目做人工状态修正，但不再承担每日全批次 disposition 闭环。

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

重点解读 disposition：

- `completed`
- `queued`
- `not_selected`

`failed_all_sources` 绝不能解释为零新增；`degraded_fallback_sources` 也不能描述成官方 RSS 全量成功。

## ChatGPT 15:15 日报

任务读取仓库中的 `public/latest_batch.json`，将全部新文章纳入全量清单，同时按证据、主题相关性、文章类型和投资价值完成当日与历史积压的重点解读。日报生成过程中必须为当天所有正式新 DOI 形成 `completed`、`queued` 或 `not_selected` 三选一的显式 disposition，并把本次实际完成解读的历史 backlog 写回 `completed`。

状态写回完成后必须重新读取 `data/doi_index.json` 和 `data/deep_analysis_queue.json` 做一致性检查：当天所有正式新 DOI 都应存在 disposition；所有本次已解读 DOI 都应为 `completed`；`not_selected` 不得留在活跃队列；`completed` 不得再次计入 backlog。若写回或回读失败，日报必须明确披露状态写回异常，不得虚报成功。

若批次状态为 `degraded_sources`，日报顶部必须披露具体使用了哪些回退来源；若存在 `scheduler_delayed` 或 `late_discovery_recovery`，也必须披露调度异常和补录数量。
