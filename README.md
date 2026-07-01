# Polymarket 校准交易系统 v0.1(纸面模式)

第一阶段:15 分钟 BTC up/down 纸面交易,跑四周,攒校准样本、验证数据管道。
**全程无真实下单** —— 代码里不存在下单路径,且启动时强制校验所有密钥为空。
定位:训练场 + 管道验证,不是提款机;真金白银的目标在第二阶段的事件市场。

**零第三方依赖**:只需要 Python 3.11+,不用 pip 装任何东西。联网请求、
WebSocket 行情订阅、图表全部用标准库自实现(`netutil.py` / `miniws.py` /
`svgchart.py`)。

## 这是个什么东西

一个命令行程序,两条命令:

```bash
python -m polymarket_paper.main       # ① 采集器:挂机跑,记录预测与假想单
python -m polymarket_paper.report     # ② 报表:生成 reports/report.html
```

`report.html` 是自包含网页(双击用浏览器打开,自动适配深色模式),
包含两个关键数字、校准曲线、滚动 Brier、校准明细表;另有 report.md
文本版和 CSV 原始数据。

## 先跑仿真(不联网,验证全流程)

```bash
python -m unittest discover -s tests    # 75 项单测,秒级
python scripts/simulate.py              # 本机模拟交易所,把 15 分钟压成 1 分钟,
                                        # 完整跑 发现市场→快照→模拟入场→结算→报表
```

仿真会同时验证两条结算路径(Polymarket 官方结果 / Binance K 线兜底),
结束后在 `data/sim/report.html` 出报表。**电脑不能联网也能跑这一步。**

## 架构

```
polymarket_paper/
├── signal.py        own_prob:零漂移 GBM,Φ(ln(S/S_open) / (σ√τ)),σ 为 EWMA 已实现波动率
├── sizing.py        1/4 Kelly + 单笔(本金+费)≤ 虚拟资金 2% + Polymarket taker 费公式
├── trader.py        入场决策:|own_prob − 卖一价| > 5pp 取便宜侧;结算损益计算
├── metrics.py       Brier / 滚动 Brier / 按概率分桶的校准曲线
├── storage.py       SQLite:markets / records(时间戳、市场ID、own_prob、市场价、
│                    假想仓位、结算结果)/ state(虚拟资金)
├── settlement.py    结算:优先 Polymarket 官方结果,兜底 Binance 15m K 线
├── report.py        报表:report.html + report.md + CSV
├── main.py          主循环:发现市场 → 快照 → 模拟入场 → 结算
├── miniws.py        迷你 WebSocket 客户端/测试服务端(RFC 6455,纯标准库)
├── netutil.py       HTTP JSON 工具(urllib 异步包装)
├── svgchart.py      SVG 图表渲染(校准曲线、滚动 Brier)
└── feeds/
    ├── binance.py     BTC 现货成交 WS(参照价 + 波动率)
    └── polymarket.py  Gamma 市场发现 + CLOB 订单簿 WS(REST 轮询兜底)
scripts/simulate.py  离线端到端仿真(本机模拟 Polymarket + Binance)
```

## 真跑之前的核对清单(本仓库在无外网环境中开发,需对照线上确认)

1. **市场发现**:通过 Gamma `/markets` 按标题正则 `Bitcoin Up or Down`
   + endDate 落在 15 分钟整点边界筛选。若线上命名不同,调整 `config.toml`
   的 `series_title_regex`,或用 `override_condition_id` 手动钉死先跑通。
2. **taker 费率**:优先读 CLOB 市场对象的 `taker_base_fee`,读不到用
   `[fees] taker_fee_bps`(默认 200bps)兜底 —— 对照官网当前费率改。
3. **结算规则**:默认平盘判 Down(Up 需严格上涨),核对 `tie_resolves_down`。
   Binance 兜底结算与 Polymarket 官方价格源可能有极小分歧,报表里
   `resolution_source` 字段可区分。

## 四周后看两个数字

1. `report.html` 里 **own_prob 的 Brier 是否低于市场价的 Brier**
   (即是否跑赢"直接抄市场价"这个基线);
2. **扣除点差(以卖一价入场)和 taker 费后的假想净损益**。

赢了 → 升级到真钱小仓;没赢 → 管道全部保留,策略转攻低流动性事件市场。
