"""Orchestration: runs the frontier analysis, the Monte Carlo grid, sensitivities and stress tests."""
import numpy as np

from . import engine as E
from . import mpt
from .config import ASSETS, GROWTH, LABELS
from .market import Shocks, calibrate, gen_boot, gen_t, psd

GFC = [-.45, -.52, -.50, .06, .02, .08, .02, -.75]        # 2008-style: total return over the 12-month shock
STAGFLATION = [-.22, -.22, -.18, -.13, -.12, -.02, .03, -.60]    # 2022-style: stocks and bonds fall together


def _clean(s):
    return {k: v for k, v in s.items() if k not in ('fn', 'fr')}


def _shock(R, a, b, total):
    R2 = R.copy()
    R2[:, a:b, :] = (np.log1p(np.array(total)) / (b - a)).astype(np.float32)
    return R2


def run_all(cfg, quick=False, log=print):
    n = 3000 if quick else int(cfg['simulation']['paths'])
    T, goal = cfg['T'], cfg['goal']['amount']
    ret, vol, ter = np.array(cfg['ret']), np.array(cfg['vol']), np.array(cfg['ter'])
    hist, C = calibrate(cfg)
    sh = Shocks(n, T, cfg['simulation']['seed'])
    engines = {'t': gen_t(sh, C, ret, vol, ter)}
    if hist is not None:
        engines['bootstrap'] = gen_boot(sh, hist, C, ret, vol, ter)
    Rt = engines['t']
    lumps = E.make_lumps(cfg, n)
    cadd = E.speculative_topups(cfg, n) if cfg['speculative']['topup'] else None
    flows = E.make_flows(cfg, n)
    allocs = E.alloc_weights(cfg)
    rec = cfg['portfolio']['recommended']
    out = dict(assumptions=dict(returns=dict(zip(ASSETS, ret.tolist())), fees=dict(zip(ASSETS, ter.tolist())),
                                vol=dict(zip(ASSETS, vol.tolist())), corr=np.round(C, 2).tolist(),
                                paths=n, calibration=cfg['market']['calibration']))

    # ---- mean-variance analysis on the six growth assets -------------------------------------
    cov = C * np.outer(vol, vol)
    mu = mpt.mu_arith(ret, vol, ter)
    hi = np.array([cfg['mpt_max_weight'].get(a, 0.0) for a in GROWTH])
    c6, m6, lo = cov[:6, :6], mu[:6], np.zeros(6)
    an = mpt.analyse(m6, c6, lo, hi, rf=mu[6])
    stat = lambda w: (float(np.sqrt(w @ c6 @ w)), float(w @ m6))
    named = {'max Sharpe (bounded)': an['max_sharpe'], 'minimum variance': an['min_var'],
             'resampled Sharpe (Michaud)': an['resampled'], 'risk parity (ERC)': an['erc']}
    allocs['Risk parity (unlevered)'] = np.append(an['erc'], 0.0)
    pts = {k: stat(w[:6] / w[:6].sum()) for k, w in allocs.items()}
    out['mpt'] = dict(portfolios={k: dict(zip(GROWTH, np.round(w, 3).tolist())) for k, w in named.items()},
                      stats={k: dict(vol=stat(w)[0], ret=stat(w)[1]) for k, w in named.items()},
                      frontier=an['frontier'], candidate_points=pts, arith_returns=dict(zip(ASSETS, np.round(mu, 4).tolist())))

    # ---- Monte Carlo grid ---------------------------------------------------------------------
    base_names = {'Do nothing (idle cash stays, new money keeps the mix)':
                  E.baseline_state(cfg, np.array([cfg['portfolio']['holdings'].get(a, 0) for a in ASSETS[:7]], float), cadd=cadd),
                  'All new money into global equity (cash and rest stay)':
                  E.baseline_state(cfg, [1, 0, 0, 0, 0, 0, 0], cadd=cadd)}
    for st in base_names.values():
        st['w0'] = st['w0'] / st['w0'].sum()
    grid, res_keep = [], {}
    for eng, R in engines.items():
        for name in list(allocs) + list(base_names):
            st = base_names[name] if name in base_names else E.start_state(cfg, allocs[name], cadd=cadd)
            r = E.run(cfg, R, flows, lumps, st)
            s = E.summarize(cfg, r)
            grid.append(dict(engine=eng, portfolio=name, **_clean(s)))
            if eng == 't':
                res_keep[name] = (r, s)
        log(f"[{eng}] {len(allocs) + len(base_names)} portfolios simulated")
    out['grid'] = grid

    # ---- sensitivities and stress on the recommended allocation -------------------------------
    st0 = E.start_state(cfg, allocs[rec], cadd=cadd)
    go = lambda R=Rt, fl=None, lp=None, st=None: E.summarize(cfg, E.run(cfg, R, flows if fl is None else fl,
                                                                         lumps if lp is None else lp, st0 if st is None else st))
    c0 = cfg['income']['monthly_contribution']
    lower, higher = np.array([-.02, -.02, -.02, -.005, -.005, -.015, -.005, -.02]), np.array([.02, .02, .02, .005, .005, .01, .005, .02])
    Cst = C.copy()
    for i in (3, 4):
        for j in (0, 1, 2):
            Cst[i, j] = Cst[j, i] = .6
    Cst = psd(Cst)
    sens = {'base': go()}
    for k in (.75, 1.25, 1.5):
        sens[f"contributions ${c0 * k:,.0f}/month"] = go(fl=E.make_flows(cfg, n, c_mult=k))
    sens['contributions grow 3%/year'] = go(fl=E.make_flows(cfg, n, growth=.03))
    sens['income gaps 3x as frequent'] = go(fl=E.make_flows(cfg, n, gap_p=3 * cfg['income']['gap_start_prob']))
    sens['returns 2pp lower (equities), bonds -0.5, gold -1.5'] = go(R=gen_t(sh, C, ret + lower, vol, ter))
    sens['returns 2pp higher (equities)'] = go(R=gen_t(sh, C, ret + higher, vol, ter))
    sens['stock-bond correlation 0.6'] = go(R=gen_t(sh, Cst, ret, vol, ter))
    if 'bootstrap' in engines:
        sens['historical bootstrap instead of Student-t'] = go(R=engines['bootstrap'])
    if cfg['speculative']['start'] or cfg['speculative']['topup']:
        sens['no speculative top-ups'] = go(st=E.start_state(cfg, allocs[rec], cadd=None))
        sens['speculative sleeve goes to zero today (no top-ups)'] = go(st=E.start_state(cfg, allocs[rec], cadd=None, HC0=0.0))
        sens['speculative bull case (median +8%/year)'] = go(R=gen_t(sh, C, np.r_[ret[:7], .08], vol, ter))
    if cfg['lump_sums']:
        sens['one-off inflows 25% smaller'] = go(lp=E.make_lumps(cfg, n, scale=.75))
    out['sensitivity'] = {k: _clean(v) for k, v in sens.items()}

    stress = {}
    if T >= 48:
        stress['2008-style crash in year 2'] = go(R=_shock(Rt, 24, 36, GFC))
        stress['2022-style stocks+bonds crash in year 2'] = go(R=_shock(Rt, 24, 36, STAGFLATION))
        stress['2008-style crash 2.5 years before the horizon'] = go(R=_shock(Rt, T - 30, T - 18, GFC))
        R3 = Rt.copy()
        g = np.array([.0, .01, .01, .03, .03, .03, .03, .0])
        R3[:, 12:min(132, T), :] += ((np.log1p(g - ter) - np.log1p(ret - ter)) / 12).astype(np.float32)
        stress['lost decade: equities ~0%/year for years 2-11'] = go(R=R3)
    out['stress'] = {k: _clean(v) for k, v in stress.items()}

    ladder = {}
    for k in (.6, .8, 1.0, 1.2, 1.4, 1.6):
        s = E.summarize(cfg, E.run(cfg, Rt, E.make_flows(cfg, n, c_mult=k), lumps, st0))
        ladder[f"{c0 * k:.0f}"] = _clean(s)
    out['contribution_ladder'] = ladder

    memo = {}
    W = res_keep[rec][0]['W'].astype(float)
    for pi in (.015, .025, .035, .045):
        real = W / ((1 + pi) ** (np.arange(T + 1) / 12))
        memo[f"{pi * 100:.1f}"] = float((real[:, 1:] >= goal).any(1).mean())
    out['inflation_sensitivity'] = memo

    # ---- risk budget (variance shares) --------------------------------------------------------
    h = cfg['portfolio']['holdings']
    cur = np.array([float(h.get(a, 0)) for a in ASSETS[:7]] + [float(cfg['speculative']['start'])])
    H0 = E.alloc(np.array([st0['H0']]), allocs[rec], np.array([st0['excess']]))[0]
    new = np.append(H0, st0['HC0'])
    new[6] += st0['HR0']
    rr = res_keep[rec][0]
    fin = np.append(np.median(rr['H'], 0), np.median(rr['HC']))
    fin[6] += np.median(rr['HR'])
    risk = {}
    for k, v in (('as_is', cur), ('after_plan', new), ('median_at_horizon', fin)):
        v = v / v.sum()
        risk[k] = dict(weights=np.round(v, 3).tolist(), risk_share=np.round(mpt.risk_contrib(v, cov), 3).tolist(),
                       vol=float(np.sqrt(v @ cov @ v)))
    out['risk'] = risk
    out['meta'] = dict(recommended=rec, goal=goal, horizon_years=cfg['goal']['horizon_years'],
                       inflation=cfg['goal']['inflation'], paths=n)
    extras = dict(W=res_keep[rec][0]['W'], summary=res_keep[rec][1], an=an, cov6=c6, mu6=m6, pts=pts)
    return out, extras
