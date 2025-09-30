# currency_leverage_collection

跨交易所（Binance / Bybit / MEXC / WEEX）风险限额（Risk Limit / Leverage Brackets）采集、整合与制表项目。

- 自动获取目标币种（SURF 列表，仅 USDT 计价）。
- 并行抓取四家交易所的风险限额/档位信息（包含 Selenium 解析 WEEX 动态页面）。
- 统一筛选成“目标 USDT 交易对”的精选结果 `<exchange>_selected.json`。
- 生成多 Sheet Excel（每个币种一个 Sheet），列示“最大杠杆、最大持仓(USDT)、维持保证金率”。

---

## 1) 目录结构与角色

- `currencyGet_surf/`
  - `fetch_symbols.py`
    - 获取 SURF 支持币种集合，输出 `data/currency_kinds/surf_pairs.json`（只保留 `quote == USDT`）。
    - 默认“HTTP模式”，不会打开浏览器。若 `settings.SURF_USE_BROWSER=True` 则启用 Selenium/Edge 抓取。
- `dataGet/`
  - `binance_brackets_fetch.py`
    - 抓取全量风险分层 → 过滤出目标交易对 → `data/dataGet_api/binance/binance_selected.json`
  - `bybit_brackets_fetch.py`
    - 依据目标交易对并发请求 → `data/dataGet_api/bybit/bybit_selected.json`
  - `mexc_brackets_fetch.py`
    - 抓取 detailV2 和全量 ticker → 选优/换算 → `data/dataGet_api/mexc/mexc_selected.json`
  - `weex_brackets_fetch.py`
    - Selenium 多实例并发解析风险限额表格（`ul.list-settle`）→ `data/dataGet_api/weex/weex_selected.json`
  - `dataGet_main.py`
    - 并行启动四家抓取脚本，一键运行；日志写入 `data/dataGet_api/_logs/`
  - `probe/*.py`
    - CDP 网络探针脚本（诊断工具）
  - `utils/`
    - `multithread_utils.py`：线程池与进度条
    - `retry_utils.py`：多种重试装饰器
- `tableMake/`
  - `tableMake.py`
    - 读取四家 `*_selected.json`，按 SURF 目标币种生成 Excel 至 `result/Leverage&Margin_<timestamp>.xlsx`
- `data/`
  - `currency_kinds/surf_pairs.json`：SURF 获取的目标币种集合（base/quote）
  - `dataGet_api/<exchange>/...`：各交易所原始与精选结果
- `result/`
  - `Leverage&Margin_<timestamp>.xlsx`：最终制表结果
  - `_logs/`：整链路运行日志
- `main.py`
  - 项目一键主入口（位于 `currency_leverage_collection/` 根目录）

---

## 2) 抓取目标 URL 与字段

- Binance
  - 目标端点（Web BAPI）：
    - `https://www.binance.com/bapi/futures/v1/friendly/future/common/brackets`
  - 关键字段（示例）：
    - `symbol`，`riskBrackets[*].maxOpenPosLeverage`（最大杠杆）、`riskBrackets[*].bracketNotionalCap`（最大名义持仓）、`riskBrackets[*].bracketMaintenanceMarginRate`（维持保证金率）
  - 过滤策略：仅保留无后缀的 `BASEUSDT`（忽略 `BTCUSDT_250328` 等带后缀合约）

- Bybit
  - 目标端点（站内 API）：
    - `https://www.bybitglobal.com/x-api/contract/v5/public/support/symbol-risk?symbol=<SYMBOL>`
  - 关键字段（示例）：
    - `maximumLever`（最大杠杆）、`storingLocationValue`（名义上限）、`maintenanceMarginRate`（维持保证金率）

- MEXC
  - 目标端点：
    - 详情包：`https://futures.mexc.com/api/v1/contract/detailV2?client=web`
    - 全量 ticker：`https://futures.mexc.com/api/v1/contract/ticker?`
  - 关键字段（示例）：
    - `mlev`（最大杠杆）、`notional_usdt`（名义上限，经由张数*面值*价格计算得出）、`mmr`（维持保证金率）

- WEEX
  - 风险限额说明页（动态渲染）：
    - `https://www.weex.com/zh-CN/futures/introduction/risk-limit?code=cmt_<base>usdt`
      - 例如 `ARB` → `...risk-limit?code=cmt_arbusdt`
  - DOM 选择器与字段：
    - `ul.list-settle > li > span`，按列取 `lv / range / mlev / mmr`
    - `range` 为区间字符串，取上界作为“最大持仓(USDT)”

---

## 3) 数据流与输出

1. 获取 SURF 目标
   - `currencyGet_surf/fetch_symbols.py` 产出 `data/currency_kinds/surf_pairs.json`
2. 并行抓四家交易所
   - `dataGet/dataGet_main.py` 调度四个脚本（见上）
   - 精选结果写入：
     - Binance: `data/dataGet_api/binance/binance_selected.json`
     - Bybit: `data/dataGet_api/bybit/bybit_selected.json`
     - MEXC: `data/dataGet_api/mexc/mexc_selected.json`
     - WEEX: `data/dataGet_api/weex/weex_selected.json`
3. 制表
   - `tableMake/tableMake.py` 读取上述四个精选结果，按币种生成 Excel：
     - 列：`最大杠杆`、`最大持仓 (USDT)`、`维持保证金率`
     - 交易所块顺序：BINANCE → WEEX → MECX → BYBIT（与样表贴近）
   - `result/Leverage&Margin_<timestamp>.xlsx`

---

## 4) 运行

- 安装依赖：
```bash
pip install -r requirements.txt
```

- 一键运行（推荐）：
```bash
python main.py
```
流程：获取 SURF 币种 → 并行抓四所数据 → 生成 Excel（输出至 `result/`）。

- 分步运行（调试）：
```bash
python currencyGet_surf/fetch_symbols.py
python dataGet/dataGet_main.py
python tableMake/tableMake.py
```

---

## 5) 制表口径映射（与样表对齐）

- 最大杠杆：
  - Binance `maxOpenPosLeverage`
  - Bybit `maximumLever`
  - MEXC `mlev`
  - WEEX `mlev`
  - 展示保留小数精度，统一后缀 `X`（如 `12.5X`）

- 最大持仓 (USDT)：
  - Binance `bracketNotionalCap`
  - Bybit `storingLocationValue`
  - MEXC `notional_usdt`
  - WEEX `range` 上界

- 维持保证金率：
  - Binance `bracketMaintenanceMarginRate` → 百分数
  - Bybit `maintenanceMarginRate` → 百分数
  - MEXC `mmr` → 百分数
  - WEEX `mmr`（已为百分数字符串，原样）

---

## 6) 配置说明（config/settings）

建议在 `config/settings.py` 中提供下列键（脚本会有默认路径，存在则以配置为准）：

- SURF 抓取
  - `SURF_STATS_URL`：SURF 统计/支持币种页面 URL
  - `SURF_USE_BROWSER`：是否使用浏览器（默认 False，HTTP 模式不打开浏览器）
  - `SURF_HEADLESS`：启用浏览器时是否无头
  - `SURF_TIMEOUT`、`SURF_MAX_SCROLLS`、`SURF_SCROLL_PAUSE`
  - `SURF_ONLY_USDT=True`、`SURF_QUOTE='USDT'`
  - 输出：`DATA_DIR`、`OUTPUT_JSON`、`OUTPUT_CSV`、`OUTPUT_TXT`

- dataGet 输出（若提供）：`DATAGET_OUTPUT_DIR`（默认 `data/dataGet_api`）

- 其他敏感信息（如需要）：通过 `.env` 或系统环境变量加载，避免入库。

---

## 7) 备注与常见问题

- Binance 返回中可能存在后缀合约（如 `BTCUSDT_250328`）；本项目仅保留无后缀 `BASEUSDT` 的精确记录。
- WEEX 页面动态渲染较慢时，可在 `weex_brackets_fetch.py` 调整并发与 `render_timeout`、`per_wait`。
- 若某币种在某交易所缺失数据，对应表格行会留空。
- 目录名为 `tableMake/`（非 `tableMaker/`）。如误建，可直接删除无影响。
