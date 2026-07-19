# ETF 工具联网数据源改造方案

## 新约束

1. **权威数据源改为联网获取**：本地 JSON/CSV 只作为缓存、审计和离线兜底，不再作为决策权威来源。
2. **E大操作必须关联当天文章**：买卖记录只说明发生了什么；操作原因优先从 ETF拯救世界/E大操作当天文章内容中抽取，包括估值判断、仓位判断、风险提示和计划上下文。
3. **且慢和理杏仁直接代码获取**：常规更新应由服务或脚本直接调用网页/API，不依赖 agent 手工读取、复制、总结，以节省 token 并提升稳定性。

## 当前状态

- `scripts/sync_long_win.py` 已能联网从 `https://etf.maxmeng.top` 页面 chunk 提取 Supabase anon key，并从 `https://supabase.maxmeng.top/rest/v1` 拉取长赢 150 / S 定投计划和操作。
- `app/main.py` 目前仍直接读取：
  - `data/long_win_positions.json`
  - `data/signals.json`
  - `/vol1/1000/openzl/jztz/daily_outputs/matrix_snapshot_*.csv`
  - `/vol1/1000/openzl/jztz/market_data/*.csv`
  - `/vol1/1000/openzl/qieman_etf/topic整理/topic_index.json`

## 目标架构

### 1. 数据源层

新增 `app/data_sources/`：

- `long_win.py`
  - 复用 `scripts/sync_long_win.py` 的 Supabase 抓取逻辑。
  - 页面请求时优先联网拉取或读短 TTL 缓存。
  - 输出统一结构：plans、positions、actions。

- `ed_articles.py`
  - 按 E大操作 URL / 日期拉取对应文章。
  - 从文章正文提取：operation_reason、mentioned_indices、valuation_context、risk_note、position_context。
  - 将提取结果挂到 `ed_actions[].reason_detail`。

- `qieman.py`
  - 直接抓取且慢/长赢页面或 Supabase 数据。
  - 只把本地文件作为缓存和可追溯归档。

- `lixinger.py`
  - 直接调用理杏仁 API 获取指数估值、PE/PB 分位、ROE、股息率等。
  - 不再依赖 JZTZ 已生成 CSV 作为主路径；JZTZ 可继续作为展示/备份。

### 2. 缓存策略

- 缓存目录：`/vol1/1000/openzl/etf_tool/cache/`
- 每条缓存必须带：`source_url`、`fetched_at`、`data_date`、`ttl_seconds`。
- UI 明确显示 `data_date` 和 `fetched_at`。
- 若联网失败：提示数据源异常；可以显示缓存，但必须标记为“缓存/非权威实时”。

### 3. API 改造顺序

1. `/api/summary`：改为调用数据源层，不直接 `load_json()`。
2. `/api/sunburst/{plan_key}`：从联网长赢持仓生成。
3. `/api/compare` 和 `/api/calibration`：使用联网 E大操作 + 当天文章原因。
4. `/api/valuations`：接理杏仁实时估值。
5. `/api/topics`：保留本地知识库作为方法论检索，但不作为市场/操作数据权威源。

## E大当天文章原因字段

建议每条操作扩展：

```json
{
  "date": "YYYY-MM-DD",
  "action": "buy",
  "name": "...",
  "code": "...",
  "shares": 1,
  "url": "https://qieman.com/content/items/...",
  "operation_reason": "当天文章中可直接读出的操作原因",
  "reason_evidence": ["原文摘录1", "原文摘录2"],
  "valuation_context": "低估/高估/中性/无法判断",
  "position_context": "补仓/再平衡/网格/止盈/降低风险",
  "risk_note": "文章中的风险提示"
}
```

## 下一步实现建议

优先做 `app/data_sources/long_win.py`，把现有 `sync_long_win.py` 改成可被 FastAPI 直接调用的模块，并加 10-30 分钟 TTL 缓存。这样立刻满足“不依赖本地数据”的第一步，同时复用已经验证可用的联网抓取路径。
