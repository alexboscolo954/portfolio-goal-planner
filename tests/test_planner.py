import pathlib

import numpy as np
import pytest

from planner import config, engine as E, market, mpt


@pytest.fixture(scope='module')
def cfg():
    c = config.load(pathlib.Path(__file__).resolve().parent.parent / 'examples' / 'example.toml')
    c['goal']['horizon_years'] = 15
    c['T'] = 180
    return c


def zero_state(cfg):
    n, T = 40, cfg['T']
    return n, np.zeros((n, T, 8), np.float32), np.full((n, T), 3500.0), np.zeros((n, T))


def test_bookkeeping_is_exact_with_zero_returns(cfg):
    """Zero returns, no gaps: final wealth = start + contributions + lump sums (minus the 0.1% rebalance cost)."""
    n, R, fl, lumps = zero_state(cfg)
    lumps[:, 11] = 55_000.0
    h = cfg['portfolio']['holdings']
    start = sum(h.values()) + cfg['speculative']['start']
    for name, w in E.alloc_weights(cfg).items():
        r = E.run(cfg, R, fl, lumps, E.start_state(cfg, w))
        assert abs(r['W'][:, -1].mean() - (start + 3500 * cfg['T'] + 55_000)) < 150, name
        assert (r['H'] >= -1e-6).all()


def test_median_growth_matches_forecast(cfg):
    C = market.default_corr()
    sh = market.Shocks(2000, cfg['T'], seed=3)
    R = market.gen_t(sh, C, np.array(cfg['ret']), np.array(cfg['vol']), np.array(cfg['ter']))
    g = np.median(np.exp(R[:, :, 0].astype(float).sum(1))) ** (12 / cfg['T']) - 1
    assert abs(g - (cfg['ret'][0] - cfg['ter'][0])) < .006


def test_alloc_conserves_value_and_buys_underweights():
    H = np.array([[100.0, 0, 0, 0, 0, 0, 0]])
    w = np.array([.5, .5, 0, 0, 0, 0, 0])
    out = E.alloc(H, w, np.array([100.0]))
    assert out.sum() == pytest.approx(200.0)
    assert out[0, 0] == pytest.approx(100.0) and out[0, 1] == pytest.approx(100.0)   # all new money to the underweight asset
    sold = E.alloc(np.array([[100.0, 50, 0, 0, 0, 0, 0]]), w, np.array([-30.0]))
    assert sold.sum() == pytest.approx(120.0) and sold[0, 0] == pytest.approx(70.0)  # outflow sells the overweight first


def test_default_corr_is_a_valid_correlation_matrix():
    C = market.default_corr()
    assert np.allclose(np.diag(C), 1) and np.allclose(C, C.T) and np.linalg.eigvalsh(C).min() > 0


def test_income_gap_owes_liabilities(cfg):
    fl = E.make_flows(cfg, 2000, gap_p=1.0)          # permanent gaps
    assert fl[:, 0].mean() == pytest.approx(-1200)   # liabilities still due, no contributions
    assert fl[:, 40].mean() >= -1e-9                 # liabilities have ended after 36 months


def test_mpt_weights_respect_bounds():
    C = market.default_corr()
    vol = np.array([a[1] for a in config.ASSUMPTIONS.values()])
    ret = np.array([a[0] for a in config.ASSUMPTIONS.values()])
    ter = np.array([a[2] for a in config.ASSUMPTIONS.values()])
    cov, mu = C * np.outer(vol, vol), mpt.mu_arith(ret, vol, ter)
    hi = np.array([1, .2, .25, .6, .3, .15])
    w = mpt.opt(mu[:6], cov[:6, :6], np.zeros(6), hi, 'sharpe', rf=mu[6])
    assert w.sum() == pytest.approx(1) and (w <= hi + 1e-6).all() and (w >= -1e-6).all()
