# Polymarket 校准交易系统 v0.1(纸面模式)

第一阶段:5 分钟 BTC up/down 纸面交易(实测确认周期是 5 分钟,不是最初设想的
15 分钟),跑四周,攒校准样本、验证数据管道。
**全程无真实下单** —— 代码里不存在下单路径,且启动时强制校验所有密钥为空。
定位:训练场 + 管道验证,不是提款机;真金白银的目标在第二阶段的事件市场。

**零第三方依赖**:只需要 Python 3.11+,不用 pip 装任何东西。联网请求、
WebSocket 行情订阅、图表全部用标准库自实现(`netutil.py` / `miniws.py` /
`svgchart.py`)。

## 这是个什么东西

一个命令行程序,两条命令:

```bash
python -m polymarket_paper.main         # ① 采集器:挂机跑,记录预测与假想单
python -m polymarket_paper.dashboard    # ② 实时仪表盘:浏览器打开一直看
python -m polymarket_paper.report       # ③(可选)导出一次性的 report.html/CSV 快照
```

**实时仪表盘**(`dashboard.py`):本地网页,①②两个命令同时开着跑,浏览器打开
`http://127.0.0.1:8765` 就能看,页面每 8 秒自动刷新一次(数字改用 `--interval`
调),不用手动重新生成、不用刷新浏览器。只监听 127.0.0.1,不对外网暴露。

`report.py` 生成的是某一时刻的静态快照(`reports/report.html` + report.md +
CSV),适合导出存档或分享;日常盯盘用仪表盘就够了。两者内容一样:两个关键
数字、运行状态(虚拟资金/追踪市场数/待结算数)、校准曲线、滚动 Brier、
校准明细表,自动适配深色模式。

## 先跑仿真(不联网,验证全流程)

```bash
python -m unittest discover -s tests    # 75 项单测,秒级
python scripts/simulate.py              # 本机模拟交易所,把区间压成 1 分钟,
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
├── report.py        报表渲染(render_body 供 report.py / dashboard.py 共用)+ 静态导出
├── dashboard.py     本地实时仪表盘:HTTP 服务,浏览器轮询 /fragment 自动刷新
├── main.py          主循环:发现市场 → 快照 → 模拟入场 → 结算
├── miniws.py        迷你 WebSocket 客户端/测试服务端(RFC 6455,纯标准库)
├── netutil.py       HTTP JSON 工具(urllib 异步包装)
├── svgchart.py      SVG 图表渲染(校准曲线、滚动 Brier)
└── feeds/
    ├── binance.py     BTC 现货成交 WS(参照价 + 波动率)
    └── polymarket.py  Gamma 市场发现 + CLOB 订单簿 WS(REST 轮询兜底)
scripts/simulate.py  离线端到端仿真(本机模拟 Polymarket + Binance)
```

## 首跑核对清单(2025-12-19 已对照官方 API 实测确认)

1. **市场发现**:✅ 已确认。Gamma `/markets` 里标题格式为
   `Bitcoin Up or Down - <月> <日>, <时:分><AM/PM>-<时:分><AM/PM> ET`,
   `series_title_regex = "Bitcoin Up or Down"` 能正确匹配。**区间实际是
   5 分钟一档,不是最初设想的 15 分钟**——`config.toml` 的
   `interval_minutes` 已改为 5,相关参数(波动率窗口、决策间隔等)已按
   比例缩短。若未来线上命名变化,用 `override_condition_id` 手动钉死
   先跑通。
2. **taker 费率**:优先读 CLOB 市场对象的 `taker_base_fee`,读不到用
   `[fees] taker_fee_bps`(默认 200bps)兜底。**尚未实测确认真实数值**,
   建议首次运行前手动查一次:
   `Invoke-RestMethod -Uri "https://clob.polymarket.com/markets/<conditionId>"`。
3. **结算规则**:✅ 已确认。官方文案原文:"结束价 >= 开始价 判 Up,
   否则判 Down"——**平局(价格不变)算 Up**,`tie_resolves_down` 已改为
   `false`。结算依据 Chainlink BTC/USD 数据流,不是 Binance 现货或其他
   交易所价格,因此 Binance 只作为官方结果未出时的临时近似兜底(两者
   存在极小概率的边缘分歧),报表里 `resolution_source` 字段可区分每
   条记录到底用的哪个价格源。

## 四周后看两个数字

1. `report.html` 里 **own_prob 的 Brier 是否低于市场价的 Brier**
   (即是否跑赢"直接抄市场价"这个基线);
2. **扣除点差(以卖一价入场)和 taker 费后的假想净损益**。

赢了 → 升级到真钱小仓;没赢 → 管道全部保留,策略转攻低流动性事件市场。
