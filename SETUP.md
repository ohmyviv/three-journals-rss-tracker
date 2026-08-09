# GitHub 运行与验证

仓库已部署完成，不再需要 `Bootstrap repository` 工作流。


## 公开仓库安全边界

- 不要把 token、密码、私有 API key 或个人认证信息直接写入仓库文件。
- `CROSSREF_MAILTO` 和 `EUROPE_PMC_EMAIL` 建议配置为 **Settings → Secrets and variables → Actions → Secrets**。
- 如果使用 Actions Variables，只应存放可以公开的非敏感值。
- `runs/` 是运行时诊断目录，不纳入 Git 版本控制；公开仓库只持久化 `data/` 和 `public/` 中需要连续保存的状态和结构化结果。

## 首次与日常运行

1. 在 Actions 中选择 `RSS discovery`。
2. 正常手动检查选择 `auto` 或 `live`；仅在整个数据库为空时选择 `bootstrap`。
3. 工作流实际运行 `scripts/discover_v2.py`。
4. 查看 `public/latest_run.json`、`public/health.json` 和 `public/feed_statistics.json`。
5. 在 Actions 中运行 `Freeze daily batch`，检查 `public/latest_batch.json`。

## 来源状态

- `success`：官方 RSS 成功。
- `not_modified`：官方 RSS 返回 304，内容无变化。
- `fallback_crossref`：官方 RSS 失败，Crossref DOI 回退成功。
- `failed`：RSS 与配置的回退均失败。

发现运行若使用至少一个 Crossref 回退，状态为 `degraded_fallback_sources`；日报批次对应为 `degraded_sources`。这不是故障，但必须披露覆盖限制。

Crossref 回退不等于 RSS 全量覆盖。系统会记录：

- 原始 RSS 状态、HTTP 状态和错误；
- Crossref 查询模式与起始时间；
- 请求页数、尝试次数和耗时；
- 回退首次建立的是基线还是正常增量。

## 可选 Crossref polite pool

可在仓库 **Settings → Secrets and variables → Actions** 中设置，公开仓库建议优先使用 Secret：

```text
CROSSREF_MAILTO
```

未配置时仍可使用 Crossref 公开 API；配置后请求会加入 Crossref polite pool。

## 推荐验证顺序

1. 手动运行 `RSS discovery`，模式选 `auto`。
2. 确认 Nature 为 `success` 或 `not_modified`。
3. 若 Science、Cell 官方 RSS 仍返回 403，应看到 `fallback_crossref`，而不是 `failed`。
4. 首次回退应把 Science、Cell 近 60 天记录标成 `bootstrap_seed`，深度解读队列不应突然增加大量旧文章。
5. 再运行一次 `auto`，确认回退改用 `from-created-date` 增量窗口且 DOI 不重复。
6. 运行 `Freeze daily batch`，确认批次状态为 `degraded_sources`，并保留全部真正的 `live_discovery` 项目。
