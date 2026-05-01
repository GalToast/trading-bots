#!/usr/bin/env python3
"""
Statistical 50 Strategy Sweep — Quant/Stat edge discovery.

Tests 50 unique statistical/quantitative strategies across 35 Coinbase coins on 7d data
for fast edge discovery. Top candidates get promoted for 30d validation.

Strategy variants cover:
- Regression-based (linear, polynomial, ridge, lasso, elastic net, bayesian, robust)
- Spectral analysis (FFT, wavelet, Fourier, spectral density)
- Time-series properties (Hurst, unit root, ACF, PACF, variance ratio)
- Statistical tests (runs test, change point, Granger causality)
- Stochastic models (Markov switching, HMM, OU process, GARCH)
- Advanced decomposition (STL, PCA, factor analysis, copula)
- Information theory (entropy, mutual information, transfer entropy)
- Non-parametric (LOESS, spline, cluster analysis)

Uses the shared strategy_library.py engine with 40bps fees, $48 start.
"""

import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient
from strategy_library import backtest

# ==========================================
# STATISTICAL HELPER FUNCTIONS
# ==========================================

def linear_regression(x, y):
    """Simple OLS linear regression. Returns (slope, intercept, r_squared)."""
    n = len(x)
    if n < 3:
        return 0, 0, 0
    sx = sum(x)
    sy = sum(y)
    sxy = sum(xi * yi for xi, yi in zip(x, y))
    sxx = sum(xi * xi for xi in x)
    denom = n * sxx - sx * sx
    if denom == 0:
        return 0, sy / n, 0
    slope = (n * sxy - sx * sy) / denom
    intercept = (sy - slope * sx) / n
    y_pred = [slope * xi + intercept for xi in x]
    ss_res = sum((yi - yp) ** 2 for yi, yp in zip(y, y_pred))
    y_mean = sy / n
    ss_tot = sum((yi - y_mean) ** 2 for yi in y)
    r_sq = 1 - ss_res / ss_tot if ss_tot > 0 else 0
    return slope, intercept, r_sq


def polynomial_regression_2(y):
    """2nd order polynomial fit: y = a*x^2 + b*x + c. Returns coefficients."""
    n = len(y)
    if n < 5:
        return 0, 0, 0
    x = list(range(n))
    sx = sum(x)
    sx2 = sum(xi * xi for xi in x)
    sx3 = sum(xi ** 3 for xi in x)
    sx4 = sum(xi ** 4 for xi in x)
    sy = sum(y)
    sxy = sum(xi * yi for xi, yi in zip(x, y))
    sx2y = sum(xi * xi * yi for xi, yi in zip(x, y))
    # Normal equations for quadratic: [n, sx, sx2; sx, sx2, sx3; sx2, sx3, sx4] * [c,b,a] = [sy, sxy, sx2y]
    # Solve 3x3 system via Cramer's rule (simplified)
    m0 = [n, sx, sx2, sy]
    m1 = [sx, sx2, sx3, sxy]
    m2 = [sx2, sx3, sx4, sx2y]

    def det3(a0, a1, a2, b0, b1, b2, c0, c1, c2):
        return a0 * (b1 * c2 - b2 * c1) - a1 * (b0 * c2 - b2 * c0) + a2 * (b0 * c1 - b1 * c0)

    D = det3(m0[0], m0[1], m0[2], m1[0], m1[1], m1[2], m2[0], m2[1], m2[2])
    if abs(D) < 1e-12:
        return 0, 0, 0
    Dc = det3(m0[3], m0[1], m0[2], m1[3], m1[1], m1[2], m2[3], m2[1], m2[2])
    Db = det3(m0[0], m0[3], m0[2], m1[0], m1[3], m1[2], m2[0], m2[3], m2[2])
    Da = det3(m0[0], m0[1], m0[3], m1[0], m1[1], m1[3], m2[0], m2[1], m2[3])

    a = Da / D
    b = Db / D
    c = Dc / D
    return a, b, c


def compute_hurst(closes, max_lag=20):
    """Rescaled range (R/S) Hurst exponent estimation (simplified)."""
    n = len(closes)
    if n < max_lag * 3:
        return 0.5
    returns = [closes[i] / closes[i - 1] - 1 for i in range(1, n) if closes[i - 1] != 0]
    if len(returns) < max_lag * 2:
        return 0.5
    lags = [max_lag // 4, max_lag // 2, max_lag]
    lags = [l for l in lags if l >= 5 and len(returns) >= l * 2]
    if len(lags) < 2:
        return 0.5
    log_rs = []
    for lag in lags:
        num_chunks = len(returns) // lag
        if num_chunks < 2:
            continue
        rs_vals = []
        for chunk in range(num_chunks):
            sub = returns[chunk * lag:(chunk + 1) * lag]
            mean_r = sum(sub) / len(sub)
            dev = [sub[i] - mean_r for i in range(len(sub))]
            cum_dev = [sum(dev[:i + 1]) for i in range(len(dev))]
            r = max(cum_dev) - min(cum_dev)
            s = math.sqrt(sum(d * d for d in sub) / len(sub)) if sub else 0
            if s > 0 and r > 0:
                rs_vals.append(r / s)
        if rs_vals:
            avg_rs = sum(rs_vals) / len(rs_vals)
            if avg_rs > 0:
                log_rs.append((math.log(lag), math.log(avg_rs)))
    if len(log_rs) < 2:
        return 0.5
    _, _, r_sq = linear_regression([l[0] for l in log_rs], [l[1] for l in log_rs])
    slope, _, _ = linear_regression([l[0] for l in log_rs], [l[1] for l in log_rs])
    return max(0.0, min(1.0, slope))


def compute_fft(closes):
    """Simplified FFT-like DFT for dominant frequency detection."""
    n = len(closes)
    if n < 20:
        return None, None
    returns = [closes[i] / closes[i - 1] - 1 for i in range(1, n) if closes[i - 1] != 0]
    n_ret = len(returns)
    if n_ret < 10:
        return None, None
    # Compute DFT magnitude for a few frequencies
    max_freq = n_ret // 2
    magnitudes = []
    for k in range(1, min(max_freq, 15)):
        re = sum(returns[t] * math.cos(2 * math.pi * k * t / n_ret) for t in range(n_ret))
        im = sum(returns[t] * math.sin(2 * math.pi * k * t / n_ret) for t in range(n_ret))
        mag = math.sqrt(re * re + im * im) / n_ret
        magnitudes.append((k, mag))
    if not magnitudes:
        return None, None
    dominant = max(magnitudes, key=lambda x: x[1])
    dominant_freq = dominant[0]
    dominant_mag = dominant[1]
    # Current phase at dominant frequency
    re_now = sum(returns[t] * math.cos(2 * math.pi * dominant_freq * t / n_ret) for t in range(n_ret))
    im_now = sum(returns[t] * math.sin(2 * math.pi * dominant_freq * t / n_ret) for t in range(n_ret))
    phase = math.atan2(im_now, re_now)
    return dominant_freq, phase


def compute_wavelet(closes, level=3):
    """Simplified Haar wavelet decomposition."""
    n = len(closes)
    if n < 8:
        return []
    signal = list(closes)
    details = []
    current = signal
    for _ in range(min(level, int(math.log2(n)) - 1)):
        if len(current) < 2:
            break
        approx = []
        detail = []
        for i in range(0, len(current) - 1, 2):
            avg = (current[i] + current[i + 1]) / 2
            diff = (current[i] - current[i + 1]) / 2
            approx.append(avg)
            detail.append(diff)
        details.append(detail)
        current = approx
    return details


def compute_psd(closes):
    """Power Spectral Density (simplified periodogram)."""
    n = len(closes)
    if n < 20:
        return []
    returns = [closes[i] / closes[i - 1] - 1 for i in range(1, n) if closes[i - 1] != 0]
    n_ret = len(returns)
    if n_ret < 10:
        return []
    mean_r = sum(returns) / n_ret
    centered = [r - mean_r for r in returns]
    psd = []
    for k in range(1, min(n_ret // 2, 20)):
        re = sum(centered[t] * math.cos(2 * math.pi * k * t / n_ret) for t in range(n_ret))
        im = sum(centered[t] * math.sin(2 * math.pi * k * t / n_ret) for t in range(n_ret))
        power = (re * re + im * im) / n_ret
        psd.append((k, power))
    return psd


def compute_acf(data, max_lag=10):
    """Autocorrelation function."""
    n = len(data)
    if n < max_lag + 5:
        return []
    mean = sum(data) / n
    var = sum((x - mean) ** 2 for x in data) / n
    if var == 0:
        return [1.0] + [0.0] * max_lag
    acf = [1.0]
    for lag in range(1, max_lag + 1):
        cov = sum((data[i] - mean) * (data[i + lag] - mean) for i in range(n - lag)) / n
        acf.append(cov / var)
    return acf


def compute_pacf(data, max_lag=5):
    """Partial autocorrelation function (Durbin-Levinson approximation)."""
    acf = compute_acf(data, max_lag)
    if len(acf) < 2:
        return []
    pacf = [1.0]
    for k in range(1, min(len(acf), max_lag + 1)):
        if k == 1:
            pacf.append(acf[1])
        else:
            # Simplified: use last ACf value as PACF approx
            pacf.append(acf[k])
    return pacf


def compute_returns(closes):
    """Simple returns series."""
    return [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes)) if closes[i - 1] != 0]


def compute_log_returns(closes):
    """Log returns series."""
    rets = []
    for i in range(1, len(closes)):
        if closes[i - 1] > 0:
            r = math.log(closes[i] / closes[i - 1])
            rets.append(r)
    return rets


def k_means_1d(data, k=2, max_iter=20):
    """Simple 1D k-means clustering."""
    if len(data) < k:
        return [0] * len(data), []
    centers = sorted(data)[:k] if len(data) >= k else data
    labels = [0] * len(data)
    for _ in range(max_iter):
        new_labels = []
        for x in data:
            dists = [abs(x - c) for c in centers]
            new_labels.append(dists.index(min(dists)))
        if new_labels == labels:
            break
        labels = new_labels
        centers = [
            sum(data[i] for i in range(len(data)) if labels[i] == j) / max(1, sum(1 for l in labels if l == j))
            for j in range(k)
        ]
    return labels, centers


def compute_adf_stat(data, max_lag=5):
    """Simplified ADF test statistic (no critical values, just the t-stat)."""
    n = len(data)
    if n < max_lag + 10:
        return 0
    delta_y = [data[i] - data[i - 1] for i in range(1, n)]
    y_lag = data[:-1]
    # Augment with lagged differences
    effective_n = len(delta_y)
    # Simple regression: delta_y[t] = alpha * y[t-1] + error
    x = y_lag[-effective_n:]
    y = delta_y[-effective_n:]
    slope, _, _ = linear_regression(x, y)
    # SE of slope
    if effective_n < 5:
        return 0
    y_pred = [slope * xi for xi in x]
    ss_res = sum((yi - yp) ** 2 for yi, yp in zip(y, y_pred))
    se = math.sqrt(ss_res / (effective_n - 1)) if effective_n > 1 else 1
    sx2 = sum(xi * xi for xi in x)
    se_slope = se / math.sqrt(sx2) if sx2 > 0 else 1
    t_stat = slope / se_slope if se_slope > 0 else 0
    return t_stat


def compute_garch11(returns):
    """Simplified GARCH(1,1) fit via method of moments."""
    n = len(returns)
    if n < 20:
        return None, None, None, None
    mean = sum(returns) / n
    var = sum((r - mean) ** 2 for r in returns) / n
    if var == 0:
        return 0, 0, 0, var
    # Squared returns autocorrelation for persistence
    sq = [(r - mean) ** 2 for r in returns]
    acf1 = compute_acf(sq, 1)
    persistence = acf1[1] if len(acf1) > 1 else 0.5
    omega = var * (1 - persistence)
    alpha = 0.05
    beta = persistence - alpha
    beta = max(0, min(beta, 0.95))
    alpha = max(0, min(alpha, 0.95 - beta))
    # Current conditional variance estimate
    if len(sq) >= 2:
        current_var = omega + alpha * sq[-1] + beta * var
    else:
        current_var = var
    return omega, alpha, beta, current_var


def kalman_filter_1d(observations):
    """Simple 1D Kalman filter for trend estimation."""
    n = len(observations)
    if n < 3:
        return observations
    x = observations[0]  # state
    P = 1.0  # covariance
    Q = 0.01  # process noise
    R = 0.1  # measurement noise
    filtered = []
    for z in observations:
        # Predict
        x_pred = x
        P_pred = P + Q
        # Update
        K = P_pred / (P_pred + R)  # Kalman gain
        x = x_pred + K * (z - x_pred)
        P = (1 - K) * P_pred
        filtered.append(x)
    return filtered


def compute_ou_params(closes):
    """Ornstein-Uhlenbeck process parameter estimation."""
    n = len(closes)
    if n < 10:
        return None, None, None
    # Use returns as OU process proxy
    x = closes
    dt = 1.0
    # Simplified: fit mean-reversion speed
    x_mean = sum(x) / n
    dx = [x[i] - x[i - 1] for i in range(1, n)]
    x_lag = x[:-1]
    # dx = theta * (mu - x) * dt + sigma * dW
    # Rearrange: dx/dt = theta*mu - theta*x
    y = [d / dt for d in dx]
    slope, intercept, _ = linear_regression(x_lag, y)
    if slope == 0:
        return None, None, None
    theta = -slope  # mean reversion speed
    mu = -intercept / slope  # long-term mean
    sigma = math.sqrt(sum((y[i] - (intercept + slope * x_lag[i])) ** 2 for i in range(len(y))) / max(1, len(y) - 2))
    return theta, mu, sigma


def compute_entropy(data, bins=10):
    """Shannon entropy of a data series."""
    if len(data) < 2:
        return 0
    mn = min(data)
    mx = max(data)
    if mx == mn:
        return 0
    bin_width = (mx - mn) / bins
    counts = [0] * bins
    for x in data:
        idx = min(int((x - mn) / bin_width), bins - 1)
        counts[idx] += 1
    n = len(data)
    entropy = 0
    for c in counts:
        if c > 0:
            p = c / n
            entropy -= p * math.log(p)
    return entropy


def compute_mutual_info(x, y, bins=10):
    """Mutual information between two series."""
    if len(x) != len(y) or len(x) < 2:
        return 0
    n = len(x)
    mx, Mx = min(x), max(x)
    my, My = min(y), max(y)
    if Mx == mx or My == my:
        return 0
    bx = (Mx - mx) / bins
    by = (My - my) / bins
    joint = [[0] * bins for _ in range(bins)]
    px = [0] * bins
    py = [0] * bins
    for i in range(n):
        ix = min(int((x[i] - mx) / bx), bins - 1)
        iy = min(int((y[i] - my) / by), bins - 1)
        joint[ix][iy] += 1
        px[ix] += 1
        py[iy] += 1
    mi = 0
    for i in range(bins):
        for j in range(bins):
            if joint[i][j] > 0:
                pij = joint[i][j] / n
                mi += pij * math.log(pij / (px[i] / n * py[j] / n))
    return mi


def robust_mean(data):
    """Huber M-estimator approximation (iteratively reweighted mean)."""
    if not data:
        return 0
    mu = sum(data) / len(data)
    sigma = max(1e-8, math.sqrt(sum((x - mu) ** 2 for x in data) / len(data)))
    for _ in range(5):
        weights = []
        for x in data:
            u = abs(x - mu) / sigma
            if u <= 1.345:
                weights.append(1.0)
            else:
                weights.append(1.345 / u)
        mu = sum(w * x for w, x in zip(weights, data)) / sum(weights)
    return mu


def loess_smooth(y, span=0.3):
    """Simplified LOESS/LOWESS smoothing (moving average with tricube weights)."""
    n = len(y)
    if n < 5:
        return y
    k = max(3, int(n * span))
    smoothed = []
    for i in range(n):
        left = max(0, i - k // 2)
        right = min(n, i + k // 2 + 1)
        window = y[left:right]
        dists = [abs(j - i) / max(1, k) for j in range(left, right)]
        weights = [(1 - d ** 3) ** 3 for d in dists]
        w_sum = sum(weights)
        if w_sum > 0:
            smoothed.append(sum(w * val for w, val in zip(weights, window)) / w_sum)
        else:
            smoothed.append(y[i])
    return smoothed


def cubic_spline_knots(y, num_knots=5):
    """Simplified cubic spline: return knot positions and values."""
    n = len(y)
    if n < 4:
        return list(range(len(y))), y
    knot_indices = [int(i * (n - 1) / (num_knots - 1)) for i in range(num_knots)]
    knot_indices[0] = 0
    knot_indices[-1] = n - 1
    return knot_indices, [y[i] for i in knot_indices]


def compute_variance_ratio(returns, periods=[2, 5, 10]):
    """Variance ratio test for random walk rejection."""
    n = len(returns)
    if n < max(periods) * 3:
        return {}
    var1 = sum(r * r for r in returns) / n
    results = {}
    for q in periods:
        if n < q * 2:
            continue
        q_returns = [sum(returns[i:i + q]) for i in range(0, n - q + 1, q)]
        var_q = sum(r * r for r in q_returns) / len(q_returns) / q
        results[q] = var_q / var1 if var1 > 0 else 1
    return results


def runs_test(data):
    """Wald-Wolfowitz runs test for randomness."""
    n = len(data)
    if n < 5:
        return 0, 1.0
    median = sorted(data)[n // 2]
    signs = [1 if x > median else 0 for x in data]
    n1 = sum(signs)
    n2 = n - n1
    if n1 == 0 or n2 == 0:
        return 0, 1.0
    runs = 1
    for i in range(1, n):
        if signs[i] != signs[i - 1]:
            runs += 1
    expected = 1 + 2 * n1 * n2 / n
    var = 2 * n1 * n2 * (2 * n1 * n2 - n) / (n * n * (n - 1)) if n > 1 else 1
    z = (runs - expected) / math.sqrt(max(1e-10, var))
    return runs, z


def granger_test(y, x, max_lag=3):
    """Simplified Granger causality test (F-stat approximation)."""
    n = len(y)
    if n < max_lag * 3 or n != len(x):
        return 0
    # Restricted model: y[t] = a + sum(b_i * y[t-i])
    # Unrestricted: y[t] = a + sum(b_i * y[t-i]) + sum(c_i * x[t-i])
    restricted_ss = 0
    unrestricted_ss = 0
    count = 0
    for t in range(max_lag, n):
        y_lags = [y[t - i - 1] for i in range(max_lag)]
        x_lags = [x[t - i - 1] for i in range(max_lag)]
        # Simple avg prediction
        y_pred_r = sum(y_lags) / max_lag
        # For unrestricted, simple avg of both
        y_pred_u = (sum(y_lags) + sum(x_lags)) / (2 * max_lag)
        restricted_ss += (y[t] - y_pred_r) ** 2
        unrestricted_ss += (y[t] - y_pred_u) ** 2
        count += 1
    if restricted_ss == 0:
        return 0
    f_stat = ((restricted_ss - unrestricted_ss) / max_lag) / (unrestricted_ss / max(1, count - 2 * max_lag - 1))
    return f_stat


def dfa_exponent(closes, min_scale=4, max_scale=None):
    """Detrended Fluctuation Analysis scaling exponent."""
    n = len(closes)
    if n < 20:
        return 0.5
    returns = [closes[i] / closes[i - 1] - 1 for i in range(1, n) if closes[i - 1] != 0]
    n_ret = len(returns)
    if n_ret < 10:
        return 0.5
    y = [sum(returns[:i + 1]) for i in range(n_ret)]  # integrated
    max_scale = max_scale or n_ret // 4
    scales = []
    flucs = []
    for s in range(min_scale, min(max_scale + 1, n_ret // 2), max(1, (max_scale - min_scale) // 4)):
        num_windows = n_ret // s
        if num_windows < 2:
            continue
        fluc = 0
        for w in range(num_windows):
            seg = y[w * s:(w + 1) * s]
            x_seg = list(range(s))
            slope, intercept, _ = linear_regression(x_seg, seg)
            trend = [slope * xi + intercept for xi in x_seg]
            fluc += sum((seg[i] - trend[i]) ** 2 for i in range(s)) / s
        fluc /= num_windows
        fluc = math.sqrt(fluc)
        if fluc > 0:
            scales.append(s)
            flucs.append(fluc)
    if len(scales) < 2:
        return 0.5
    slope, _, _ = linear_regression([math.log(s) for s in scales], [math.log(f) for f in flucs])
    return max(0.0, min(1.5, slope))


def fractal_dimension(closes):
    """Higuchi fractal dimension of price series."""
    n = len(closes)
    if n < 10:
        return 2.0
    k_max = min(n // 3, 10)
    if k_max < 2:
        return 2.0
    l_vals = []
    for k in range(1, k_max + 1):
        l_k = 0
        for m in range(1, k + 1):
            l_mk = sum(abs(closes[m + i * k] - closes[m + (i - 1) * k]) for i in range(1, (n - m) // k + 1))
            l_mk *= (n - 1) / (((n - m) // k) * k)
            l_k += l_mk
        l_k /= k
        l_vals.append(l_k)
    k_vals = list(range(1, k_max + 1))
    valid = [(k, l) for k, l in zip(k_vals, l_vals) if l > 0]
    if len(valid) < 2:
        return 2.0
    slope, _, _ = linear_regression(
        [math.log(k) for k, _ in valid],
        [math.log(l) for _, l in valid]
    )
    return max(1.0, min(2.0, 2 - slope))


def change_point_detect(data, min_seg=5):
    """Simple change point detection via cost function (mean shift)."""
    n = len(data)
    if n < min_seg * 3:
        return -1
    best_cost = float('inf')
    best_cp = -1
    for cp in range(min_seg, n - min_seg):
        left = data[:cp]
        right = data[cp:]
        mu_l = sum(left) / len(left)
        mu_r = sum(right) / len(right)
        cost = sum((x - mu_l) ** 2 for x in left) + sum((x - mu_r) ** 2 for x in right)
        if cost < best_cost:
            best_cost = cost
            best_cp = cp
    return best_cp


def markov_2state(data, max_iter=50):
    """2-state Markov regime switching (simplified Baum-Welch-like)."""
    n = len(data)
    if n < 10:
        return 0
    # Initialize: split by median
    median = sorted(data)[n // 2]
    states = [0 if x <= median else 1 for x in data]
    # Estimate transition matrix
    trans = [[0, 0], [0, 0]]
    for i in range(1, n):
        trans[states[i - 1]][states[i]] += 1
    # Normalize
    for s in range(2):
        total = sum(trans[s])
        if total > 0:
            trans[s] = [t / total for t in trans[s]]
    # Current state probability
    current_state = states[-1]
    # Transition prob to state 1
    p_to_1 = trans[current_state][1]
    return p_to_1


def compute_cross_corr(x, y, max_lag=5):
    """Cross-correlation between two series."""
    n = min(len(x), len(y))
    if n < max_lag + 5:
        return []
    x = x[:n]
    y = y[:n]
    mx = sum(x) / n
    my = sum(y) / n
    sx = math.sqrt(sum((xi - mx) ** 2 for xi in x) / n)
    sy = math.sqrt(sum((yi - my) ** 2 for yi in y) / n)
    if sx == 0 or sy == 0:
        return [0] * (max_lag + 1)
    cc = []
    for lag in range(max_lag + 1):
        cov = sum((x[i] - mx) * (y[i + lag] - my) for i in range(n - lag)) / (n - lag)
        cc.append(cov / (sx * sy))
    return cc


def compute_distance_matrix(returns, window=10):
    """Euclidean distance between recent return windows (pattern matching)."""
    n = len(returns)
    if n < window * 2:
        return []
    windows = []
    for i in range(window, n):
        w = returns[i - window:i]
        windows.append(w)
    if len(windows) < 2:
        return []
    current = windows[-1]
    dists = []
    for w in windows[:-1]:
        d = math.sqrt(sum((a - b) ** 2 for a, b in zip(current, w)))
        dists.append(d)
    return dists


def impulse_response(data, shock_size=1.0, horizon=10):
    """Simplified impulse response function (VAR-like)."""
    n = len(data)
    if n < horizon + 5:
        return []
    # Simple AR(1) approximation
    y = data[1:]
    x = data[:-1]
    phi, _, _ = linear_regression(x, y)
    irf = [shock_size]
    for i in range(1, horizon):
        irf.append(irf[-1] * phi)
    return irf


def compute_copula_simple(x, y):
    """Simplified copula-based dependency measure (rank correlation)."""
    n = min(len(x), len(y))
    if n < 5:
        return 0
    x_r = sorted(range(n), key=lambda i: x[i])
    y_r = sorted(range(n), key=lambda i: y[i])
    # Convert to ranks
    x_rank = [0] * n
    y_rank = [0] * n
    for i, idx in enumerate(x_r):
        x_rank[idx] = i
    for i, idx in enumerate(y_r):
        y_rank[idx] = i
    # Spearman's rho
    d_sq = sum((x_rank[i] - y_rank[i]) ** 2 for i in range(n))
    rho = 1 - 6 * d_sq / (n * (n * n - 1))
    return rho


# ==========================================
# STATISTICAL STRATEGY ENTRY FUNCTIONS
# ==========================================

def _linear_regression_entry(candles_hist, closes, candle, params):
    """Enter when price below regression line and starting to revert."""
    if len(closes) < 30:
        return False
    x = list(range(len(closes)))
    slope, intercept, r_sq = linear_regression(x, closes)
    current_pred = slope * (len(closes) - 1) + intercept
    current_price = closes[-1]
    below_line = current_price < current_pred * 0.998
    reverting = len(closes) > 2 and closes[-1] > closes[-2]
    return below_line and reverting


def _polynomial_regression_entry(candles_hist, closes, candle, params):
    """Enter at polynomial regression inflection point."""
    if len(closes) < 20:
        return False
    a, b, c = polynomial_regression_2(closes)
    if a == 0:
        return False
    # Inflection at x = -b/(2a) for quadratic; enter if near end of series
    inflection_x = -b / (2 * a)
    n = len(closes)
    near_inflection = abs(inflection_x - n) < n * 0.3
    # Price starting to turn
    turning = len(closes) > 2 and closes[-1] > closes[-2]
    return near_inflection and turning


def _pca_signal_entry(candles_hist, closes, candle, params):
    """Enter on first principal component of price/volume flip."""
    if len(candles_hist) < 30:
        return False
    # Simplified PCA: use price and volume as 2 features
    prices = list(closes[-20:])
    volumes = [float(c["volume"]) for c in candles_hist[-20:]]
    # Normalize
    mp = sum(prices) / len(prices)
    sp = max(1e-8, math.sqrt(sum((p - mp) ** 2 for p in prices) / len(prices)))
    mv = sum(volumes) / len(volumes)
    sv = max(1e-8, math.sqrt(sum((v - mv) ** 2 for v in volumes) / len(volumes)))
    z_p = [(p - mp) / sp for p in prices]
    z_v = [(v - mv) / sv for v in volumes]
    # PC1 approx: equal-weighted combo
    pc1 = [zp + zv for zp, zv in zip(z_p, z_v)]
    # Enter when PC1 flips from negative to positive
    if len(pc1) > 2 and pc1[-2] < 0 and pc1[-1] > 0:
        return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _factor_analysis_entry(candles_hist, closes, candle, params):
    """Enter on price momentum + volume factor alignment."""
    if len(candles_hist) < 30:
        return False
    # Momentum factor
    mom_5 = closes[-1] / closes[-6] - 1 if len(closes) > 6 else 0
    mom_10 = closes[-1] / closes[-11] - 1 if len(closes) > 11 else 0
    # Volume factor
    vol_5 = sum(float(c["volume"]) for c in candles_hist[-5:]) / 5
    vol_20 = sum(float(c["volume"]) for c in candles_hist[-20:]) / 20
    vol_factor = vol_5 / vol_20 - 1 if vol_20 > 0 else 0
    # Enter when both factors aligned positively
    if mom_5 > 0 and vol_factor > 0.1:
        return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _cluster_analysis_entry(candles_hist, closes, candle, params):
    """Enter when K-means cluster shifts (returns regime change)."""
    if len(closes) < 40:
        return False
    returns = compute_returns(closes[-40:])
    if len(returns) < 10:
        return False
    labels, centers = k_means_1d(returns, k=2)
    if len(labels) < 5:
        return False
    # Check if recent observations switched cluster
    recent_labels = labels[-5:]
    prev_labels = labels[-15:-5]
    recent_majority = sum(recent_labels) > len(recent_labels) // 2
    prev_majority = sum(prev_labels) > len(prev_labels) // 2
    if recent_majority != prev_majority:
        return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _time_series_decomposition_entry(candles_hist, closes, candle, params):
    """Enter on seasonal component of STL-like decomposition."""
    if len(closes) < 40:
        return False
    n = len(closes)
    # Simple moving average as trend
    period = 10
    trend = []
    for i in range(period // 2, n - period // 2):
        trend.append(sum(closes[i - period // 2:i + period // 2 + 1]) / (period + 1))
    if len(trend) < 5:
        return False
    # Seasonal: price - trend
    seasonal = []
    for i in range(len(trend)):
        idx = i + period // 2
        seasonal.append(closes[idx] - trend[i])
    # Enter at seasonal trough
    if len(seasonal) > 2 and seasonal[-1] < 0 and seasonal[-1] < min(seasonal[-5:-1]):
        return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _fourier_analysis_entry(candles_hist, closes, candle, params):
    """Enter at FFT-detected dominant cycle bottom."""
    if len(closes) < 30:
        return False
    freq, phase = compute_fft(closes)
    if freq is None:
        return False
    # Enter near cycle bottom (phase near -pi/2 or 3pi/2)
    cycle_bottom = phase < -0.5 or phase > 2.5
    if cycle_bottom:
        return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _wavelet_analysis_entry(candles_hist, closes, candle, params):
    """Enter on wavelet compression point."""
    if len(closes) < 30:
        return False
    details = compute_wavelet(closes, level=3)
    if len(details) < 2:
        return False
    # Check finest detail level for compression (low variance)
    finest = details[0]
    if len(finest) < 3:
        return False
    recent_var = sum(d * d for d in finest[-3:]) / 3
    prev_var = sum(d * d for d in finest[:-3]) / max(1, len(finest) - 3)
    compressed = recent_var < prev_var * 0.5
    if compressed:
        return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _spectral_analysis_entry(candles_hist, closes, candle, params):
    """Enter on PSD peak detection (dominant frequency power surge)."""
    if len(closes) < 30:
        return False
    psd = compute_psd(closes)
    if not psd:
        return False
    max_power = max(p for _, p in psd)
    avg_power = sum(p for _, p in psd) / len(psd)
    # Dominant peak
    peak_ratio = max_power / avg_power if avg_power > 0 else 0
    if peak_ratio > 2.0:
        return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _hurst_exponent_entry(candles_hist, closes, candle, params):
    """Enter when H > 0.5 (trending regime)."""
    if len(closes) < 50:
        return False
    h = compute_hurst(closes[-50:])
    if h > 0.55:
        return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _unit_root_entry(candles_hist, closes, candle, params):
    """Enter when ADF test shows series becoming stationary (mean-reversion opportunity)."""
    if len(closes) < 30:
        return False
    t_stat = compute_adf_stat(closes[-30:], max_lag=3)
    # More negative = more stationary
    if t_stat < -2.0:
        return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _change_point_entry(candles_hist, closes, candle, params):
    """Enter after statistical change point detection."""
    if len(closes) < 30:
        return False
    returns = compute_returns(closes[-30:])
    if len(returns) < 15:
        return False
    cp = change_point_detect(returns, min_seg=3)
    if cp < 0:
        return False
    # CP near end = recent regime change
    recent_change = cp > len(returns) * 0.7
    if recent_change:
        return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _markov_switching_entry(candles_hist, closes, candle, params):
    """Enter on 2-state Markov regime change to bullish state."""
    if len(closes) < 30:
        return False
    returns = compute_returns(closes[-30:])
    if len(returns) < 10:
        return False
    p_to_1 = markov_2state(returns)
    # High probability of switching to state 1 (bullish)
    if p_to_1 > 0.6:
        return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _hidden_markov_entry(candles_hist, closes, candle, params):
    """Enter on HMM state transition signal (simplified)."""
    if len(closes) < 40:
        return False
    returns = compute_returns(closes[-40:])
    if len(returns) < 10:
        return False
    # Simplified HMM: use emission probability shift
    # State 0: low returns, State 1: high returns
    median = sorted(returns)[len(returns) // 2]
    states = [0 if r <= median else 1 for r in returns]
    # Transition likelihood
    transitions = sum(1 for i in range(1, len(states)) if states[i] != states[i - 1])
    trans_rate = transitions / len(states)
    # Low transition rate + current in state 1 = stable bullish
    if trans_rate < 0.3 and states[-1] == 1:
        return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _garch_forecast_entry(candles_hist, closes, candle, params):
    """Enter when GARCH(1,1) volatility forecast shows vol declining."""
    if len(closes) < 30:
        return False
    returns = compute_returns(closes[-50:])
    if len(returns) < 20:
        return False
    omega, alpha, beta, current_var = compute_garch11(returns)
    if omega is None:
        return False
    # Compare current conditional var to long-run var
    long_run_var = omega / (1 - alpha - beta) if (1 - alpha - beta) > 0 else current_var
    vol_declining = current_var < long_run_var * 0.9
    if vol_declining:
        return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _kalman_filter_entry(candles_hist, closes, candle, params):
    """Enter on Kalman-filtered trend state change."""
    if len(closes) < 30:
        return False
    filtered = kalman_filter_1d(closes[-30:])
    if len(filtered) < 5:
        return False
    # Trend change: filtered values turning up
    recent_slope = (filtered[-1] - filtered[-3]) / 3
    prev_slope = (filtered[-6] - filtered[-9]) / 3 if len(filtered) > 9 else 0
    turning_up = recent_slope > 0 and recent_slope > prev_slope
    if turning_up:
        return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _ornstein_uhlenbeck_entry(candles_hist, closes, candle, params):
    """Enter on OU process mean reversion at extreme."""
    if len(closes) < 30:
        return False
    theta, mu, sigma = compute_ou_params(closes[-30:])
    if theta is None or theta <= 0:
        return False
    current = closes[-1]
    # Z-score from mean
    z = (current - mu) / sigma if sigma > 0 else 0
    # Enter when price is below mean and starting to revert
    if z < -1.5:
        return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _cointegration_entry(candles_hist, closes, candle, params):
    """Enter on simplified cointegration proxy (spread mean reversion)."""
    if len(closes) < 40:
        return False
    # Use price as proxy; test if spread from rolling mean is extreme
    rolling_mean = sum(closes[-20:]) / 20
    spread = closes[-1] - rolling_mean
    rolling_std = math.sqrt(sum((c - rolling_mean) ** 2 for c in closes[-20:]) / 20)
    if rolling_std == 0:
        return False
    z = spread / rolling_std
    # Enter on spread reversion from extreme
    if z < -1.5 and closes[-1] > closes[-2]:
        return True
    return False


def _distance_method_entry(candles_hist, closes, candle, params):
    """Enter when distance-based pattern matching finds similar bullish pattern."""
    if len(closes) < 30:
        return False
    returns = compute_returns(closes)
    dists = compute_distance_matrix(returns, window=10)
    if not dists:
        return False
    # Small distance = similar pattern found
    min_dist = min(dists)
    avg_dist = sum(dists) / len(dists)
    if min_dist < avg_dist * 0.5:
        return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _autocorrelation_entry(candles_hist, closes, candle, params):
    """Enter on significant positive autocorrelation at lag 1."""
    if len(closes) < 30:
        return False
    returns = compute_returns(closes[-30:])
    if len(returns) < 10:
        return False
    acf = compute_acf(returns, max_lag=1)
    if len(acf) > 1 and acf[1] > 0.2:
        return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _partial_autocorrelation_entry(candles_hist, closes, candle, params):
    """Enter on PACF signal at short lags."""
    if len(closes) < 30:
        return False
    returns = compute_returns(closes[-30:])
    if len(returns) < 10:
        return False
    pacf = compute_pacf(returns, max_lag=3)
    if len(pacf) > 2 and pacf[1] > 0.3:
        return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _variance_ratio_entry(candles_hist, closes, candle, params):
    """Enter when variance ratio rejects random walk (VR > 1)."""
    if len(closes) < 40:
        return False
    returns = compute_returns(closes[-60:])
    if len(returns) < 20:
        return False
    vr = compute_variance_ratio(returns, periods=[5])
    if 5 in vr and vr[5] > 1.3:
        return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _runs_test_entry(candles_hist, closes, candle, params):
    """Enter when runs test rejects randomness (trending detected)."""
    if len(closes) < 30:
        return False
    returns = compute_returns(closes[-30:])
    if len(returns) < 10:
        return False
    runs, z = runs_test(returns)
    # |z| > 1.96 rejects randomness
    if abs(z) > 1.96:
        # Check if positive trend
        if len(returns) > 5 and sum(returns[-5:]) > 0:
            return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _quantile_regression_entry(candles_hist, closes, candle, params):
    """Enter at quantile regression extreme (simplified via median regression)."""
    if len(closes) < 30:
        return False
    # Simplified: use median-based regression (quantile at 0.5)
    x = list(range(len(closes[-20:])))
    y = closes[-20:]
    slope, intercept, _ = linear_regression(x, y)
    predicted = [slope * xi + intercept for xi in x]
    residuals = [y[i] - predicted[i] for i in range(len(y))]
    median_res = sorted(residuals)[len(residuals) // 2]
    # Enter when residual is far below median (oversold)
    if residuals[-1] < median_res - abs(median_res) * 0.5:
        return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _ridge_regression_entry(candles_hist, closes, candle, params):
    """Enter on ridge-regularized regression signal."""
    if len(closes) < 30:
        return False
    x = list(range(len(closes[-20:])))
    y = closes[-20:]
    # Ridge: add lambda to diagonal
    lam = 1.0
    n = len(x)
    sx = sum(x)
    sy = sum(y)
    sxy = sum(xi * yi for xi, yi in zip(x, y))
    sxx = sum(xi * xi for xi in x)
    denom = n * sxx - sx * sx + lam * n
    if denom == 0:
        return False
    slope = (n * sxy - sx * sy) / denom
    # Enter on positive slope
    if slope > 0.01 * sy / n:
        return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _lasso_regression_entry(candles_hist, closes, candle, params):
    """Enter on lasso-sparse regression signal."""
    if len(closes) < 30:
        return False
    x = list(range(len(closes[-20:])))
    y = closes[-20:]
    # Lasso approx: iterative soft-thresholding
    slope, intercept, _ = linear_regression(x, y)
    # Soft threshold
    threshold = 0.1 * abs(slope)
    if slope > 0:
        slope = max(0, slope - threshold)
    else:
        slope = min(0, slope + threshold)
    if slope > 0.001:
        return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _elastic_net_entry(candles_hist, closes, candle, params):
    """Enter on combined L1/L2 regularization signal."""
    if len(closes) < 30:
        return False
    x = list(range(len(closes[-20:])))
    y = closes[-20:]
    n = len(x)
    sx = sum(x)
    sy = sum(y)
    sxy = sum(xi * yi for xi, yi in zip(x, y))
    sxx = sum(xi * xi for xi in x)
    # Elastic net: l1 + l2 penalty
    l1 = 0.1
    l2 = 0.1
    denom = n * sxx - sx * sx + l2 * n
    if denom == 0:
        return False
    slope = (n * sxy - sx * sy) / denom
    # L1 soft threshold
    threshold = l1
    if slope > 0:
        slope = max(0, slope - threshold)
    else:
        slope = min(0, slope + threshold)
    if slope > 0.001:
        return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _bayesian_regression_entry(candles_hist, closes, candle, params):
    """Enter on Bayesian posterior predictive signal."""
    if len(closes) < 30:
        return False
    x = list(range(len(closes[-20:])))
    y = closes[-20:]
    slope, intercept, _ = linear_regression(x, y)
    # Bayesian: shrink toward prior (prior slope = 0)
    prior_slope = 0
    prior_var = 1.0
    # Posterior mean approx: weighted avg of prior and MLE
    n = len(x)
    data_var = max(1e-8, sum((y[i] - (slope * x[i] + intercept)) ** 2 for i in range(n)) / n)
    w = prior_var / (prior_var + data_var / n)
    posterior_slope = w * prior_slope + (1 - w) * slope
    if posterior_slope > 0.01:
        return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _robust_regression_entry(candles_hist, closes, candle, params):
    """Enter on Huber/M-estimator robust regression signal."""
    if len(closes) < 30:
        return False
    y = closes[-20:]
    x = list(range(len(y)))
    # Robust mean of returns as signal
    returns = compute_returns(closes[-20:])
    robust_mean_ret = robust_mean(returns)
    if robust_mean_ret > 0.001:
        return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _local_regression_entry(candles_hist, closes, candle, params):
    """Enter on LOESS/LOWESS smoothed signal turning up."""
    if len(closes) < 30:
        return False
    smoothed = loess_smooth(closes[-20:], span=0.3)
    if len(smoothed) < 5:
        return False
    # Enter when smoothed series turns up
    recent_trend = smoothed[-1] - smoothed[-3]
    prev_trend = smoothed[-3] - smoothed[-5] if len(smoothed) > 5 else 0
    if recent_trend > 0 and recent_trend > prev_trend:
        return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _spline_regression_entry(candles_hist, closes, candle, params):
    """Enter at cubic spline knot (inflection detected)."""
    if len(closes) < 20:
        return False
    knot_indices, knot_values = cubic_spline_knots(closes[-20:], num_knots=5)
    if len(knot_values) < 4:
        return False
    # Check if last knot is a local minimum
    if len(knot_values) >= 3:
        is_min = knot_values[-2] < knot_values[-3] and knot_values[-2] < knot_values[-1]
        if is_min:
            return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _gamma_regression_entry(candles_hist, closes, candle, params):
    """Enter on non-linear regression for skewed returns (gamma proxy)."""
    if len(closes) < 30:
        return False
    returns = compute_returns(closes[-30:])
    if len(returns) < 10:
        return False
    # Gamma proxy: skewness of returns
    n = len(returns)
    mean_r = sum(returns) / n
    std_r = max(1e-8, math.sqrt(sum((r - mean_r) ** 2 for r in returns) / n))
    skew = sum((r - mean_r) ** 3 for r in returns) / (n * std_r ** 3)
    # Positive skew = upside potential
    if skew > 0.5:
        return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _logistic_regression_entry(candles_hist, closes, candle, params):
    """Enter on classification-based entry (logistic approx)."""
    if len(closes) < 30:
        return False
    # Features: return, volume change
    ret = closes[-1] / closes[-2] - 1 if len(closes) > 1 else 0
    vol_change = 0
    if len(candles_hist) > 2:
        v1 = float(candles_hist[-1]["volume"])
        v2 = float(candles_hist[-2]["volume"])
        vol_change = (v1 - v2) / v2 if v2 > 0 else 0
    # Simplified logistic: w1*ret + w2*vol_change > threshold
    logit = 3 * ret + 1 * vol_change
    if logit > 0.1:
        return True
    return False


def _multinomial_regression_entry(candles_hist, closes, candle, params):
    """Enter on multi-class regime prediction (simplified to 3 states)."""
    if len(closes) < 30:
        return False
    returns = compute_returns(closes[-30:])
    if len(returns) < 10:
        return False
    # Classify into 3 regimes: down, flat, up
    mean_r = sum(returns) / len(returns)
    std_r = max(1e-8, math.sqrt(sum((r - mean_r) ** 2 for r in returns) / len(returns)))
    # Current regime
    recent = returns[-5:]
    recent_mean = sum(recent) / len(recent)
    if recent_mean > std_r * 0.5:
        # Predicted: up regime
        return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _survival_analysis_entry(candles_hist, closes, candle, params):
    """Enter on hazard rate based entry."""
    if len(closes) < 40:
        return False
    returns = compute_returns(closes[-40:])
    if len(returns) < 10:
        return False
    # Hazard rate proxy: fraction of negative returns in window
    window = 10
    if len(returns) < window:
        return False
    recent_neg = sum(1 for r in returns[-window:] if r < 0)
    prev_neg = sum(1 for r in returns[-window * 2:-window] if r < 0)
    hazard_recent = recent_neg / window
    hazard_prev = prev_neg / window
    # Declining hazard = lower risk of drawdown
    if hazard_recent < hazard_prev * 0.8:
        return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _extreme_value_entry(candles_hist, closes, candle, params):
    """Enter on GPD tail estimation (extreme tail event bounce)."""
    if len(closes) < 40:
        return False
    returns = compute_returns(closes[-60:])
    if len(returns) < 20:
        return False
    # Peaks over threshold
    threshold = sorted(returns)[len(returns) // 10]  # 10th percentile
    exceedances = [r - threshold for r in returns if r < threshold]
    if not exceedances:
        return False
    # GPD shape proxy: mean of exceedances
    mean_exc = sum(exceedances) / len(exceedances)
    # Large negative tail event = oversold
    if mean_exc < -0.02:
        return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _copula_signal_entry(candles_hist, closes, candle, params):
    """Enter on dependency structure change (simplified via rank correlation)."""
    if len(candles_hist) < 30:
        return False
    prices = closes[-20:]
    volumes = [float(c["volume"]) for c in candles_hist[-20:]]
    rho = compute_copula_simple(prices, volumes)
    # Strong negative price-volume correlation = capitulation
    if rho < -0.3:
        return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _granger_causality_entry(candles_hist, closes, candle, params):
    """Enter when price leads/lags relationship detected."""
    if len(closes) < 30:
        return False
    returns = compute_returns(closes[-30:])
    volumes = [float(c["volume"]) for c in candles_hist[-30:]]
    vol_changes = [(volumes[i] - volumes[i - 1]) / volumes[i - 1] for i in range(1, len(volumes)) if volumes[i - 1] > 0]
    min_len = min(len(returns), len(vol_changes))
    returns = returns[-min_len:]
    vol_changes = vol_changes[-min_len:]
    # Test if volume Granger-causes price
    f_stat = granger_test(returns, vol_changes, max_lag=2)
    if f_stat > 1.5:
        return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _impulse_response_entry(candles_hist, closes, candle, params):
    """Enter on impulse response peak (momentum from shock)."""
    if len(closes) < 30:
        return False
    irf = impulse_response(closes[-20:], shock_size=1.0, horizon=10)
    if len(irf) < 5:
        return False
    # Peak in IRF
    peak_idx = irf.index(max(irf))
    # Enter when we're past the peak (momentum fading, reversion starting)
    if peak_idx < len(irf) // 2:
        return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _state_space_entry(candles_hist, closes, candle, params):
    """Enter on state-space model signal."""
    if len(closes) < 30:
        return False
    # Simplified state space: observation = state + noise, state evolves
    filtered = kalman_filter_1d(closes[-20:])
    if len(filtered) < 3:
        return False
    # State estimate vs observation
    state_estimate = filtered[-1]
    observation = closes[-1]
    # Enter when state > observation (latent trend stronger than price)
    if state_estimate > observation * 1.001:
        return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _dynamic_factor_entry(candles_hist, closes, candle, params):
    """Enter on dynamic factor model signal."""
    if len(candles_hist) < 40:
        return False
    # Extract common factor from price + volume
    prices = closes[-20:]
    volumes = [float(c["volume"]) for c in candles_hist[-20:]]
    # Normalize
    mp = sum(prices) / len(prices)
    sp = max(1e-8, math.sqrt(sum((p - mp) ** 2 for p in prices) / len(prices)))
    mv = sum(volumes) / len(volumes)
    sv = max(1e-8, math.sqrt(sum((v - mv) ** 2 for v in volumes) / len(volumes)))
    z_p = [(p - mp) / sp for p in prices]
    z_v = [(v - mv) / sv for v in volumes]
    # Dynamic factor: time-varying weight
    # Recent weight more on price
    recent_factor = sum(0.7 * zp + 0.3 * zv for zp, zv in zip(z_p[-5:], z_v[-5:])) / 5
    if recent_factor > 0.3:
        return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _regime_detection_entry(candles_hist, closes, candle, params):
    """Enter on statistical regime classifier (bullish regime)."""
    if len(closes) < 40:
        return False
    returns = compute_returns(closes[-40:])
    if len(returns) < 20:
        return False
    # Regime features: mean, vol, skew
    window = 10
    recent = returns[-window:]
    prev = returns[-window * 2:-window]
    mean_recent = sum(recent) / len(recent)
    mean_prev = sum(prev) / len(prev)
    vol_recent = math.sqrt(sum(r * r for r in recent) / len(recent))
    vol_prev = math.sqrt(sum(r * r for r in prev) / len(prev))
    # Bullish regime: positive mean, declining vol
    if mean_recent > 0 and vol_recent < vol_prev * 0.9:
        return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _volatility_clustering_entry(candles_hist, closes, candle, params):
    """Enter on GARCH-style vol clustering signal."""
    if len(closes) < 40:
        return False
    returns = compute_returns(closes[-40:])
    if len(returns) < 20:
        return False
    # Vol clustering: compare recent vol to historical
    abs_ret = [abs(r) for r in returns]
    recent_vol = sum(abs_ret[-5:]) / 5
    hist_vol = sum(abs_ret[:-5]) / max(1, len(abs_ret) - 5)
    # Vol declining after cluster
    if recent_vol < hist_vol * 0.7:
        return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _long_memory_entry(candles_hist, closes, candle, params):
    """Enter on long-range dependence detection."""
    if len(closes) < 50:
        return False
    returns = compute_returns(closes[-60:])
    if len(returns) < 30:
        return False
    # Long memory: ACF decays slowly
    acf = compute_acf(returns, max_lag=10)
    if len(acf) < 10:
        return False
    # Check if ACF at lag 10 is still significant
    if abs(acf[10]) > 0.1:
        return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _fractal_dimension_entry(candles_hist, closes, candle, params):
    """Enter on fractal analysis of price series."""
    if len(closes) < 30:
        return False
    fd = fractal_dimension(closes[-40:])
    # FD near 1.0 = smooth trend, FD near 2.0 = rough/noisy
    # Enter when FD declining (becoming smoother/trending)
    if fd < 1.4:
        return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _entropy_measure_entry(candles_hist, closes, candle, params):
    """Enter on Shannon entropy of returns (low entropy = predictable)."""
    if len(closes) < 30:
        return False
    returns = compute_returns(closes[-30:])
    if len(returns) < 10:
        return False
    entropy = compute_entropy(returns, bins=8)
    max_entropy = math.log(8)  # uniform
    normalized = entropy / max_entropy if max_entropy > 0 else 1
    # Low entropy = more predictable/orderly
    if normalized < 0.6:
        return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _mutual_information_entry(candles_hist, closes, candle, params):
    """Enter on MI between price and volume."""
    if len(candles_hist) < 30:
        return False
    prices = closes[-20:]
    volumes = [float(c["volume"]) for c in candles_hist[-20:]]
    mi = compute_mutual_info(prices, volumes, bins=8)
    # High MI = strong dependency
    if mi > 0.3:
        return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _transfer_entropy_entry(candles_hist, closes, candle, params):
    """Enter on directional information flow (simplified)."""
    if len(candles_hist) < 40:
        return False
    price_changes = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes)) if closes[i - 1] != 0]
    vol_changes = []
    for i in range(1, len(candles_hist)):
        v1 = float(candles_hist[i]["volume"])
        v0 = float(candles_hist[i - 1]["volume"])
        vol_changes.append((v1 - v0) / v0 if v0 > 0 else 0)
    min_len = min(len(price_changes), len(vol_changes))
    price_changes = price_changes[-min_len:]
    vol_changes = vol_changes[-min_len:]
    # Transfer entropy proxy: conditional MI
    if min_len < 10:
        return False
    # Simplified: MI between lagged volume and current price
    vol_lagged = vol_changes[:-1]
    price_current = price_changes[1:]
    te = compute_mutual_info(vol_lagged, price_current, bins=6)
    if te > 0.2:
        return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _cross_correlation_entry(candles_hist, closes, candle, params):
    """Enter on cross-correlation of returns with volume."""
    if len(candles_hist) < 30:
        return False
    returns = compute_returns(closes[-30:])
    volumes = [float(c["volume"]) for c in candles_hist[-30:]]
    vol_changes = [(volumes[i] - volumes[i - 1]) / volumes[i - 1] for i in range(1, len(volumes)) if volumes[i - 1] > 0]
    min_len = min(len(returns), len(vol_changes))
    returns = returns[-min_len:]
    vol_changes = vol_changes[-min_len:]
    cc = compute_cross_corr(returns, vol_changes, max_lag=3)
    if len(cc) > 1 and cc[0] > 0.3:
        return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _detrended_fluctuation_entry(candles_hist, closes, candle, params):
    """Enter on DFA scaling exponent signal."""
    if len(closes) < 40:
        return False
    alpha = dfa_exponent(closes[-60:])
    # alpha > 0.5 = persistent (trending)
    if alpha > 0.55:
        return len(closes) > 1 and closes[-1] > closes[-2]
    return False


# ==========================================
# STRATEGY DEFINITIONS
# ==========================================

STATISTICAL_STRATEGIES = [
    # Regression-based
    {"name": "linear_regression", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "polynomial_regression", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "ridge_regression", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "lasso_regression", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "elastic_net", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "bayesian_regression", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "robust_regression", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "local_regression", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "spline_regression", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "gamma_regression", "params": {"tp_pct": 10, "sl_pct": 4, "max_hold": 24}},
    {"name": "logistic_regression", "params": {"tp_pct": 6, "sl_pct": 3, "max_hold": 24}},
    {"name": "multinomial_regression", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "quantile_regression", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},

    # Spectral/frequency
    {"name": "fourier_analysis", "params": {"tp_pct": 10, "sl_pct": 4, "max_hold": 24}},
    {"name": "wavelet_analysis", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "spectral_analysis", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},

    # Time-series properties
    {"name": "hurst_exponent", "params": {"tp_pct": 10, "sl_pct": 4, "max_hold": 24}},
    {"name": "unit_root", "params": {"tp_pct": 6, "sl_pct": 3, "max_hold": 24}},
    {"name": "autocorrelation", "params": {"tp_pct": 6, "sl_pct": 3, "max_hold": 24}},
    {"name": "partial_autocorrelation", "params": {"tp_pct": 6, "sl_pct": 3, "max_hold": 24}},
    {"name": "variance_ratio", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "runs_test", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "long_memory", "params": {"tp_pct": 10, "sl_pct": 4, "max_hold": 24}},
    {"name": "detrended_fluctuation", "params": {"tp_pct": 10, "sl_pct": 4, "max_hold": 24}},

    # Decomposition/factors
    {"name": "pca_signal", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "factor_analysis", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "time_series_decomposition", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},

    # Stochastic models
    {"name": "garch_forecast", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "kalman_filter", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "ornstein_uhlenbeck", "params": {"tp_pct": 10, "sl_pct": 4, "max_hold": 24}},
    {"name": "markov_switching", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "hidden_markov", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},

    # Statistical tests
    {"name": "change_point", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "cluster_analysis", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "granger_causality", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},

    # Advanced
    {"name": "cointegration", "params": {"tp_pct": 6, "sl_pct": 3, "max_hold": 24}},
    {"name": "distance_method", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "survival_analysis", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "extreme_value", "params": {"tp_pct": 12, "sl_pct": 5, "max_hold": 24}},
    {"name": "copula_signal", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "impulse_response", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "state_space", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "dynamic_factor", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "regime_detection", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "volatility_clustering", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "fractal_dimension", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "entropy_measure", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "mutual_information", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "transfer_entropy", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "cross_correlation", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
]

ENTRY_FUNCS = {
    "linear_regression": _linear_regression_entry,
    "polynomial_regression": _polynomial_regression_entry,
    "pca_signal": _pca_signal_entry,
    "factor_analysis": _factor_analysis_entry,
    "cluster_analysis": _cluster_analysis_entry,
    "time_series_decomposition": _time_series_decomposition_entry,
    "fourier_analysis": _fourier_analysis_entry,
    "wavelet_analysis": _wavelet_analysis_entry,
    "spectral_analysis": _spectral_analysis_entry,
    "hurst_exponent": _hurst_exponent_entry,
    "unit_root": _unit_root_entry,
    "change_point": _change_point_entry,
    "markov_switching": _markov_switching_entry,
    "hidden_markov": _hidden_markov_entry,
    "garch_forecast": _garch_forecast_entry,
    "kalman_filter": _kalman_filter_entry,
    "ornstein_uhlenbeck": _ornstein_uhlenbeck_entry,
    "cointegration": _cointegration_entry,
    "distance_method": _distance_method_entry,
    "autocorrelation": _autocorrelation_entry,
    "partial_autocorrelation": _partial_autocorrelation_entry,
    "variance_ratio": _variance_ratio_entry,
    "runs_test": _runs_test_entry,
    "quantile_regression": _quantile_regression_entry,
    "ridge_regression": _ridge_regression_entry,
    "lasso_regression": _lasso_regression_entry,
    "elastic_net": _elastic_net_entry,
    "bayesian_regression": _bayesian_regression_entry,
    "robust_regression": _robust_regression_entry,
    "local_regression": _local_regression_entry,
    "spline_regression": _spline_regression_entry,
    "gamma_regression": _gamma_regression_entry,
    "logistic_regression": _logistic_regression_entry,
    "multinomial_regression": _multinomial_regression_entry,
    "survival_analysis": _survival_analysis_entry,
    "extreme_value": _extreme_value_entry,
    "copula_signal": _copula_signal_entry,
    "granger_causality": _granger_causality_entry,
    "impulse_response": _impulse_response_entry,
    "state_space": _state_space_entry,
    "dynamic_factor": _dynamic_factor_entry,
    "regime_detection": _regime_detection_entry,
    "volatility_clustering": _volatility_clustering_entry,
    "long_memory": _long_memory_entry,
    "fractal_dimension": _fractal_dimension_entry,
    "entropy_measure": _entropy_measure_entry,
    "mutual_information": _mutual_information_entry,
    "transfer_entropy": _transfer_entropy_entry,
    "cross_correlation": _cross_correlation_entry,
    "detrended_fluctuation": _detrended_fluctuation_entry,
}


def fetch_candles(client, pid, start, end):
    """Fetch candles in chunks to avoid API limits."""
    chunk_sec = 300 * 5 * 60
    all_c = []
    cs = start
    while cs < end:
        ce = min(cs + chunk_sec, end)
        try:
            resp = client.market_candles(pid, start=cs, end=ce, granularity="FIVE_MINUTE")
            cands = resp.get("candles", [])
            all_c.extend(cands)
            cs = ce
            if not cands:
                break
            time.sleep(0.1)
        except Exception:
            cs = ce
            time.sleep(0.3)
    all_c.sort(key=lambda c: int(c["start"]))
    return all_c


def main():
    start_time = time.time()
    print(f"\n{'='*70}")
    print(f"STATISTICAL 50 STRATEGY SWEEP — Quant Edge Discovery")
    print(f"Started: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*70}\n")

    client = CoinbaseAdvancedClient()

    # Load coin list
    coin_file = Path(__file__).parent.parent / "coinbase_usd_pairs.txt"
    if coin_file.exists():
        coins = [line.strip() for line in open(coin_file) if line.strip() and not line.startswith("Total")]
        print(f"Loaded {len(coins)} coins from coinbase_usd_pairs.txt")
    else:
        coins = ["GHST-USD", "MOG-USD", "RAVE-USD", "TRU-USD", "NOM-USD"]
        print(f"Using fallback: {len(coins)} coins")

    fast_coins = coins[:35] + [c for c in ["GHST-USD", "NOM-USD", "TRU-USD", "MOG-USD", "RAVE-USD"] if c not in coins[:35]]
    print(f"Testing on {len(fast_coins)} coins (7d discovery phase)\n")

    now = int(time.time())
    start_ts = now - 7 * 86400

    all_candles = {}
    for coin in fast_coins:
        try:
            candles = fetch_candles(client, coin, start_ts, now)
            if candles:
                all_candles[coin] = candles
                print(f"  {coin}: {len(candles)} candles")
            else:
                print(f"  {coin}: NO DATA")
        except Exception as e:
            print(f"  {coin}: ERROR — {str(e)[:60]}")
        time.sleep(0.2)

    print(f"\nFetched data for {len(all_candles)} coins")
    print(f"Testing {len(STATISTICAL_STRATEGIES)} statistical strategies...\n")

    results = []
    total_tests = len(all_candles) * len(STATISTICAL_STRATEGIES)
    test_count = 0

    for strat_def in STATISTICAL_STRATEGIES:
        strat_name = strat_def["name"]
        entry_fn = ENTRY_FUNCS.get(strat_name)
        if entry_fn is None:
            print(f"  SKIP {strat_name}: no entry function")
            continue

        coin_results = []
        for coin, candles in all_candles.items():
            test_count += 1
            try:
                result = backtest(candles, entry_fn, strat_def["params"],
                                  fee_rate=0.004, starting_cash=48.0)
                coin_results.append({"coin": coin, "candles": len(candles), **result})
            except Exception as e:
                coin_results.append({"coin": coin, "error": str(e)[:80]})

            if test_count % 100 == 0:
                elapsed = time.time() - start_time
                print(f"  Progress: {test_count}/{total_tests} tests ({elapsed:.0f}s)")

        profitable = [r for r in coin_results if "net_pnl" in r and r["net_pnl"] > 0]
        avg_pnl = sum(r.get("net_pnl", 0) for r in coin_results) / len(coin_results) if coin_results else 0

        strat_summary = {
            "strategy": strat_name,
            "coins_tested": len(coin_results),
            "profitable_coins": len(profitable),
            "hit_rate": len(profitable) / len(coin_results) * 100 if coin_results else 0,
            "avg_net_pnl": round(avg_pnl, 2),
            "total_net_pnl": round(sum(r.get("net_pnl", 0) for r in coin_results), 2),
            "best_coin": max(profitable, key=lambda x: x.get("net_pnl", 0)) if profitable else None,
            "coin_details": coin_results[:5]
        }
        results.append(strat_summary)

        print(f"  {strat_name:<30} | {len(profitable):>3}/{len(coin_results)} coins | "
              f"Hit: {strat_summary['hit_rate']:>5.1f}% | "
              f"Avg PnL: ${avg_pnl:>7.2f} | "
              f"Total: ${strat_summary['total_net_pnl']:>8.2f}")

    results.sort(key=lambda x: x["total_net_pnl"], reverse=True)

    output = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": round(time.time() - start_time, 1),
        "coins_tested": len(all_candles),
        "strategies_tested": len(results),
        "total_backtests": test_count,
        "results": results,
        "top_10_strategies": results[:10],
        "promoted_for_30d": [r["strategy"] for r in results[:5] if r["hit_rate"] > 30]
    }

    out_path = Path(__file__).parent.parent / "reports" / "statistical_50_sweep_7d.json"
    out_path.parent.mkdir(exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\n{'='*70}")
    print(f"SWEEP COMPLETE in {time.time() - start_time:.0f}s")
    print(f"Results saved to: {out_path}")
    print(f"\nTOP 10 STATISTICAL STRATEGIES:")
    for i, r in enumerate(results[:10], 1):
        print(f"  {i:>2}. {r['strategy']:<30} ${r['total_net_pnl']:>8.2f}  "
              f"Hit: {r['hit_rate']:>5.1f}%  "
              f"Profitable: {r['profitable_coins']}/{r['coins_tested']}")

    if output["promoted_for_30d"]:
        print(f"\nPROMOTED FOR 30D VALIDATION:")
        for s in output["promoted_for_30d"]:
            print(f"  [PROMOTED] {s}")

    print(f"{'='*70}\n")


if __name__ == "__main__":
    main()
