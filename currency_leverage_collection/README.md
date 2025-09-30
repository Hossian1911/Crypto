# 项目说明：跨交易所杠杆-风险限额与维持保证金率采集与对比

本项目的目标是从 SURF 官方获取“支持币种清单”，并针对每个币种分别查询 Binance、Bybit、MEXC、WEEX 四家交易所在不同杠杆/风险分层下的“最大可开仓名义价值（或仓位上限）”及“维持保证金率（Maintenance Margin Rate, 简写 MM 或 maintMarginRatio）”，最终整合生成 Excel 表格，便于横向对比。

---

## 1. 项目架构

- `currencyGet_surf/`
  - 负责从 SURF 官网/接口获取支持交易的币种列表（如 `BTC`, `ETH` 等，或更精确的合约交易对 `BTCUSDT`）。
- `dataGet_binance/`
  - 负责调用 Binance 接口，拉取“分层（leverage/risk limit brackets）”的最大名义价值与维持保证金率。
- `dataGet_bybit/`
  - 负责调用 Bybit 接口，拉取“风险限额（Risk Limit）/分层”的最大名义价值与维持保证金率。
- `dataGet_mexc/`
  - 负责调用 MEXC 接口，拉取“风险限额/分层”的最大名义价值与维持保证金率。
- `dataGet_weex/`
  - 负责调用 WEEX 接口或页面抓取，获取“风险限额/分层”的最大名义价值与维持保证金率。
- `config/`
  - 存放统一配置（API 域名、请求头、签名参数、超时与重试、输出目录等）。
- `table_maker/`
  - 将上述数据合并，生成 Excel 表格。
- `main.py`
  - 主入口，一键执行：拉取 SURF 币种 -> 并发/批量查询四所分层参数 -> 规整与落表。

---

## 2. 数据口径与术语统一

为保证对比一致性，定义如下数据口径：

- “币种/交易对”：以 USDT 合约为主（例如 `BTCUSDT`）。若 SURF 返回币种而非具体交易对，则需在各交易所内映射到标准合约符号。
- “杠杆倍数（X）”：交易所通常以分层（tier/bracket）方式定义，最大杠杆随名义头寸（notional/position size）增加而降低。
- “最大可开仓名义价值/仓位上限”：每个分层给出一个 `notionalCap`（或 `position limit / riskLimitValue`），在该区间内对应一个最大可用初始杠杆（initial leverage）。
- “维持保证金率（MM）”：分层中的维持保证金率；不同交易所字段名称不同，但语义一致，用于计算维持保证金。
- “分层/风险限额（tier/risk limit/bracket）”：同一合约会给出多档区间，每档包含：名义价值范围、最大初始杠杆、维持保证金率等参数。

注意：
- 各交易所字段命名不同、单位可能不同（如名义价值单位为 USDT 或 合约张数）。本项目在 `table_maker` 统一换算为“USDT 名义价值”。
- 有的接口是“私有接口（需签名/密钥）”，有的为“公开接口（无需密钥）”。本项目优先使用公开接口；若仅私有可得，则在 `config` 中配置 API Key/Secret，并在代码中安全加载。

---

## 3. 各交易所接口与字段映射（初版调研）

以下为基于官方文档的字段定位思路与常用端点。具体实现时请以最新文档为准并在代码中进行健壮性处理。

### 3.1 Binance（币安）USDT 永续（USDS-M）

- 文档：`developers.binance.com` -> Derivatives -> USDⓈ-Margined Futures -> Account/Market API
- 关键端点（常见）：
  - 【分层/杠杆表】`/fapi/v1/leverageBracket`（历史上为 USER-DATA；后来也提供过市场数据版本，具体以变更日志为准）。
- 典型响应字段（示例）：
  - `symbol`: 如 `BTCUSDT`
  - `brackets`: 数组，每个元素包含：
    - `bracket`: 分层序号
    - `initialLeverage`: 该层最大初始杠杆
    - `notionalCap`: 该层名义价值上限（USDT）
    - `notionalFloor`: 该层名义价值下限（USDT）
    - `maintMarginRatio`: 该层维持保证金率
- 口径说明：
  - 可将 `initialLeverage` 直接映射为某一档“最大杠杆 X”。
  - 以 `notionalCap` 为“最大可开仓名义价值（USDT）”。
  - 以 `maintMarginRatio` 为“维持保证金率”。

### 3.2 Bybit v5

- 文档：`bybit-exchange.github.io/docs/v5/`
- 关键端点（常见）：
  - 【合约基础信息】`GET /v5/market/instruments-info`（返回合约基础过滤器，不一定包含分层）
  - 【风险限额列表】`GET /v5/contract/risk-limit`（线性/反向合约的风险限额分层）
    - 典型查询参数：`category=linear|inverse`，`symbol=BTCUSDT`
- 典型响应字段（示例，以 risk-limit 为参考）：
  - `riskId`：分层编号
  - `limit` 或 `riskLimitValue`：该层名义价值上限（单位通常为 USDT 或 合约面值换算）
  - `maintainMargin` 或 `maintainMarginRate`：该层维持保证金（率）
  - `initialMargin` 或 `initialMarginRate`：初始保证金（率），可换算最大杠杆（`leverage = 1 / initialMarginRate`）
- 口径说明：
  - 若返回初始保证金率，可通过 `1 / initialMarginRate` 求得“最大初始杠杆”。
  - 名义价值单位需根据合约面值统一换算为 USDT。

### 3.3 MEXC 合约

- 文档：`mexc.com/api-docs/`（合约/期货）
- 关键端点（常见）：
  - 【风险限额】文档条目：`Get risk limits`（部分场景属于私有端点，需要 API Key/签名）。
- 典型响应字段（示例，命名以文档为准）：
  - `level`/`tier`：分层编号
  - `maxNotional` / `positionLimit`：该层名义价值/仓位上限
  - `maintMarginRate`：维持保证金率
  - `initialMarginRate`：初始保证金率（可换算最大杠杆）
- 口径说明：
  - 若仅私有接口可得，则需在 `config` 中配置密钥，并在运行时签名请求。

### 3.4 WEEX 合约

- 文档/帮助中心：`weex.com`（风险限额说明页）
- 当前观察：
  - 官方 API 文档对“风险限额/维持保证金率”的开放度较低，可能需要：
    - 方案 A：若存在公开或私有 API，则直接请求。
    - 方案 B：无 API 时，抓取官方“风险限额表格”网页并解析（需做好反爬与结构变更兜底）。
- 目标字段：
  - 分层编号、每层最大杠杆、名义价值上限、维持保证金率。

---

## 4. 数据抓取与映射流程

1. `currencyGet_surf`
   - 尝试通过 SURF 官网/接口获取“支持币种清单”。
   - 若仅有币种（如 `BTC`），需要在各交易所内映射至标准交易对（优先 USDT 合约：`BTCUSDT`）。
   - 产出：`symbols.json`，例如：
     ```json
     {
       "BTC": {"binance": "BTCUSDT", "bybit": "BTCUSDT", "mexc": "BTC_USDT", "weex": "BTCUSDT"},
       "ETH": {"binance": "ETHUSDT", ...}
     }
     ```

2. `dataGet_*`
   - 针对每个交易所、每个合约交易对，请求该交易所“分层/风险限额”接口。
   - 解析字段并统一映射为通用结构：
     ```json
     {
       "exchange": "binance",
       "symbol": "BTCUSDT",
       "tiers": [
         {"tier": 1, "maxLeverage": 125, "notionalCapUSDT": 50000, "maintMarginRate": 0.003},
         {"tier": 2, "maxLeverage": 100, "notionalCapUSDT": 100000, "maintMarginRate": 0.002},
         ...
       ]
     }
     ```
   - 注意：
     - 若返回初始保证金率 `imr`，则 `maxLeverage = round(1 / imr)`。
     - 若名义价值单位为“张数/合约单位”，需按合约面值/标的价格换算为 USDT 名义价值（落表需明确写明计算基价时间戳）。

3. `table_maker`
   - 输入：来自四所的数据（按每个交易对合并）。
   - 输出：Excel 表格（`xlsx`），表头类似：
     - 行维度：杠杆档位（125X、100X、75X、50X、25X、10X等）
     - 列维度：`BINANCE 最大开仓`、`BINANCE 维持保证金率`、`BYBIT 最大开仓`、`BYBIT 维持保证金率`、`WEEX 最大开仓`、`WEEX 维持保证金率`、`MEXC 最大开仓`、`MEXC 维持保证金率`
   - 逻辑：
     - 将每家交易所“分层（以最大杠杆/初始保证金率）”映射到上述行中的标准杠杆档位，若某档不存在则留空或填 `-`。

4. `main`
   - 步骤编排：拉取 SURF 币种 -> 并发请求四所数据 -> 统一映射 -> 生成 Excel。

---

## 5. 输出表格格式（示意）

以 `BTC` 为例（与需求配图一致）：

- 行：`125X / 100X / 75X / 50X / 25X / 10X`
- 列：`BINANCE 最大开仓`、`BINANCE 维持保证金率`、`BYBIT 最大开仓`、`BYBIT 维持保证金率`、`WEEX 最大开仓`、`WEEX 维持保证金率`、`MEXC 最大开仓`、`MEXC 维持保证金率`

空缺数据使用 `-` 表示。

---

## 6. 开发注意事项

- 并发与限频：各交易所有速率限制，需加入速率控制与指数退避重试。
- 错误兜底：接口波动、字段变更、网络超时需重试和降级（如仅返回部分分层则照常落表）。
- 时间戳/价差：若需名义价值换算（基于标记价格/指数价），请统一在同一时间点取价并记录时间戳，落表备注“价格采样时间”。
- 账号与密钥：尽量使用公开端点；若必须使用私有端点，请在本地 `.env` 或系统安全存储中配置，不要写入仓库。
- 可测试性：为每家交易所编写最小可运行示例与 Mock，便于在无密钥/离线情况下跑通表格生成流程。

---

## 7. 任务清单（Roadmap）

- [ ] 确认 SURF 官网与“支持币种”获取方式（API/页面抓取），落地 `currencyGet_surf`。
- [ ] Binance：验证 `/fapi/v1/leverageBracket` 可用性（市场/私有），完成解析器与映射。
- [ ] Bybit：实现 `GET /v5/contract/risk-limit`（或同等端点）解析与映射。
- [ ] MEXC：确认 `Get risk limits` 端点可用性（若私有则接入签名），实现解析与映射。
- [ ] WEEX：确认是否存在公开/私有端点；若无，开发网页抓取器并解析风险限额表格。
- [ ] 统一名义价值单位为 USDT，必要时增加价格服务用于换算。
- [ ] `table_maker`：实现 Excel 生成，行按标准杠杆档位，列按交易所与指标。
- [ ] `main`：整合一键运行，提供日志与失败重试。

---

## 8. 字段映射速查表（对齐到通用模型）

通用模型字段：`tier` / `maxLeverage` / `notionalCapUSDT` / `maintMarginRate`

- Binance：
  - `initialLeverage` -> `maxLeverage`
  - `notionalCap` -> `notionalCapUSDT`
  - `maintMarginRatio` -> `maintMarginRate`
- Bybit（risk limit）：
  - `initialMarginRate` -> `maxLeverage = 1 / initialMarginRate`
  - `riskLimitValue`/`limit` -> `notionalCapUSDT`（按单位换算）
  - `maintainMargin`/`maintainMarginRate` -> `maintMarginRate`
- MEXC：
  - `initialMarginRate` -> `maxLeverage = 1 / initialMarginRate`
  - `maxNotional`/`positionLimit` -> `notionalCapUSDT`
  - `maintMarginRate` -> `maintMarginRate`
- WEEX：
  - 页面或接口中的“最大杠杆” -> `maxLeverage`
  - “仓位/名义上限” -> `notionalCapUSDT`
  - “维持保证金率” -> `maintMarginRate`

---

## 9. 依赖与实现建议

- 语言与库建议：
  - Python 3.10+
  - `httpx`（或 `requests`）用于 HTTP 请求
  - `pydantic` 统一响应数据校验与模型化
  - `pandas`, `openpyxl` 用于 Excel 生成
  - `tenacity`/自实现重试
- 目录建议：
  - `currencyGet_surf/`
  - `dataGet_binance/`, `dataGet_bybit/`, `dataGet_mexc/`, `dataGet_weex/`
  - `table_maker/`
  - `config/`
  - `main.py`

---

## 10. 备注

- 文档与端点可能会有变更，请以各交易所官方文档为准，并在实现中加上变更感知与告警。
- 若 SURF 并无公开 API，则采用页面抓取或从 SURF 的静态资源中解析支持币种清单；无法自动化时，先落地人工字典，后续替换为自动化。
