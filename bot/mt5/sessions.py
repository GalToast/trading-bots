"""Symbol/session classifiers (crypto, exotic, session windows). Pure; unit-tested."""
from __future__ import annotations
from .config import *  # noqa: F401,F403
from datetime import datetime
from datetime import timezone


def is_crypto(symbol):
    crypto_tickers = {'BTC', 'ETH', 'XRP', 'DOGE', 'ADA', 'SOL', 'LTC', 'XMR', 'ZEC', 'DASH', 'DOT', 'MATIC', 'AVAX', 'LINK', 'UNI', 'BNB', 'SHIB', 'PEPE'}
    for t in crypto_tickers:
        if symbol.startswith(t):
            return True
    return False

def is_commodity(symbol):
    return symbol.startswith('XAU') or symbol.startswith('XAG')

def is_exotic(symbol):
    """Check if symbol is an exotic pair"""
    exotics = {'ZAR', 'MXN', 'NOK', 'SEK', 'DKK', 'HKD', 'CNH', 'SGD', 'CZK', 'PLN', 'HUF', 'TRY'}
    for e in exotics:
        if e in symbol:
            return True
    return False

def is_good_session(symbol):
    """Check if current time is a good session for this symbol"""
    # DISABLED: Trade all symbols regardless of session
    return True

def is_overlap_session():
    """Check if we're in the London/NY overlap (best liquidity)"""
    utc_hour = datetime.now(timezone.utc).hour
    return SESSION_OVERLAP[0] <= utc_hour < SESSION_OVERLAP[1]

def is_off_session():
    """Check if we're in the low-liquidity off-session period."""
    utc_hour = datetime.now(timezone.utc).hour
    return utc_hour in OFF_SESSION_HOURS

def is_msls_symbol_valid(symbol):
    """Check if a symbol has a proven 90%+ green rate for MSLS."""
    proven_msls_symbols = {
        'NAS100',  # 96.6% Green Next
        'US30',    # 95.7% Green Next
        'AUDCHF',  # 95.4% Green Next
        'EURJPY',  # 95.2% Green Next
        'XAUUSD',  # 92.2% Green Next
    }
    return symbol in proven_msls_symbols

def is_asian_session():
    """Check if we're in Asian session (00:00-07:59 UTC)"""
    utc_hour = datetime.now(timezone.utc).hour
    return 0 <= utc_hour < 8

def is_london_session():
    """Check if we're in London session (07:00-16:59 UTC)"""
    utc_hour = datetime.now(timezone.utc).hour
    return 7 <= utc_hour < 17
