from __future__ import annotations

from collections.abc import Mapping


def get_symbol_family_bucket(symbol, index_family_symbol_keys):
    text = str(symbol or "").upper()
    if any(key in text for key in index_family_symbol_keys):
        return "INDEX"
    return ""


def get_alleyway_mapping(alleyway_state, key):
    value = alleyway_state.get(key)
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def arm_sync_close_reentry_freeze(
    *,
    alleyway_state,
    symbol,
    now,
    symbol_freeze_seconds,
    index_family_freeze_seconds,
    index_family_symbol_keys,
):
    now = float(now)
    symbol = str(symbol or "").upper()
    if not symbol:
        return "", 0, 0

    symbol_freeze_until = get_alleyway_mapping(alleyway_state, "sync_close_reentry_symbol_freeze_until")
    symbol_freeze_until[symbol] = max(
        float(symbol_freeze_until.get(symbol, 0.0) or 0.0),
        now + symbol_freeze_seconds,
    )
    alleyway_state["sync_close_reentry_symbol_freeze_until"] = symbol_freeze_until

    family = get_symbol_family_bucket(symbol, index_family_symbol_keys)
    family_seconds = 0
    if family:
        family_freeze_until = get_alleyway_mapping(alleyway_state, "sync_close_reentry_family_freeze_until")
        family_seconds = index_family_freeze_seconds
        family_freeze_until[family] = max(
            float(family_freeze_until.get(family, 0.0) or 0.0),
            now + family_seconds,
        )
        alleyway_state["sync_close_reentry_family_freeze_until"] = family_freeze_until

    return family, symbol_freeze_seconds, family_seconds


def get_symbol_stress(*, active_positions, symbol, max_positions_per_symbol):
    symbol_positions = [pdata for pdata in active_positions.values() if pdata["symbol"] == symbol]
    if not symbol_positions:
        return {
            "score": 0.0,
            "drawdown_share": 0.0,
            "volume_share": 0.0,
            "position_ratio": 0.0,
            "all_losing": False,
        }

    total_drawdown = sum(max(0.0, -(pdata.get("last_pnl", 0.0) or 0.0)) for pdata in active_positions.values())
    symbol_drawdown = sum(max(0.0, -(pdata.get("last_pnl", 0.0) or 0.0)) for pdata in symbol_positions)

    total_volume = sum(float(pdata.get("volume", 0.0) or 0.0) for pdata in active_positions.values())
    symbol_volume = sum(float(pdata.get("volume", 0.0) or 0.0) for pdata in symbol_positions)

    drawdown_share = (symbol_drawdown / total_drawdown) if total_drawdown > 0 else 0.0
    volume_share = (symbol_volume / total_volume) if total_volume > 0 else 0.0
    position_ratio = len(symbol_positions) / max(1, max_positions_per_symbol)
    all_losing = all((pdata.get("last_pnl", 0.0) or 0.0) <= 0 for pdata in symbol_positions)

    score = 0.0
    score += min(1.5, drawdown_share * 1.6)
    score += min(1.0, volume_share * 1.2)
    score += min(1.0, position_ratio * 0.8)
    if all_losing:
        score += 0.35

    return {
        "score": score,
        "drawdown_share": drawdown_share,
        "volume_share": volume_share,
        "position_ratio": position_ratio,
        "all_losing": all_losing,
    }


def get_anchor_drag_state(*, active_positions, anchor_drag_min_loss_usd, anchor_drag_min_drawdown_share):
    total_drawdown = 0.0
    adopted_drawdown_by_symbol = {}
    adopted_count_by_symbol = {}
    all_positions_by_symbol = {}

    for pdata in active_positions.values():
        symbol = str(pdata.get("symbol") or "UNKNOWN")
        pnl = float(pdata.get("last_pnl", 0.0) or 0.0)
        all_positions_by_symbol.setdefault(symbol, []).append(pdata)
        loss_abs = max(0.0, -pnl)
        total_drawdown += loss_abs
        if pdata.get("adopted") and loss_abs > 0.0:
            adopted_drawdown_by_symbol[symbol] = adopted_drawdown_by_symbol.get(symbol, 0.0) + loss_abs
            adopted_count_by_symbol[symbol] = adopted_count_by_symbol.get(symbol, 0) + 1

    if not adopted_drawdown_by_symbol or total_drawdown <= 0.0:
        return {
            "active": False,
            "symbol": "",
            "loss_abs": 0.0,
            "drawdown_share": 0.0,
            "adopted_positions": 0,
            "all_losing": False,
        }

    symbol, loss_abs = max(adopted_drawdown_by_symbol.items(), key=lambda item: item[1])
    drawdown_share = loss_abs / total_drawdown if total_drawdown > 0.0 else 0.0
    symbol_positions = all_positions_by_symbol.get(symbol, [])
    all_losing = bool(symbol_positions) and all(
        float(pdata.get("last_pnl", 0.0) or 0.0) <= 0.0 for pdata in symbol_positions
    )

    return {
        "active": (
            loss_abs >= anchor_drag_min_loss_usd
            and drawdown_share >= anchor_drag_min_drawdown_share
            and adopted_count_by_symbol.get(symbol, 0) > 0
            and all_losing
        ),
        "symbol": symbol,
        "loss_abs": loss_abs,
        "drawdown_share": drawdown_share,
        "adopted_positions": int(adopted_count_by_symbol.get(symbol, 0) or 0),
        "all_losing": all_losing,
    }


def allow_experimental_cluster_improvement_relief(
    *,
    entry_posture,
    symbol_positions,
    symbol_stress,
    regime,
    confidence,
    experimental_mode_floor,
    current_experimental_pair_slot,
    current_experimental_regime_positions,
    book_stress,
    free_margin_ratio,
    profit_buffer_available,
    financed_crowd_relief_min_free_margin_ratio,
    financed_crowd_block_max_buffer_usd,
    financed_crowd_block_min_symbol_positions,
    financed_crowd_relief_max_stress_score,
    financed_crowd_relief_max_drawdown_share,
    financed_crowd_relief_min_confidence_buffer,
    rearm_recovery_experimental_relief_min_confidence,
):
    if regime not in {"PRICE", "RAW", "GEMINI"}:
        return False
    if not current_experimental_pair_slot or current_experimental_regime_positions > 0:
        return False
    if entry_posture != "REARM":
        return False
    if int((book_stress or {}).get("direct_positions", 0) or 0) != 0:
        return False
    if int((book_stress or {}).get("adopted_positions", 0) or 0) <= 0:
        return False
    if float(free_margin_ratio or 0.0) < financed_crowd_relief_min_free_margin_ratio:
        return False
    if float(profit_buffer_available or 0.0) > financed_crowd_block_max_buffer_usd:
        return False

    same_symbol_direct_positions = sum(1 for pdata in symbol_positions if not pdata.get("adopted", False))
    same_symbol_adopted_positions = sum(1 for pdata in symbol_positions if pdata.get("adopted", False))
    if same_symbol_direct_positions > 0:
        return False
    if same_symbol_adopted_positions < financed_crowd_block_min_symbol_positions:
        return False
    if not bool(symbol_stress.get("all_losing")):
        return False

    stress_score = float(symbol_stress.get("score", 0.0) or 0.0)
    drawdown_share = float(symbol_stress.get("drawdown_share", 0.0) or 0.0)
    if stress_score > financed_crowd_relief_max_stress_score:
        return False
    if drawdown_share > financed_crowd_relief_max_drawdown_share:
        return False

    confidence_floor = max(
        float(experimental_mode_floor or 0.0) + financed_crowd_relief_min_confidence_buffer,
        rearm_recovery_experimental_relief_min_confidence,
    )
    return float(confidence or 0.0) >= confidence_floor
