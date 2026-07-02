"""主循环:按 15 分钟周期发现市场 → 记录预测快照 → 模拟入场 → 到期结算。

用法:
    python -m polymarket_paper.main [--config config.toml]

全程无真实下单;启动即执行纸面模式硬校验(密钥必须留空)。
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import time

from .config import Config, assert_paper_mode, load_config
from .feeds.binance import BinanceFeed
from .feeds.polymarket import BookFeed, MarketInfo, PolymarketClient
from .settlement import settle_pending
from .signal import EwmaVol, prob_up
from .storage import Store
from .trader import decide

log = logging.getLogger("polymarket_paper")


async def run_market_cycle(cfg: Config, store: Store, binance: BinanceFeed,
                           client: PolymarketClient, market: MarketInfo) -> None:
    """跑完一个 15 分钟市场:定期快照 + 至多 N 笔假想单。"""
    st = cfg.strategy
    fee_bps = market.fee_bps if market.fee_bps >= 0 else cfg.fees.taker_fee_bps
    store.upsert_market(
        market_id=market.condition_id, slug=market.slug,
        question=market.question, token_id_up=market.token_id_up,
        token_id_down=market.token_id_down,
        interval_start=market.interval_start,
        interval_end=market.interval_end, fee_bps=fee_bps)

    book = BookFeed(cfg=cfg.polymarket, client=client,
                    token_ids=[market.token_id_up, market.token_id_down])
    book_task = asyncio.ensure_future(book.run())

    open_price: float | None = None
    trades_done = store.count_trades(market.condition_id)
    try:
        while True:
            now = time.time()
            remaining = market.interval_end - now
            if remaining <= 0:
                break

            # 区间开始后第一时间锚定 Binance 参照开盘价
            if open_price is None and now >= market.interval_start \
                    and binance.last_price is not None:
                open_price = binance.last_price
                store.set_market_open_price(market.condition_id, open_price)
                log.info("区间 %s 开盘参照价 %.2f",
                         market.slug or market.condition_id[:16], open_price)

            in_window = (now - market.interval_start) >= st.warmup_s
            if open_price is not None and in_window \
                    and binance.last_price is not None and binance.vol.ready:
                own = prob_up(binance.last_price, open_price, remaining,
                              binance.vol.sigma, cfg.signal.prob_clamp)
                bid_up, ask_up = book.top(market.token_id_up)
                _, ask_down = book.top(market.token_id_down)
                mid_up = (bid_up + ask_up) / 2 \
                    if bid_up is not None and ask_up is not None else None

                intent = None
                if remaining > st.cutoff_s and trades_done < st.max_trades_per_market:
                    bankroll = store.get_bankroll(cfg.bankroll.initial_usdc)
                    intent = decide(own, ask_up, ask_down, bankroll, cfg, fee_bps)

                store.insert_record(
                    market_id=market.condition_id, own_prob=own,
                    mkt_bid_up=bid_up, mkt_ask_up=ask_up, mkt_mid_up=mid_up,
                    mkt_ask_down=ask_down, btc_price=binance.last_price,
                    sigma=binance.vol.sigma, remaining_s=remaining,
                    side=intent.side if intent else None,
                    entry_price=intent.price if intent else None,
                    stake=intent.stake if intent else None,
                    shares=intent.shares if intent else None,
                    fee=intent.fee if intent else None)
                if intent:
                    trades_done += 1
                    log.info("假想单: %s @ %.3f,edge %.3f,本金 %.2f + 费 %.2f",
                             intent.side, intent.price, intent.edge,
                             intent.stake, intent.fee)
            await asyncio.sleep(min(st.decision_interval_s, max(remaining, 0.5)))
    finally:
        book_task.cancel()


async def main_loop(cfg: Config) -> None:
    store = Store(cfg.db_path)
    vol = EwmaVol(cfg.signal.vol_halflife_s, cfg.signal.min_sigma)
    binance = BinanceFeed(cfg.binance, vol)
    client = PolymarketClient(cfg.polymarket)
    binance_task = asyncio.ensure_future(binance.run())
    log.info("纸面模式启动,虚拟资金 %.2f USDC",
             store.get_bankroll(cfg.bankroll.initial_usdc))
    try:
        while True:
            # 先结清所有已到期市场
            await settle_pending(store, cfg, client)

            market = None
            try:
                market = await client.discover_current_market()
            except Exception as exc:
                log.warning("市场发现失败: %s", exc)
            if market is None:
                log.info("暂未发现进行中的 15 分钟 BTC 市场,20s 后重试")
                await asyncio.sleep(20)
                continue

            log.info("进入市场 %s(%s),结算于 %s",
                     market.slug or market.condition_id[:16], market.question,
                     time.strftime("%H:%M:%S", time.localtime(market.interval_end)))
            await run_market_cycle(cfg, store, binance, client, market)
            # 区间刚结束,稍等结算数据可用
            await asyncio.sleep(5)
    finally:
        binance_task.cancel()
        store.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Polymarket 纸面校准交易系统")
    parser.add_argument("--config", default="config.toml")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load_config(args.config)
    assert_paper_mode(cfg)
    try:
        asyncio.run(main_loop(cfg))
    except KeyboardInterrupt:
        log.info("已停止(Ctrl+C)")


if __name__ == "__main__":
    main()
