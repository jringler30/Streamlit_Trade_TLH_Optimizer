"""
conftest.py
===========
Pytest configuration: mock Streamlit and gdown so that any test which imports
portfolio_returns_engine.py (the Streamlit app file) does not crash on missing
server infrastructure.

After the engine/ package refactor, most tests import pure functions directly
from engine.core, engine.rebalancing, and engine.metrics — which have NO
Streamlit dependency and need NO mocking. The mocks here are a defensive
fallback for tests that still reference portfolio_returns_engine directly.

Architecture note
-----------------
The previous conftest.py used a manual exec() pre-load hack to expose
portfolio_returns_engine functions while catching st.stop() exceptions.
That hack is no longer needed because the engine/ package provides clean,
importable pure functions with no Streamlit attachment.
"""

import sys
import datetime
import pathlib
import pandas as pd
from unittest.mock import MagicMock


# ─────────────────────────────────────────────────────────────────────────────
# Step 1: Streamlit mock — prevents crashes when portfolio_returns_engine.py
# is imported (it calls st.set_page_config, st.cache_data, etc. at module level)
# ─────────────────────────────────────────────────────────────────────────────

_FIXED_DATE = datetime.date(2020, 1, 1)


class _DateAwareMock(MagicMock):
    """
    MagicMock subclass that returns safe values for common Streamlit widget calls:
    - date_input() → real datetime.date (prevents arithmetic TypeError on subtraction)
    - button()     → False (prevents module-level if-run-btn: blocks from executing)
    - checkbox()   → False
    - __getitem__  → another _DateAwareMock (for nested calls like columns(2)[0].date_input())
    """

    def date_input(self, *args, **kwargs):
        return _FIXED_DATE

    def button(self, *args, **kwargs):
        return False

    def checkbox(self, *args, **kwargs):
        return False

    def __getitem__(self, item):
        return _DateAwareMock()


def _make_streamlit_mock():
    """Minimal Streamlit mock that lets module-level calls succeed silently."""
    st = _DateAwareMock()
    st.set_page_config = MagicMock()

    # @st.cache_data must act as a pass-through decorator
    def _cache_data(**kwargs):
        def _wrap(fn):
            return fn
        return _wrap

    st.cache_data = _cache_data

    # st.stop() is a no-op in tests — module-level guard code is bypassed because
    # run_btn returns False, so the computation block never runs, and the guard
    # at "if '_r' not in st.session_state" is never reached for engine/ imports.
    st.stop = MagicMock()
    st.error = MagicMock()
    st.warning = MagicMock()
    st.info = MagicMock()
    st.sidebar = _DateAwareMock()
    st.session_state = {}
    return st


sys.modules.setdefault("streamlit", _make_streamlit_mock())
sys.modules.setdefault("gdown", MagicMock())


# ─────────────────────────────────────────────────────────────────────────────
# Step 2: Data mocks — prevents FileNotFoundError if portfolio_returns_engine
# is imported (it calls load_data() → reads /tmp/price_data.parquet at module level)
# ─────────────────────────────────────────────────────────────────────────────

_DATA_PATH = "/tmp/price_data.parquet"
_REQUIRED_COLS = [
    "TRADINGITEMID", "TICKERSYMBOL", "PRICEDATE",
    "PRICECLOSE", "PRICEMID", "TRADINGITEMSTATUSID",
]

_orig_path_exists = pathlib.Path.exists
_orig_path_stat = pathlib.Path.stat


class _FakeStat:
    st_size = 200 * 1024 * 1024  # 200 MB — passes the size check


def _mock_path_exists(self):
    if str(self) == _DATA_PATH:
        return True
    return _orig_path_exists(self)


def _mock_path_stat(self, *, follow_symlinks=True):
    if str(self) == _DATA_PATH:
        return _FakeStat()
    return _orig_path_stat(self, follow_symlinks=follow_symlinks)


pathlib.Path.exists = _mock_path_exists
pathlib.Path.stat = _mock_path_stat

_orig_read_parquet = pd.read_parquet


def _mock_read_parquet(path, *args, **kwargs):
    if str(path) == _DATA_PATH:
        cols = kwargs.get("columns", _REQUIRED_COLS)
        return pd.DataFrame(columns=cols)
    return _orig_read_parquet(path, *args, **kwargs)


pd.read_parquet = _mock_read_parquet
