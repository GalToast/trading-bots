#!/usr/bin/env python3
"""
All-product Kraken spot grid optimizer.

This is deliberately stricter than the older grid backtests:
- sells are processed before buys, so a new candle-low fill cannot also claim a
  same-candle candle-high exit with unknown path order
- open inventory is marked to market after liquidation fee at the final close
- drawdown, max open inventory, and unresolved bags are first-class metrics
- rankings require enough completed closes to avoid single-lucky-wick winners
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
REPORTS = ROOT / "reports"
CACHE = REPORTS / "cache"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from kraken_config import DEFAULT_MAKER_FEE_BPS, DEFAULT_TAKER_FEE_BPS  # noqa: E402

DEFAULT_CACHE_PATH = CACHE / "kraken_spot_pulse_candles.json"
DEFAULT_JSON_PATH = REPORTS / "kraken_grid_optimizer.json"
DEFAULT_CSV_PATH = REPORTS / "kraken_grid_optimizer.csv"
DEFAULT_MD_PATH = REPORTS / "kraken_grid_optimizer.md"
DEFAULT_SPACINGS_BPS = "40,60,80,100,150,200,300,500,800"
DEFAULT_LEVELS = "3,5,8,12"
DEFAULT_ENTRY_OFFSETS = "1.0"
DEFAULT_EXIT_MODELS = "maker,taker"
DEFAULT_QUOTES = "USD,USDC,USDT"


@dataclass(frozen=True)
class Candle:
    t: float
    o: float
    h: float
    l: float
    c: float
    v: float = 0.0


@dataclass
class Position:
    level: int
    buy_price: float
    qty: float
    cost_usd: float
    buy_fee_usd: float
    target_price: float
    buy_time: float


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        out = float(value)
        if math.isnan(out) or math.isinf(out):
            return default
        return out
    except (TypeError, ValueError):
        return default


def parse_float_csv(raw: str) -> list[float]:
    return [float(part.strip()) for part in raw.split(",") if part.strip()]


def parse_int_csv(raw: str) -> list[int]:
    return [int(part.strip()) for part in raw.split(",") if part.strip()]


def rest_pair_to_product(pair: str) -> str:
    pair = pair.upper().replace("/", "").replace("-", "")
    for quote in ("USDT", "USDC", "USD", "EUR", "BTC", "XBT", "ETH"):
        if pair.endswith(quote) and len(pair) > len(quote):
            base = pair[: -len(quote)]
            if quote == "XBT":
                quote = "BTC"
            return f"{base}-{quote}"
    return pair


def product_quote(product_id: str) -> str:
    if "-" not in product_id:
        return ""
    return product_id.rsplit("-", 1)[1].upper()


def normalize_candle(row: dict[str, Any]) -> Candle | None:
    t = to_float(row.get("t", row.get("start", row.get("time"))))
    o = to_float(row.get("o", row.get("open")))
    h = to_float(row.get("h", row.get("high")))
    l = to_float(row.get("l", row.get("low")))
    c = to_float(row.get("c", row.get("close")))
    v = to_float(row.get("v", row.get("volume", row.get("ticks", 0.0))))
    if t <= 0 or o <= 0 or h <= 0 or l <= 0 or c <= 0:
        return None
    if l > h:
        return None
    return Candle(t=t, o=o, h=h, l=l, c=c, v=v)


def load_pulse_entries(cache: dict[str, Any]) -> dict[str, list[Candle]]:
    out: dict[str, list[Candle]] = {}
    entries = cache.get("entries")
    if not isinstance(entries, dict):
        return out
    for key, entry in entries.items():
        parts = str(key).split("|")
        if not parts:
            continue
        product = parts[0].upper()
        candles = entry.get("candles") if isinstance(entry, dict) else None
        if not isinstance(candles, list):
            continue
        parsed = [c for c in (normalize_candle(row) for row in candles if isinstance(row, dict)) if c]
        if parsed:
            parsed.sort(key=lambda candle: candle.t)
            out[product] = parsed
    return out


def load_ohlc_collector(cache: dict[str, Any]) -> dict[str, list[Candle]]:
    out: dict[str, list[Candle]] = {}
    products = cache.get("products")
    if not isinstance(products, dict):
        return out
    for product, product_data in products.items():
        grains = product_data.get("granularities") if isinstance(product_data, dict) else None
        if not isinstance(grains, dict):
            continue
        best: list[Candle] = []
        best_grain = 10**9
        for grain_raw, grain_data in grains.items():
            try:
                grain = int(grain_raw)
            except (TypeError, ValueError):
                continue
            candles = grain_data.get("candles") if isinstance(grain_data, dict) else None
            if not isinstance(candles, list):
                continue
            parsed = [c for c in (normalize_candle(row) for row in candles if isinstance(row, dict)) if c]
            if parsed and grain < best_grain:
                best = parsed
                best_grain = grain
        if best:
            best.sort(key=lambda candle: candle.t)
            out[str(product).upper()] = best
    return out


def load_bridge_dict(cache: dict[str, Any]) -> dict[str, list[Candle]]:
    out: dict[str, list[Candle]] = {}
    if not isinstance(cache, dict) or "entries" in cache or "products" in cache:
        return out
    for raw_pair, rows in cache.items():
        if not isinstance(rows, list):
            continue
        parsed = [c for c in (normalize_candle(row) for row in rows if isinstance(row, dict)) if c]
        if parsed:
            parsed.sort(key=lambda candle: candle.t)
            out[rest_pair_to_product(str(raw_pair))] = parsed
    return out


def load_candle_map(path: Path) -> tuple[str, dict[str, list[Candle]]]:
    cache = json.loads(path.read_text(encoding="utf-8"))
    loaders = [
        ("pulse_entries", load_pulse_entries),
        ("ohlc_collector", load_ohlc_collector),
        ("bridge_dict", load_bridge_dict),
    ]
    for name, loader in loaders:
        loaded = loader(cache)
        if loaded:
            return name, loaded
    return "unknown", {}


def mark_open_inventory(
    positions: list[Position],
    close: float,
    *,
    liquidation_fee_bps: float,
    mark_haircut_bps: float,
) -> float:
    haircut = max(0.0, liquidation_fee_bps + mark_haircut_bps) / 10000.0
    return sum(pos.qty * close * max(0.0, 1.0 - haircut) for pos in positions)


def liquidate_positions(
    positions: list[Position],
    close: float,
    *,
    liquidation_fee_bps: float,
    mark_haircut_bps: float,
) -> tuple[float, float, float]:
    """Return net proceeds, net PnL, and liquidation costs for all open positions."""
    cost_bps = max(0.0, liquidation_fee_bps + mark_haircut_bps) / 10000.0
    proceeds_net = 0.0
    net_pnl = 0.0
    liquidation_costs = 0.0
    for pos in positions:
        proceeds_gross = pos.qty * close
        cost = proceeds_gross * cost_bps
        proceeds = proceeds_gross - cost
        proceeds_net += proceeds
        liquidation_costs += cost
        net_pnl += proceeds - pos.cost_usd - pos.buy_fee_usd
    return proceeds_net, net_pnl, liquidation_costs


def simulate_grid(
    candles: list[Candle],
    *,
    spacing_bps: float,
    levels: int,
    entry_offset_mult: float,
    initial_capital: float,
    maker_fee_bps: float,
    exit_fee_bps: float,
    liquidation_fee_bps: float,
    mark_haircut_bps: float,
    enable_inventory_sweeps: bool,
    sweep_min_inventory_net_bps: float,
    sweep_max_inventory_pct: float,
    sweep_cooldown_candles: int,
    force_final_liquidation: bool,
    recenter_when_flat: bool,
) -> dict[str, Any]:
    if len(candles) < 2:
        return {"error": "not_enough_candles"}
    if spacing_bps <= 0 or levels <= 0 or initial_capital <= 0:
        return {"error": "bad_parameters"}

    spacing = spacing_bps / 10000.0
    entry_offset = max(0.0, entry_offset_mult) * spacing
    cash = initial_capital
    allocation = initial_capital / float(levels)
    anchor = candles[0].c
    positions: list[Position] = []
    realized_net = 0.0
    gross_profit = 0.0
    gross_loss = 0.0
    fees = 0.0
    closes = 0
    target_closes = 0
    sweep_closes = 0
    sweep_count = 0
    buys = 0
    max_open = 0
    max_inventory_cost = 0.0
    equity_peak = initial_capital
    max_drawdown_pct = 0.0
    turnover_usd = 0.0
    prev_close = candles[0].c
    last_sweep_index = -10**9

    for candle_index, candle in enumerate(candles[1:], start=1):
        if recenter_when_flat and not positions:
            anchor = prev_close

        # Conservative path ordering: old exits first, then new entries.
        remaining: list[Position] = []
        for pos in positions:
            if candle.h >= pos.target_price:
                proceeds_gross = pos.qty * pos.target_price
                sell_fee = proceeds_gross * exit_fee_bps / 10000.0
                proceeds_net = proceeds_gross - sell_fee
                pnl = proceeds_net - pos.cost_usd - pos.buy_fee_usd
                cash += proceeds_net
                fees += sell_fee
                realized_net += pnl
                turnover_usd += proceeds_gross
                closes += 1
                target_closes += 1
                if pnl >= 0:
                    gross_profit += pnl
                else:
                    gross_loss += abs(pnl)
            else:
                remaining.append(pos)
        positions = remaining

        open_levels = {pos.level for pos in positions}
        for level_idx in range(1, levels + 1):
            buy_price = anchor * (1.0 - entry_offset - (level_idx - 1) * spacing)
            if buy_price <= 0 or candle.l > buy_price or level_idx in open_levels:
                continue
            buy_fee = allocation * maker_fee_bps / 10000.0
            total_cash_needed = allocation + buy_fee
            if cash + 1e-12 < total_cash_needed:
                continue
            qty = allocation / buy_price
            target_price = buy_price * (1.0 + spacing)
            cash -= total_cash_needed
            fees += buy_fee
            turnover_usd += allocation
            buys += 1
            pos = Position(
                level=level_idx,
                buy_price=buy_price,
                qty=qty,
                cost_usd=allocation,
                buy_fee_usd=buy_fee,
                target_price=target_price,
                buy_time=candle.t,
            )
            positions.append(pos)
            open_levels.add(level_idx)

        open_cost = sum(pos.cost_usd + pos.buy_fee_usd for pos in positions)
        open_inventory_pct_now = open_cost / initial_capital * 100.0 if initial_capital > 0 else 0.0
        if (
            enable_inventory_sweeps
            and positions
            and open_inventory_pct_now >= sweep_max_inventory_pct
            and candle_index - last_sweep_index >= max(0, sweep_cooldown_candles)
        ):
            liquidation_value = mark_open_inventory(
                positions,
                candle.c,
                liquidation_fee_bps=liquidation_fee_bps,
                mark_haircut_bps=mark_haircut_bps,
            )
            inventory_net_bps = (liquidation_value / open_cost - 1.0) * 10000.0 if open_cost > 0 else 0.0
            if inventory_net_bps >= sweep_min_inventory_net_bps:
                proceeds, pnl, liquidation_costs = liquidate_positions(
                    positions,
                    candle.c,
                    liquidation_fee_bps=liquidation_fee_bps,
                    mark_haircut_bps=mark_haircut_bps,
                )
                cash += proceeds
                fees += liquidation_costs
                realized_net += pnl
                turnover_usd += proceeds
                closes += len(positions)
                sweep_closes += len(positions)
                sweep_count += 1
                last_sweep_index = candle_index
                if pnl >= 0:
                    gross_profit += pnl
                else:
                    gross_loss += abs(pnl)
                positions = []
                open_cost = 0.0

        max_open = max(max_open, len(positions))
        max_inventory_cost = max(max_inventory_cost, open_cost)
        equity = cash + mark_open_inventory(
            positions,
            candle.c,
            liquidation_fee_bps=liquidation_fee_bps,
            mark_haircut_bps=mark_haircut_bps,
        )
        equity_peak = max(equity_peak, equity)
        if equity_peak > 0:
            max_drawdown_pct = max(max_drawdown_pct, (equity_peak - equity) / equity_peak * 100.0)
        prev_close = candle.c

    last_close = candles[-1].c
    final_liquidation_closes = 0
    if force_final_liquidation and positions:
        proceeds, pnl, liquidation_costs = liquidate_positions(
            positions,
            last_close,
            liquidation_fee_bps=liquidation_fee_bps,
            mark_haircut_bps=mark_haircut_bps,
        )
        cash += proceeds
        fees += liquidation_costs
        realized_net += pnl
        turnover_usd += proceeds
        closes += len(positions)
        sweep_closes += len(positions)
        final_liquidation_closes = len(positions)
        if pnl >= 0:
            gross_profit += pnl
        else:
            gross_loss += abs(pnl)
        positions = []

    open_inventory_liquidation_value = mark_open_inventory(
        positions,
        last_close,
        liquidation_fee_bps=liquidation_fee_bps,
        mark_haircut_bps=mark_haircut_bps,
    )
    final_equity = cash + open_inventory_liquidation_value
    final_return_pct = (final_equity / initial_capital - 1.0) * 100.0
    open_inventory_cost = sum(pos.cost_usd + pos.buy_fee_usd for pos in positions)
    open_inventory_pct = open_inventory_cost / initial_capital * 100.0
    win_rate_pct = gross_profit / (gross_profit + gross_loss) * 100.0 if (gross_profit + gross_loss) else 0.0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0)

    score = final_return_pct
    score -= max(0.0, max_drawdown_pct - 2.0) * 0.15
    score -= open_inventory_pct * 0.05
    if closes < 3:
        score -= (3 - closes) * 1.0

    return {
        "spacing_bps": float(spacing_bps),
        "levels": int(levels),
        "entry_offset_mult": float(entry_offset_mult),
        "initial_capital": round(initial_capital, 8),
        "final_equity": round(final_equity, 8),
        "final_return_pct": round(final_return_pct, 6),
        "cash": round(cash, 8),
        "realized_net_usd": round(realized_net, 8),
        "open_inventory_liquidation_value": round(open_inventory_liquidation_value, 8),
        "open_inventory_cost": round(open_inventory_cost, 8),
        "open_inventory_pct": round(open_inventory_pct, 6),
        "open_positions": len(positions),
        "max_open_positions": max_open,
        "max_inventory_pct": round(max_inventory_cost / initial_capital * 100.0, 6),
        "max_drawdown_pct": round(max_drawdown_pct, 6),
        "buys": buys,
        "closes": closes,
        "target_closes": target_closes,
        "sweep_closes": sweep_closes,
        "sweep_count": sweep_count,
        "final_liquidation_closes": final_liquidation_closes,
        "win_rate_pct": round(win_rate_pct, 6),
        "profit_factor": round(profit_factor, 6),
        "fees_usd": round(fees, 8),
        "turnover_usd": round(turnover_usd, 8),
        "score": round(score, 6),
    }


def product_rows(
    candle_map: dict[str, list[Candle]],
    *,
    products: set[str],
    quote_currencies: set[str],
    min_candles: int,
) -> dict[str, list[Candle]]:
    out: dict[str, list[Candle]] = {}
    for product, candles in candle_map.items():
        product = product.upper()
        if products and product not in products:
            continue
        if quote_currencies and product_quote(product) not in quote_currencies:
            continue
        if len(candles) < min_candles:
            continue
        out[product] = candles
    return out


def build_payload(
    *,
    cache_path: Path,
    products: set[str],
    quote_currencies: set[str],
    spacings_bps: list[float],
    levels_values: list[int],
    entry_offset_mults: list[float],
    exit_models: list[str],
    initial_capital: float,
    maker_fee_bps: float,
    taker_fee_bps: float,
    mark_haircut_bps: float,
    min_candles: int,
    min_closes: int,
    max_ending_inventory_pct: float,
    max_drawdown_pct: float,
    enable_inventory_sweeps: bool,
    sweep_min_inventory_net_bps: float,
    sweep_max_inventory_pct: float,
    sweep_cooldown_candles: int,
    force_final_liquidation: bool,
    recenter_when_flat: bool,
) -> dict[str, Any]:
    source, candle_map = load_candle_map(cache_path)
    selected = product_rows(
        candle_map,
        products=products,
        quote_currencies=quote_currencies,
        min_candles=min_candles,
    )
    rows: list[dict[str, Any]] = []
    best_by_product: dict[str, dict[str, Any]] = {}

    for product_id, candles in selected.items():
        span_minutes = (candles[-1].t - candles[0].t) / 60.0 if len(candles) > 1 else 0.0
        for spacing_bps in spacings_bps:
            for levels in levels_values:
                for entry_offset_mult in entry_offset_mults:
                    for exit_model in exit_models:
                        exit_fee = maker_fee_bps if exit_model == "maker" else taker_fee_bps
                        result = simulate_grid(
                            candles,
                            spacing_bps=spacing_bps,
                            levels=levels,
                            entry_offset_mult=entry_offset_mult,
                            initial_capital=initial_capital,
                            maker_fee_bps=maker_fee_bps,
                            exit_fee_bps=exit_fee,
                            liquidation_fee_bps=taker_fee_bps,
                            mark_haircut_bps=mark_haircut_bps,
                            enable_inventory_sweeps=enable_inventory_sweeps,
                            sweep_min_inventory_net_bps=sweep_min_inventory_net_bps,
                            sweep_max_inventory_pct=sweep_max_inventory_pct,
                            sweep_cooldown_candles=sweep_cooldown_candles,
                            force_final_liquidation=force_final_liquidation,
                            recenter_when_flat=recenter_when_flat,
                        )
                        if "error" in result:
                            continue
                        row = {
                            "product_id": product_id,
                            "source": source,
                            "candles": len(candles),
                            "span_minutes": round(span_minutes, 2),
                            "exit_model": exit_model,
                            **result,
                        }
                        row["profitable"] = bool(row["closes"] >= min_closes and row["final_return_pct"] > 0)
                        row["qualified"] = bool(
                            row["profitable"]
                            and row["open_inventory_pct"] <= max_ending_inventory_pct
                            and row["max_drawdown_pct"] <= max_drawdown_pct
                        )
                        rows.append(row)
                        current_best = best_by_product.get(product_id)
                        if current_best is None or row["score"] > current_best["score"]:
                            best_by_product[product_id] = row

    qualified = [row for row in rows if row["qualified"]]
    rows.sort(key=lambda row: (row["qualified"], row["score"], row["final_return_pct"]), reverse=True)
    best_rows = sorted(
        best_by_product.values(),
        key=lambda row: (row["qualified"], row["score"], row["final_return_pct"]),
        reverse=True,
    )

    return {
        "generated_at": utc_now_iso(),
        "parameters": {
            "cache_path": str(cache_path),
            "source": source,
            "products_filter": sorted(products),
            "quote_currencies": sorted(quote_currencies),
            "spacings_bps": spacings_bps,
            "levels": levels_values,
            "entry_offset_mults": entry_offset_mults,
            "exit_models": exit_models,
            "initial_capital": initial_capital,
            "maker_fee_bps": maker_fee_bps,
            "taker_fee_bps": taker_fee_bps,
            "mark_haircut_bps": mark_haircut_bps,
            "min_candles": min_candles,
            "min_closes": min_closes,
            "max_ending_inventory_pct": max_ending_inventory_pct,
            "max_drawdown_pct": max_drawdown_pct,
            "enable_inventory_sweeps": enable_inventory_sweeps,
            "sweep_min_inventory_net_bps": sweep_min_inventory_net_bps,
            "sweep_max_inventory_pct": sweep_max_inventory_pct,
            "sweep_cooldown_candles": sweep_cooldown_candles,
            "force_final_liquidation": force_final_liquidation,
            "recenter_when_flat": recenter_when_flat,
        },
        "summary": {
            "products_loaded": len(candle_map),
            "products_selected": len(selected),
            "configs_tested": len(rows),
            "qualified_configs": len(qualified),
            "qualified_products": len({row["product_id"] for row in qualified}),
            "best_score": rows[0]["score"] if rows else 0.0,
            "best_return_pct": rows[0]["final_return_pct"] if rows else 0.0,
        },
        "rows": rows,
        "best_by_product": best_rows,
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "product_id",
        "qualified",
        "profitable",
        "score",
        "final_return_pct",
        "exit_model",
        "spacing_bps",
        "levels",
        "entry_offset_mult",
        "closes",
        "target_closes",
        "sweep_closes",
        "sweep_count",
        "buys",
        "open_positions",
        "open_inventory_pct",
        "max_drawdown_pct",
        "max_inventory_pct",
        "realized_net_usd",
        "final_equity",
        "fees_usd",
        "candles",
        "span_minutes",
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def render_md(payload: dict[str, Any], *, top: int) -> str:
    summary = payload["summary"]
    params = payload["parameters"]
    lines = [
        "# Kraken Grid Optimizer",
        "",
        f"- Generated: `{payload['generated_at']}`",
        f"- Source: `{params['source']}` from `{params['cache_path']}`",
        f"- Products selected: `{summary['products_selected']}` of `{summary['products_loaded']}` loaded",
        f"- Configs tested: `{summary['configs_tested']}`",
        f"- Qualified configs: `{summary['qualified_configs']}` across `{summary['qualified_products']}` products",
        f"- Fees: maker `{params['maker_fee_bps']}` bps, taker/liquidation `{params['taker_fee_bps']}` bps, mark haircut `{params['mark_haircut_bps']}` bps",
        f"- Qualification gate: closes `>= {params['min_closes']}`, ending inventory `<= {params['max_ending_inventory_pct']}%`, max drawdown `<= {params['max_drawdown_pct']}%`",
        f"- Inventory sweeps: `{params['enable_inventory_sweeps']}`, min sweep net `{params['sweep_min_inventory_net_bps']}` bps, trigger inventory `>= {params['sweep_max_inventory_pct']}%`, final liquidation `{params['force_final_liquidation']}`",
        "",
        "## Top Configurations",
        "",
        "| Rank | Product | Qualified | Score | Return % | Exit | Spacing bps | Levels | Closes | Sweeps | Buys | Open Inv % | Max DD % |",
        "|---:|---|---:|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for idx, row in enumerate(payload["rows"][:top], start=1):
        lines.append(
            "| {idx} | {product_id} | {qualified} | {score:.4f} | {final_return_pct:+.4f} | {exit_model} | {spacing_bps:.0f} | {levels} | {closes} | {sweep_count} | {buys} | {open_inventory_pct:.2f} | {max_drawdown_pct:.2f} |".format(
                idx=idx,
                **row,
            )
        )
    lines.extend([
        "",
        "## Interpretation",
        "",
        "- `qualified=true` means positive final equity, at least the configured minimum completed closes, bounded ending inventory, and bounded drawdown.",
        "- Final equity includes open inventory marked at the last close after taker liquidation fee plus haircut.",
        "- Inventory sweeps convert all open inventory back to cash only when liquidation value clears the configured net threshold.",
        "- The simulator processes exits before new entries per candle, avoiding same-candle buy/sell wins where the wick order is unknown.",
        "- This is still a candle-level optimizer; candidates need live order-book and maker-fill replay before real capital.",
        "",
    ])
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Optimize conservative grid trading configs across cached Kraken spot products.")
    parser.add_argument("--cache-path", default=str(DEFAULT_CACHE_PATH))
    parser.add_argument("--products", default="", help="Comma-separated product filter such as DUCK-USD,BILLY-USD. Empty means all.")
    parser.add_argument("--quote-currencies", default=DEFAULT_QUOTES)
    parser.add_argument("--spacings-bps", default=DEFAULT_SPACINGS_BPS)
    parser.add_argument("--levels", default=DEFAULT_LEVELS)
    parser.add_argument("--entry-offset-mults", default=DEFAULT_ENTRY_OFFSETS)
    parser.add_argument("--exit-models", default=DEFAULT_EXIT_MODELS, help="Comma-separated maker,taker")
    parser.add_argument("--initial-capital", type=float, default=100.0)
    parser.add_argument("--maker-fee-bps", type=float, default=DEFAULT_MAKER_FEE_BPS)
    parser.add_argument("--taker-fee-bps", type=float, default=DEFAULT_TAKER_FEE_BPS)
    parser.add_argument("--mark-haircut-bps", type=float, default=10.0)
    parser.add_argument("--min-candles", type=int, default=120)
    parser.add_argument("--min-closes", type=int, default=3)
    parser.add_argument("--max-ending-inventory-pct", type=float, default=25.0)
    parser.add_argument("--max-drawdown-pct", type=float, default=15.0)
    parser.add_argument("--enable-inventory-sweeps", action="store_true")
    parser.add_argument("--sweep-min-inventory-net-bps", type=float, default=0.0)
    parser.add_argument("--sweep-max-inventory-pct", type=float, default=50.0)
    parser.add_argument("--sweep-cooldown-candles", type=int, default=0)
    parser.add_argument("--force-final-liquidation", action="store_true")
    parser.add_argument("--no-recenter-when-flat", action="store_true")
    parser.add_argument("--json-path", default=str(DEFAULT_JSON_PATH))
    parser.add_argument("--csv-path", default=str(DEFAULT_CSV_PATH))
    parser.add_argument("--md-path", default=str(DEFAULT_MD_PATH))
    parser.add_argument("--top", type=int, default=50)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    products = {part.strip().upper() for part in args.products.split(",") if part.strip()}
    quote_currencies = {part.strip().upper() for part in args.quote_currencies.split(",") if part.strip()}
    exit_models = [part.strip().lower() for part in args.exit_models.split(",") if part.strip()]
    for model in exit_models:
        if model not in {"maker", "taker"}:
            raise SystemExit(f"Unsupported exit model: {model}")

    payload = build_payload(
        cache_path=Path(args.cache_path),
        products=products,
        quote_currencies=quote_currencies,
        spacings_bps=parse_float_csv(args.spacings_bps),
        levels_values=parse_int_csv(args.levels),
        entry_offset_mults=parse_float_csv(args.entry_offset_mults),
        exit_models=exit_models,
        initial_capital=float(args.initial_capital),
        maker_fee_bps=float(args.maker_fee_bps),
        taker_fee_bps=float(args.taker_fee_bps),
        mark_haircut_bps=float(args.mark_haircut_bps),
        min_candles=int(args.min_candles),
        min_closes=int(args.min_closes),
        max_ending_inventory_pct=float(args.max_ending_inventory_pct),
        max_drawdown_pct=float(args.max_drawdown_pct),
        enable_inventory_sweeps=bool(args.enable_inventory_sweeps),
        sweep_min_inventory_net_bps=float(args.sweep_min_inventory_net_bps),
        sweep_max_inventory_pct=float(args.sweep_max_inventory_pct),
        sweep_cooldown_candles=int(args.sweep_cooldown_candles),
        force_final_liquidation=bool(args.force_final_liquidation),
        recenter_when_flat=not args.no_recenter_when_flat,
    )

    json_path = Path(args.json_path)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    write_csv(Path(args.csv_path), payload["rows"])
    Path(args.md_path).write_text(render_md(payload, top=int(args.top)), encoding="utf-8")

    print(json.dumps(payload["summary"], indent=2, sort_keys=True))
    print(f"JSON: {args.json_path}")
    print(f"CSV: {args.csv_path}")
    print(f"MD: {args.md_path}")


if __name__ == "__main__":
    main()
