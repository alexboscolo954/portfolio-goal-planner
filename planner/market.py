"""Market model: correlation calibration and return generators (fat-tailed t and block bootstrap)."""
import os
import numpy as np
import pandas as pd

from .config import ASSETS

# Fallback correlation matrix (monthly returns of broad ETF proxies, 2008-2026), asset order = config.ASSETS.
# Used when calibration = "default" so the planner runs offline and tests are deterministic.
_DEFAULT_CORR = {
    ('global_equity', 'em_equity'): .88, ('global_equity', 'small_value'): .96, ('global_equity', 'bonds'): .32,
    ('global_equity', 'tips'): .46, ('global_equity', 'gold'): .21, ('global_equity', 'cash'): .02,
    ('em_equity', 'small_value'): .83, ('em_equity', 'bonds'): .32, ('em_equity', 'tips'): .46,
    ('em_equity', 'gold'): .34, ('em_equity', 'cash'): .03,
    ('small_value', 'bonds'): .29, ('small_value', 'tips'): .43, ('small_value', 'gold'): .17, ('small_value', 'cash'): -.03,
    ('bonds', 'tips'): .79, ('bonds', 'gold'): .37, ('bonds', 'cash'): .02,
    ('tips', 'gold'): .47, ('tips', 'cash'): -.02, ('gold', 'cash'): .16,
    ('speculative', 'global_equity'): .33, ('speculative', 'em_equity'): .30, ('speculative', 'small_value'): .30,
    ('speculative', 'bonds'): .10, ('speculative', 'tips'): .12, ('speculative', 'gold'): .05, ('speculative', 'cash'): .0,
}
# Yahoo Finance proxies used for live calibration.
_PROXIES = {'global_equity': ['VT'], 'em_equity': ['EEM'], 'small_value': ['VBR', 'SCZ'], 'bonds': ['AGG'],
            'tips': ['TIP'], 'gold': ['GLD'], 'cash': ['BIL'], 'speculative': ['BTC-USD']}


def psd(c):
    """Nearest valid correlation matrix (clip negative eigenvalues, renormalise the diagonal)."""
    w, v = np.linalg.eigh(c)
    c = (v * np.clip(w, 1e-6, None)) @ v.T
    d = np.sqrt(np.diag(c))
    return c / np.outer(d, d)


def default_corr():
    C = np.eye(len(ASSETS))
    for (a, b), x in _DEFAULT_CORR.items():
        i, j = ASSETS.index(a), ASSETS.index(b)
        C[i, j] = C[j, i] = x
    return psd(C)


def _load_prices(cache):
    if not os.path.exists(cache):
        import yfinance as yf
        tickers = sorted({t for v in _PROXIES.values() for t in v})
        px = yf.download(tickers, start='2007-01-01', progress=False, auto_adjust=True)['Close']
        os.makedirs(os.path.dirname(cache) or '.', exist_ok=True)
        px.resample('ME').last().to_csv(cache)
    return pd.read_csv(cache, index_col=0, parse_dates=True)


def calibrate(cfg):
    """Return (history of monthly log returns or None, correlation matrix)."""
    if cfg['market']['calibration'] != 'yfinance':
        return None, default_corr()
    lr = np.log(_load_prices(cfg['market']['cache'])).diff().iloc[:-1]     # drop the (partial) latest month
    P = pd.DataFrame({a: lr[t].mean(axis=1) for a, t in _PROXIES.items()})
    core = [a for a in ASSETS if a != 'speculative']
    hist = P[core].dropna()
    full = P.dropna()
    C = np.eye(len(ASSETS))
    C[:7, :7] = hist.corr().values
    C[7, :7] = C[:7, 7] = full.corr()['speculative'][core].values
    return hist, psd(C)


class Shocks:
    """Random numbers drawn once and reused, so every allocation faces the same markets (paired comparison)."""
    def __init__(self, n, T, seed=1):
        r = np.random.default_rng(seed)
        self.n, self.T = n, T
        self.z = r.standard_normal((n, T, len(ASSETS)), dtype=np.float32)
        self.w = (r.chisquare(6, (n, T, 1)) / 6).astype(np.float32)               # t(6) mixing shared across assets
        self.eps = (r.standard_t(4, (n, T)) / np.sqrt(2.0)).astype(np.float32)     # unit-variance t(4)
        self.blk = r.integers(0, 10 ** 6, (n, -(-T // 6)))


def mu_log(ret, ter):
    """Forecasts are compound returns, so the monthly log-mean is ln(1+g)/12 and the median path grows at g."""
    return np.log1p(np.asarray(ret) - np.asarray(ter)) / 12


def gen_t(sh, C, ret, vol, ter):
    """Correlated monthly log returns with Student-t(6) tails (shared mixing variable: crashes hit all assets)."""
    L = np.linalg.cholesky(C * np.outer(vol, vol) / 12)
    z = (sh.z @ L.T.astype(np.float32)) * np.float32(np.sqrt(4 / 6)) / np.sqrt(sh.w)
    return (z + mu_log(ret, ter).astype(np.float32)).astype(np.float32)


def gen_boot(sh, hist, C, ret, vol, ter, block=6):
    """Block bootstrap of historical months, de-meaned and re-centred on the forecasts.
    The speculative asset is modelled as beta x global equity plus fat-tailed idiosyncratic noise."""
    H = hist.values
    dm = H - H.mean(0)
    idx = ((sh.blk[:, :, None] % len(H) + np.arange(block)) % len(H)).reshape(sh.n, -1)[:, :sh.T]
    x = dm[idx]
    rho = C[7, 0]
    beta = rho * vol[7] / (dm[:, 0].std() * np.sqrt(12))
    sp = beta * x[:, :, 0] + vol[7] * np.sqrt(1 - rho ** 2) / np.sqrt(12) * sh.eps
    return (np.concatenate([x, sp[:, :, None]], axis=2) + mu_log(ret, ter)).astype(np.float32)
