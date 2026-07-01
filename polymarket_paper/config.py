"""配置加载与纸面模式守卫。

只依赖标准库(tomllib)。配置文件见仓库根目录 config.toml。
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class BankrollCfg:
    initial_usdc: float = 10_000.0
    kelly_multiplier: float = 0.25
    max_stake_fraction: float = 0.02


@dataclass
class StrategyCfg:
    edge_threshold: float = 0.05
    decision_interval_s: float = 30.0
    warmup_s: float = 30.0
    cutoff_s: float = 20.0
    max_trades_per_market: int = 1
    min_stake_usdc: float = 1.0
    tie_resolves_down: bool = True


@dataclass
class SignalCfg:
    vol_halflife_s: float = 300.0
    min_sigma: float = 1e-6
    prob_clamp: float = 0.005


@dataclass
class FeesCfg:
    # 兜底 taker 费率(基点);实际优先读取 CLOB 市场对象里的 taker_base_fee
    taker_fee_bps: float = 200.0


@dataclass
class BinanceCfg:
    ws_url: str = "wss://stream.binance.com:9443/ws/btcusdt@trade"
    rest_url: str = "https://api.binance.com"
    symbol: str = "BTCUSDT"


@dataclass
class PolymarketCfg:
    gamma_url: str = "https://gamma-api.polymarket.com"
    clob_rest_url: str = "https://clob.polymarket.com"
    clob_ws_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    series_title_regex: str = r"Bitcoin Up or Down"
    interval_minutes: int = 15
    book_poll_fallback_s: float = 5.0
    # 手动钉死某个市场(自动发现失败时的逃生口),留空则自动发现
    override_condition_id: str = ""
    override_token_id_up: str = ""
    override_token_id_down: str = ""


@dataclass
class Config:
    paper: bool = True
    keys: dict[str, str] = field(default_factory=dict)
    bankroll: BankrollCfg = field(default_factory=BankrollCfg)
    strategy: StrategyCfg = field(default_factory=StrategyCfg)
    signal: SignalCfg = field(default_factory=SignalCfg)
    fees: FeesCfg = field(default_factory=FeesCfg)
    binance: BinanceCfg = field(default_factory=BinanceCfg)
    polymarket: PolymarketCfg = field(default_factory=PolymarketCfg)
    db_path: str = "data/paper.db"


def _fill(dc_cls, raw: dict):
    """用 toml 字典里存在的键覆盖 dataclass 默认值,忽略未知键。"""
    kwargs = {k: raw[k] for k in raw if k in dc_cls.__dataclass_fields__}
    return dc_cls(**kwargs)


def load_config(path: str | Path = "config.toml") -> Config:
    raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    cfg = Config(
        paper=raw.get("mode", {}).get("paper", True),
        keys={k: str(v) for k, v in raw.get("keys", {}).items()},
        bankroll=_fill(BankrollCfg, raw.get("bankroll", {})),
        strategy=_fill(StrategyCfg, raw.get("strategy", {})),
        signal=_fill(SignalCfg, raw.get("signal", {})),
        fees=_fill(FeesCfg, raw.get("fees", {})),
        binance=_fill(BinanceCfg, raw.get("binance", {})),
        polymarket=_fill(PolymarketCfg, raw.get("polymarket", {})),
        db_path=raw.get("data", {}).get("db_path", "data/paper.db"),
    )
    return cfg


def assert_paper_mode(cfg: Config) -> None:
    """纸面模式硬守卫:mode.paper 必须为 true,且所有密钥必须为空串。"""
    if not cfg.paper:
        raise SystemExit("v0.1 只支持纸面模式:请把 [mode] paper 设为 true")
    for name, value in cfg.keys.items():
        if value.strip():
            raise SystemExit(
                f"纸面模式要求所有密钥留空,但 keys.{name} 非空。拒绝启动。"
            )
