"""Cash flows, portfolio rules and result metrics (all vectorised over simulated paths)."""
import numpy as np

from .config import ASSETS, weights


def milestones(H):
    return sorted({y for y in (5, 8, 10, 12, 15, 20, 25) if y < H} | {H})


def make_flows(cfg, n, c_mult=1.0, growth=None, gap_p=None, seed=11):
    """Monthly net flow into the portfolio: contribution with noise, minus fixed liabilities during income gaps.
    Liabilities are normally paid from income (outside the portfolio) but still fall due while income is zero."""
    inc, T = cfg['income'], cfg['T']
    growth = inc['annual_raise'] if growth is None else growth
    gap_p = inc['gap_start_prob'] if gap_p is None else gap_p
    lo, hi = inc['gap_months']
    r = np.random.default_rng(seed)
    m = np.arange(1, T + 1)
    c = inc['monthly_contribution'] * c_mult * np.clip(1 + inc['contribution_noise'] * r.standard_normal((n, T)), .5, 1.5) \
        * (1 + growth) ** (m / 12)
    starts, dur = r.random((n, T)) < gap_p, r.integers(lo, hi + 1, (n, T))
    rem, gap = np.zeros(n, int), np.zeros((n, T), bool)
    for t in range(T):
        rem = np.where((rem == 0) & starts[:, t], dur[:, t], rem)
        gap[:, t] = rem > 0
        rem = np.maximum(rem - 1, 0)
    liab = np.zeros(T)
    for L in cfg['liabilities']:
        liab += np.where(m <= L['months'], float(L['monthly']), 0.0)
    return np.where(gap, -liab, c).astype(np.float64)


def make_lumps(cfg, n, scale=1.0, seed=12):
    """One-off foreign-currency inflows converted to USD at a random exchange rate (lognormal), net of costs."""
    T = cfg['T']
    out = np.zeros((n, T))
    r = np.random.default_rng(seed)
    for L in cfg['lump_sums']:
        tau = L['month'] / 12
        if L['month'] > T:
            continue
        S = L['fx_start'] * np.exp((L.get('fx_drift', .02) - .5 * L.get('fx_vol', .14) ** 2) * tau
                                   + L.get('fx_vol', .14) * np.sqrt(tau) * r.standard_normal(n))
        amt = L['amount_foreign'] * np.exp(L.get('amount_sd', 0.0) * r.standard_normal(n) - .5 * L.get('amount_sd', 0.0) ** 2)
        out[:, L['month'] - 1] += scale * amt / S * (1 - L.get('conversion_cost', .015))
    return out


def speculative_topups(cfg, n):
    s, T = cfg['speculative'], cfg['T']
    m = np.arange(1, T + 1)
    row = np.where((m % int(s['topup_every_months']) == 0), float(s['topup']), 0.0)
    return np.tile(row, (n, 1))


def alloc(H, w, f):
    """Put net flow f (n,) into holdings H (n,7): buy underweights first; on an outflow sell overweights first."""
    V = H.sum(1) + f
    gap = w * V[:, None] - H
    inn, out = np.clip(f, 0, None), np.clip(-f, 0, None)
    pos = np.clip(gap, 0, None)
    s = pos.sum(1)
    buy = pos * np.where(s > 0, np.minimum(1, inn / np.maximum(s, 1e-9)), 0)[:, None]
    buy += (inn - buy.sum(1))[:, None] * w
    ov = np.clip(-gap, 0, None)
    so = ov.sum(1)
    sell = ov * np.where(so > 0, np.minimum(1, out / np.maximum(so, 1e-9)), 0)[:, None]
    sell += (out - sell.sum(1))[:, None] * H / np.maximum(H.sum(1, keepdims=True), 1e-9)
    return np.clip(H + buy - sell, 0, None)


def start_state(cfg, w0, **over):
    """Starting point under the plan: cash above the reserve is deployed to underweight assets; nothing is sold
    before `no_sell_until_month` (new money does the rebalancing instead)."""
    p = cfg['portfolio']
    h = p['holdings']
    H0 = np.array([float(h.get(a, 0)) for a in ASSETS[:6]] + [0.0])
    cash = float(h.get('cash', 0))
    res = min(float(p['reserve']), cash)
    s = dict(H0=H0, HR0=res, HC0=float(cfg['speculative']['start']), R0=float(p['reserve']),
             Rx=float(p['reserve_step_amount']), Rx_month=int(p['reserve_step_month']), cadd=None,
             w0=np.array(w0, float), rebalance=True, excess=cash - res)
    s.update(over)
    return s


def baseline_state(cfg, wflow, **over):
    """Do-nothing style baselines: no reserve concept, existing cash kept as a (lower-yielding) holding."""
    h = cfg['portfolio']['holdings']
    H0 = np.array([float(h.get(a, 0)) for a in ASSETS[:7]])
    s = dict(H0=H0, HR0=0.0, HC0=float(cfg['speculative']['start']), R0=0.0, Rx=0.0, Rx_month=0, cadd=None,
             w0=np.array(wflow, float), rebalance=False, excess=0.0, cash_drag=cfg['portfolio']['cash_haircut'])
    s.update(over)
    return s


def run(cfg, R, flows, lumps, st):
    n, T = R.shape[0], cfg['T']
    infl = cfg['goal']['inflation']
    cost_rate = cfg['portfolio']['rebalance_cost']
    reb_from = max(int(cfg['portfolio']['no_sell_until_month']), 1)
    H = np.tile(st['H0'], (n, 1)).astype(float)
    HR, HC = np.full(n, st['HR0'], float), np.full(n, st['HC0'], float)
    w0, w1, g0, g1 = st['w0'], st.get('w1', st['w0']), st.get('g0', 96), st.get('g1', T)
    wt = lambda m: w0 + (w1 - w0) * np.clip((m - g0) / max(g1 - g0, 1), 0, 1)
    drag = np.exp(-st.get('cash_drag', 0) / 12)
    if st.get('excess', 0):
        H = alloc(H, wt(0), np.full(n, float(st['excess'])))
    V, Vc = H.sum(1) + HR + HC, H.sum(1) + HR
    W = np.empty((n, T + 1), np.float32)
    W[:, 0] = V
    ix, ixc = np.ones(n), np.ones(n)
    pk, pkc, dd, ddc = ix.copy(), ixc.copy(), np.zeros(n), np.zeros(n)
    for m in range(1, T + 1):
        r = np.exp(R[:, m - 1, :].astype(np.float64))
        H *= r[:, :7]
        H[:, 6] *= drag
        HR *= r[:, 6]
        HC *= r[:, 7]
        if st.get('cadd') is not None:
            HC = HC + st['cadd'][:, m - 1]
        Vn, Vcn = H.sum(1) + HR + HC, H.sum(1) + HR
        ix *= Vn / V
        ixc *= Vcn / Vc
        pk, pkc = np.maximum(pk, ix), np.maximum(pkc, ixc)
        dd, ddc = np.minimum(dd, ix / pk - 1), np.minimum(ddc, ixc / pkc - 1)
        f = flows[:, m - 1] + lumps[:, m - 1]
        tgt = st['R0'] * (1 + infl) ** (m / 12) + (st['Rx'] if st['Rx_month'] and m >= st['Rx_month'] else 0)
        top = np.where(f > 0, np.minimum(f, np.maximum(tgt - HR, 0)), 0)
        draw = np.where(f < 0, np.minimum(-f, HR), 0)
        HR = HR + top - draw
        H = alloc(H, wt(m), f - top + draw)
        if st.get('rebalance') and m >= reb_from and (m - reb_from) % 12 == 0:
            ex = np.maximum(HR - tgt, 0)
            HR = HR - ex
            Hn = wt(m) * (H.sum(1) + ex)[:, None]
            cost = cost_rate * np.abs(Hn - H).sum(1)
            H = Hn * (1 - cost / Hn.sum(1))[:, None]
        V, Vc = H.sum(1) + HR + HC, H.sum(1) + HR
        W[:, m] = V
    return dict(W=W, dd=dd, ddc=ddc, spec_share=HC / V, H=H, HR=HR, HC=HC)


def summarize(cfg, res):
    goal, T = cfg['goal']['amount'], cfg['T']
    H = cfg['goal']['horizon_years']
    infl_f = (1 + cfg['goal']['inflation']) ** (np.arange(T + 1) / 12)
    W = res['W']
    real = W / infl_f
    first = lambda h: np.where(h.any(1), h.argmax(1) + 1, np.inf) / 12
    fn, fr = first(W[:, 1:] >= goal), first(real[:, 1:] >= goal)
    q = [5, 25, 50, 75, 95]
    cl = lambda x: None if not np.isfinite(x) else round(float(x), 1)
    ms = milestones(H)
    return dict(
        end_nom=np.percentile(W[:, -1], q).round(-3).tolist(), end_real=np.percentile(real[:, -1], q).round(-3).tolist(),
        p_end_nom=float((W[:, -1] >= goal).mean()), p_end_real=float((real[:, -1] >= goal).mean()),
        t_nom=cl(np.median(fn)), t_real=cl(np.median(fr)),
        by_nom={y: round(float((fn <= y).mean()), 3) for y in ms}, by_real={y: round(float((fr <= y).mean()), 3) for y in ms},
        dd_med=float(np.median(res['dd'])), dd_bad=float(np.percentile(res['dd'], 10)),
        ddc_med=float(np.median(res['ddc'])), ddc_bad=float(np.percentile(res['ddc'], 10)),
        cvar5_real=float(np.sort(real[:, -1])[:max(1, len(real) // 20)].mean()),
        spec_share=np.percentile(res['spec_share'], [50, 95]).round(3).tolist(), fn=fn, fr=fr)


def alloc_weights(cfg):
    return {a['name']: np.array(weights(a)) for a in cfg['allocations']}
