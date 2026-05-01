"""CI-only MetaTrader5 stub for import-time unit tests.

The focused Kraken maker tests do not exercise MT5 broker calls, but shared
helpers import MetaTrader5 at module load time. GitHub's Linux runners cannot
install the Windows-only MetaTrader5 package, so the workflow prepends this
directory to PYTHONPATH.
"""

TIMEFRAME_M1 = object()
TIMEFRAME_M2 = object()
TIMEFRAME_M5 = object()
TIMEFRAME_M15 = object()
TIMEFRAME_H1 = object()
TIMEFRAME_H4 = object()


def copy_rates_from_pos(*args, **kwargs):
    raise RuntimeError("MetaTrader5 CI stub does not provide broker data")


def symbol_info(*args, **kwargs):
    return None


def shutdown():
    return None
