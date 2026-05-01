#!/usr/bin/env python3
"""
Experiment Manager for 500 Unique Strategies Initiative.

Manages the experiment registry, tracks what's been tested,
and plans the next batch of 50 strategies.

Usage:
    python scripts/experiment_manager.py status          # Show current status
    python scripts/experiment_manager.py add <category> <strategy_name>  # Register new strategy
    python scripts/experiment_manager.py next-batch      # Plan next 50 strategies
    python scripts/experiment_manager.py report          # Generate summary report
"""

import json
import sys
import os
from datetime import datetime, timezone
from pathlib import Path

REGISTRY_PATH = Path(__file__).parent.parent / "experiment_registry.json"


def load_registry():
    with open(REGISTRY_PATH, "r") as f:
        return json.load(f)


def save_registry(registry):
    with open(REGISTRY_PATH, "w") as f:
        json.dump(registry, f, indent=2)


def status():
    registry = load_registry()
    print(f"\n{'='*60}")
    print(f"500 UNIQUE STRATEGIES EXPERIMENT — STATUS")
    print(f"{'='*60}")
    print(f"Total tested: {registry['total_unique_strategies_tested']} / {registry['total_target']}")
    print(f"Progress: {registry['total_unique_strategies_tested'] / registry['total_target'] * 100:.1f}%")
    print(f"\n{'Category':<20} {'Target':<10} {'Tested':<10} {'Status':<15}")
    print(f"{'-'*55}")
    
    for cat_name, cat_data in registry["strategy_categories"].items():
        print(f"{cat_name:<20} {cat_data['target']:<10} {cat_data['tested']:<10} {cat_data['status']:<15}")
    
    print(f"\n{'='*60}")
    print(f"NEXT BATCH: #{registry['next_batch']['batch_number']}")
    print(f"Status: {registry['next_batch']['status']}")
    print(f"Priority categories: {', '.join(registry['next_batch']['priority_categories'])}")
    print(f"{'='*60}\n")


def add_strategy(category, strategy_name, coins_tested=None, results=None):
    registry = load_registry()
    
    if category not in registry["strategy_categories"]:
        print(f"❌ Category '{category}' not found. Valid categories:")
        for cat in registry["strategy_categories"]:
            print(f"  - {cat}")
        return
    
    # Check if strategy already exists
    if strategy_name in registry["strategy_categories"][category]["strategies"]:
        print(f"⚠️  Strategy '{strategy_name}' already registered in '{category}'")
        return
    
    # Add to category
    registry["strategy_categories"][category]["strategies"].append(strategy_name)
    registry["strategy_categories"][category]["tested"] += 1
    registry["total_unique_strategies_tested"] += 1
    
    # Add experiment record
    experiment = {
        "id": len(registry["experiments"]) + 1,
        "strategy": strategy_name,
        "category": category,
        "coins_tested": coins_tested or [],
        "results": results or {},
        "registered_at": datetime.now(timezone.utc).isoformat(),
        "status": "registered"
    }
    registry["experiments"].append(experiment)
    
    save_registry(registry)
    print(f"✅ Added '{strategy_name}' to '{category}' (Total: {registry['total_unique_strategies_tested']}/{registry['total_target']})")


def next_batch():
    registry = load_registry()
    
    # Find categories with most room
    categories_by_need = sorted(
        registry["strategy_categories"].items(),
        key=lambda x: x[1]["target"] - x[1]["tested"],
        reverse=True
    )
    
    print(f"\n{'='*60}")
    print(f"NEXT BATCH PLANNER — Batch #{registry['next_batch']['batch_number']}")
    print(f"{'='*60}")
    print(f"\nRecommended 50 strategies to test next:\n")
    
    batch = []
    remaining = 50
    
    for cat_name, cat_data in categories_by_need:
        if remaining <= 0:
            break
        
        slots_available = cat_data["target"] - cat_data["tested"]
        strategies_to_add = min(slots_available, remaining)
        
        # Generate strategy names for this category
        existing = set(cat_data["strategies"])
        suggested = suggest_strategies(cat_name, strategies_to_add, existing)
        
        for strategy in suggested:
            if strategy not in existing:
                batch.append({"category": cat_name, "strategy": strategy})
                remaining -= 1
                if remaining <= 0:
                    break
    
    print(f"{'Strategy':<30} {'Category':<20}")
    print(f"{'-'*50}")
    for item in batch:
        print(f"{item['strategy']:<30} {item['category']:<20}")
    
    print(f"\nTotal: {len(batch)} strategies")
    print(f"{'='*60}\n")
    
    # Save batch plan
    registry["next_batch"]["planned_strategies"] = batch
    registry["next_batch"]["status"] = "planned"
    save_registry(registry)


def suggest_strategies(category, count, existing):
    """Suggest strategy names for a category."""
    suggestions = {
        "mean_reversion": [
            "zscore_reversion", "stochastic_reversion", "cci_reversion",
            "williams_r_reversion", "roc_reversion", "pairs_trading",
            "cointegration", "kalman_filter", "hmean_reversion",
            "distance_method", "statistical_arbitrage", "ornstein_uhlenbeck",
            "half_life_mean_reversion", "hurst_exponent", "variance_ratio",
            "autocorrelation_reversion", "regression_reversion",
            "bollinger_band_width", "keltner_reversion", "donchian_reversion",
            "fibonacci_reversion", "pivot_point_reversion", "vwap_reversion",
            "anchored_vwap_reversion", "standard_deviation_reversion",
            "mad_reversion", "percentile_channel", "rank_reversion",
            "cross_sectional_reversion", "sector_reversion",
            "beta_reversion", "correlation_reversion", "spread_reversion",
            "ratio_reversion", "residual_reversion", "factor_reversion",
            "momentum_reversion_hybrid", "breakout_reversion_hybrid",
            "volume_reversion", "volatility_reversion_hybrid",
            "time_decay_reversion", "weighted_reversion",
            "adaptive_reversion", "dynamic_reversion", "regime_switching_reversion",
            "machine_learning_reversion", "pattern_reversion",
            "seasonal_reversion", "cycle_reversion", "wavelet_reversion"
        ],
        "momentum": [
            "macd_momentum", "adx_momentum", "supertrend", "psar_momentum",
            "ichimoku_momentum", "dmi_momentum", "aroon_momentum",
            "tsi_momentum", "ultimate_oscillator", "chaikin_oscillator",
            "money_flow_momentum", "force_index", "rate_of_change",
            "detrended_price", "trix_momentum", "kst_momentum",
            "euler_fibonacci_momentum", "schaff_trend_cycle",
            "rainbow_oscillator", "true_strength", "price_momentum",
            "volume_momentum", "volatility_momentum", "cross_momentum",
            "relative_momentum", "absolute_momentum", "time_series_momentum",
            "dual_momentum", "sector_momentum", "factor_momentum",
            "trend_following", "moving_avg_crossover", "ema_ribbon",
            "guppy_mma", "alligator_momentum", "fractal_momentum",
            "adaptive_momentum", "dynamic_momentum", "regime_momentum",
            "ml_momentum", "ensemble_momentum", "volatility_adjusted_momentum",
            "volume_weighted_momentum", "correlation_momentum",
            "beta_momentum", "residual_momentum", "path_dependent_momentum",
            "asymmetric_momentum", "signed_momentum", "directional_momentum"
        ],
        "breakout": [
            "donchian_breakout", "keltner_breakout", "volatility_breakout",
            "opening_range_breakout", "atr_breakout", "channel_breakout",
            "pivot_breakout", "fibonacci_breakout", "pattern_breakout",
            "volume_breakout", "momentum_breakout", "trend_breakout",
            "false_breakout_reversal", "breakout_pullback", "breakout_continuation",
            "breakout_retest", "breakout_confluence", "multi_timeframe_breakout",
            "breakout_consolidation", "breakout_squeeze", "breakout_expansion",
            "breakout_contraction", "breakout_momentum", "breakout_reversion",
            "breakout_volume", "breakout_volatility", "breakout_trend",
            "breakout_pattern", "breakout_signal", "breakout_confirmation",
            "breakout_validation", "breakout_filter", "breakout_timing",
            "breakout_entry", "breakout_exit", "breakout_risk",
            "breakout_position_sizing", "breakout_portfolio", "breakout_adaptive",
            "breakout_dynamic", "breakout_regime", "breakout_ml",
            "breakout_ensemble", "breakout_hybrid", "breakout_multi_asset",
            "breakout_cross_sectional", "breakout_statistical", "breakout_quantitative"
        ],
        "volatility": [
            "atr_expansion", "bb_squeeze", "volatility_contraction",
            "volatility_expansion", "historical_volatility", "implied_volatility",
            "volatility_ratio", "volatility_breakout", "volatility_reversion",
            "volatility_momentum", "volatility_trend", "volatility_pattern",
            "volatility_signal", "volatility_filter", "volatility_timing",
            "volatility_entry", "volatility_exit", "volatility_risk",
            "volatility_position_sizing", "volatility_portfolio", "volatility_adaptive",
            "volatility_dynamic", "volatility_regime", "volatility_ml",
            "volatility_ensemble", "volatility_hybrid", "volatility_multi_asset",
            "volatility_cross_sectional", "volatility_statistical", "volatility_quantitative",
            "volatility_cycle", "volatility_seasonal", "volatility_wavelet",
            "volatility_fourier", "volatility_garch", "volatility_stochastic",
            "volatility_markov", "volatility_regime_switching", "volatility_threshold",
            "volatility_percentile", "volatility_rank", "volatility_zscore",
            "volatility_mad", "volatility_iqr", "volatility_range",
            "volatility_parkinson", "volatility_garman_klass", "volatility_yang_zhang",
            "volatility_rogers_satchell"
        ],
        "volume": [
            "obv", "vwap", "volume_spike", "volume_surge",
            "volume_profile", "volume_weighted", "volume_momentum",
            "volume_trend", "volume_pattern", "volume_signal",
            "volume_filter", "volume_timing", "volume_entry",
            "volume_exit", "volume_risk", "volume_position_sizing",
            "volume_portfolio", "volume_adaptive", "volume_dynamic",
            "volume_regime", "volume_ml", "volume_ensemble",
            "volume_hybrid", "volume_multi_asset", "volume_cross_sectional",
            "volume_statistical", "volume_quantitative", "volume_cycle",
            "volume_seasonal", "volume_wavelet", "volume_fourier",
            "volume_garch", "volume_stochastic", "volume_markov",
            "volume_regime_switching", "volume_threshold", "volume_percentile",
            "volume_rank", "volume_zscore", "volume_mad",
            "volume_iqr", "volume_range", "volume_parkinson",
            "volume_garman_klass", "volume_yang_zhang", "volume_rogers_satchell",
            "volume_cmf", "volume_mfi", "volume_ad"
        ],
        "candle_patterns": [
            "engulfing", "hammer", "doji", "inside_bar",
            "three_bar", "morning_star", "evening_star", "three_white_soldiers",
            "three_black_crows", "shooting_star", "hanging_man", "inverted_hammer",
            "spinning_top", "marubozu", "harami", "harami_cross",
            "piercing_line", "dark_cloud_cover", "twilight", "three_outside",
            "three_inside", "three_stars", "three_mountains", "three_rivers",
            "blockade", "advance_block", "stalled_pattern", "separating_lines",
            "matching_low", "unique_three", "breakaway", "counterattack",
            "thrusting", "in_on_neck", "tasuki_gap", "closing_marubozu",
            "concealing", "abandoned_baby", "dragonfly_doji", "gravestone_doji",
            "long_legged_doji", "rickshaw_man", "high_wave", "spinning_top",
            "falling_three", "rising_three", "upside_gap", "downside_gap",
            "kicking", "kicking_by_length", "belt_hold", "capture"
        ],
        "statistical": [
            "linear_regression", "polynomial_regression", "logistic_regression",
            "ridge_regression", "lasso_regression", "elastic_net",
            "principal_component", "factor_analysis", "cluster_analysis",
            "discriminant_analysis", "canonical_correlation", "manova",
            "time_series_decomposition", "fourier_analysis", "wavelet_analysis",
            "spectral_analysis", "cepstral_analysis", "homomorphic_analysis",
            "independent_component", "slow_feature_analysis", "nonlinear_pca",
            "kernel_pca", "sparse_pca", "robust_pca",
            "incremental_pca", "mini_batch_pca", "randomized_pca",
            "truncated_svd", "dictionary_learning", "feature_agglomeration",
            "birch", "dbscan", "optics",
            "hdbscan", "mean_shift", "affinity_propagation",
            "spectral_clustering", "gaussian_mixture", "bayesian_gaussian_mixture",
            "hidden_markov", "markov_switching", "regime_detection",
            "change_point", "structural_break", "unit_root"
        ],
        "time_based": [
            "session_open", "session_close", "weekly_cycle",
            "monthly_seasonality", "quarterly_cycle", "annual_cycle",
            "turn_of_month", "turn_of_quarter", "turn_of_year",
            "day_of_week", "time_of_day", "hourly_pattern",
            "minute_pattern", "five_minute_pattern", "fifteen_minute_pattern",
            "thirty_minute_pattern", "sixty_minute_pattern", "four_hour_pattern",
            "daily_pattern", "weekly_pattern", "monthly_pattern",
            "seasonal_decomposition", "holiday_effect", "earnings_effect",
            "dividend_effect", "split_effect", "index_rebalance",
            "option_expiry", "futures_roll", "quarter_end",
            "year_end", "january_effect", "santa_rally",
            "halloween_indicator", "sell_in_may", "presidential_cycle",
            "fomc_effect", "cpi_effect", "nfp_effect",
            "macro_announcement", "central_bank", "liquidity_cycle",
            "market_microstructure", "order_flow", "trade_flow",
            "tick_data", "volume_profile_time", "vwap_time"
        ],
        "cross_asset": [
            "btc_beta", "eth_correlation", "sector_rotation",
            "market_cap_rotation", "style_rotation", "factor_rotation",
            "cross_asset_momentum", "cross_asset_reversion", "cross_asset_breakout",
            "cross_asset_volatility", "cross_asset_volume", "cross_asset_pattern",
            "crypto_market", "stock_market", "bond_market",
            "commodity_market", "forex_market", "real_estate",
            "credit_market", "money_market", "derivatives_market",
            "options_market", "futures_market", "spot_market",
            "arbitrage", "statistical_arbitrage", "pairs_trading_cross",
            "triangular_arbitrage", "convergence_trade", "basis_trade",
            "calendar_spread", "inter_commodity", "inter_market",
            "cross_currency", "cross_volatility", "cross_liquidity",
            "cross_momentum", "cross_sentiment", "cross_flow",
            "cross_regime", "cross_cycle", "cross_seasonal",
            "macro_factor", "micro_factor", "style_factor",
            "risk_factor", "return_factor", "volatility_factor",
            "liquidity_factor", "sentiment_factor", "flow_factor"
        ],
        "hybrid": [
            "rsi_volume", "ma_atr", "breakout_volume",
            "momentum_volatility", "mean_reversion_volume", "trend_volume",
            "pattern_volume", "statistical_volume", "time_volume",
            "cross_asset_volume", "rsi_volatility", "ma_volatility",
            "breakout_volatility", "momentum_volume", "mean_reversion_volatility",
            "trend_volatility", "pattern_volatility", "statistical_volatility",
            "time_volatility", "cross_asset_volatility", "rsi_momentum",
            "ma_breakout", "breakout_momentum", "momentum_mean_reversion",
            "trend_pattern", "statistical_time", "cross_asset_momentum",
            "multi_indicator", "multi_timeframe", "multi_asset",
            "multi_factor", "multi_regime", "multi_cycle",
            "ensemble_simple", "ensemble_weighted", "ensemble_adaptive",
            "ensemble_dynamic", "ensemble_ml", "ensemble_deep",
            "hybrid_ml", "hybrid_deep", "hybrid_reinforcement",
            "hybrid_genetic", "hybrid_bayesian", "hybrid_fuzzy",
            "hybrid_neuro", "hybrid_svm", "hybrid_tree",
            "hybrid_forest", "hybrid_boost", "hybrid_stack"
        ]
    }
    
    return suggestions.get(category, [])[:count + 10]  # Return extra to filter


def report():
    registry = load_registry()
    
    print(f"\n{'='*70}")
    print(f"EXPERIMENT REGISTRY REPORT")
    print(f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*70}")
    
    print(f"\n📊 OVERVIEW:")
    print(f"   Total strategies tested: {registry['total_unique_strategies_tested']}")
    print(f"   Target: {registry['total_target']}")
    print(f"   Progress: {registry['total_unique_strategies_tested'] / registry['total_target'] * 100:.1f}%")
    
    print(f"\n📈 BY CATEGORY:")
    for cat_name, cat_data in registry["strategy_categories"].items():
        progress = cat_data["tested"] / cat_data["target"] * 100
        bar = "█" * int(progress / 5) + "░" * (20 - int(progress / 5))
        print(f"   {cat_name:<20} [{bar}] {cat_data['tested']}/{cat_data['target']} ({progress:.0f}%)")
    
    print(f"\n🧪 RECENT EXPERIMENTS:")
    if registry["experiments"]:
        for exp in registry["experiments"][-5:]:
            coins_raw = exp.get("coins_tested", [])
            coins = len(coins_raw) if isinstance(coins_raw, list) else coins_raw
            print(f"   #{exp['id']}: {exp['strategy']} ({exp['category']}) — {coins} coins")
    else:
        print(f"   No experiments registered yet")
    
    print(f"\n{'='*70}\n")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python experiment_manager.py <command> [args]")
        print("Commands: status, add, next-batch, report")
        sys.exit(1)
    
    command = sys.argv[1]
    
    if command == "status":
        status()
    elif command == "add":
        if len(sys.argv) < 4:
            print("Usage: python experiment_manager.py add <category> <strategy_name>")
            sys.exit(1)
        category = sys.argv[2]
        strategy_name = sys.argv[3]
        add_strategy(category, strategy_name)
    elif command == "next-batch":
        next_batch()
    elif command == "report":
        report()
    else:
        print(f"Unknown command: {command}")
        sys.exit(1)
