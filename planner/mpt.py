"""Mean-variance tools: bounded frontier, max-Sharpe, minimum variance, resampling (Michaud), risk parity."""
import numpy as np
from scipy.optimize import linprog, minimize


def mu_arith(ret, vol, ter):
    """Arithmetic expected return implied by a compound forecast and volatility (lognormal)."""
    return (1 + np.asarray(ret) - np.asarray(ter)) * np.exp(.5 * np.asarray(vol) ** 2) - 1


def opt(mu, cov, lo, hi, mode, target=None, rf=0.0):
    cons = [{'type': 'eq', 'fun': lambda w: w.sum() - 1}]
    if mode == 'ret':
        cons.append({'type': 'eq', 'fun': lambda w: w @ mu - target})
    f = {'var': lambda w: w @ cov @ w, 'ret': lambda w: w @ cov @ w,
         'sharpe': lambda w: -(w @ mu - rf) / np.sqrt(w @ cov @ w)}[mode]
    r = minimize(f, np.full(len(mu), 1 / len(mu)), method='SLSQP', bounds=list(zip(lo, hi)), constraints=cons,
                 options=dict(maxiter=500))
    return r.x


def max_return(mu, lo, hi):
    r = linprog(-mu, A_eq=[np.ones(len(mu))], b_eq=[1], bounds=list(zip(lo, hi)))
    return float(-r.fun)


def erc(cov):
    k = len(cov)
    f = lambda w: ((w * (cov @ w) - (w @ cov @ w) / k) ** 2).sum() * 1e6
    return minimize(f, np.full(k, 1 / k), method='SLSQP', bounds=[(0, 1)] * k,
                    constraints=[{'type': 'eq', 'fun': lambda w: w.sum() - 1}]).x


def risk_contrib(w, cov):
    return w * (cov @ w) / (w @ cov @ w)


def analyse(mu, cov, lo, hi, rf, years_of_data=15, draws=150, points=18, seed=5):
    """Bounded long-only frontier plus the usual named portfolios, for the growth assets."""
    ws = opt(mu, cov, lo, hi, 'sharpe', rf=rf)
    wm = opt(mu, cov, lo, hi, 'var')
    top = max_return(mu, lo, hi) - 1e-4
    fr = []
    for t in np.linspace(mu @ wm + .002, top, points):
        w = opt(mu, cov, lo, hi, 'ret', target=t)
        fr.append((float(np.sqrt(w @ cov @ w)), float(w @ mu)))
    rng = np.random.default_rng(seed)
    Lc = np.linalg.cholesky(cov / years_of_data)                 # estimation error of the means
    rs = np.mean([opt(mu + Lc @ rng.standard_normal(len(mu)), cov, lo, hi, 'sharpe', rf=rf) for _ in range(draws)], axis=0)
    return dict(max_sharpe=ws, min_var=wm, resampled=rs, erc=erc(cov), frontier=fr)
