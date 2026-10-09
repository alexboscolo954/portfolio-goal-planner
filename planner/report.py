"""Charts, JSON and a markdown summary generated straight from the results (no hand-copied numbers)."""
import json
import os
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

from .config import GROWTH, LABELS

plt.rcParams.update({'font.size': 10, 'axes.spines.top': False, 'axes.spines.right': False})


def _years(cfg):
    return np.arange(cfg['T'] + 1) / 12


def charts(cfg, out, ex, outdir):
    os.makedirs(outdir, exist_ok=True)
    T, goal, infl = cfg['T'], cfg['goal']['amount'], cfg['goal']['inflation']
    yrs, f = _years(cfg), (1 + infl) ** (np.arange(T + 1) / 12)
    fig, ax = plt.subplots(1, 2, figsize=(12, 4.4))
    for a, real in zip(ax, (False, True)):
        p = np.percentile(ex['W'] / (f if real else 1), [5, 25, 50, 75, 95], axis=0) / 1e3
        a.fill_between(yrs, p[0], p[4], color='#9ecae1', alpha=.45, label='5-95%')
        a.fill_between(yrs, p[1], p[3], color='#4292c6', alpha=.55, label='25-75%')
        a.plot(yrs, p[2], color='#08306b', lw=2, label='median')
        a.axhline(goal / 1e3, color='#d94801', lw=1.2)
        a.set_title('Total wealth, ' + ("today's dollars" if real else 'nominal'))
        a.set_xlabel('years'); a.set_ylabel('$ thousand'); a.legend(loc='upper left', fontsize=8, frameon=False)
        a.set_ylim(0, p[4, -1] * 1.05)
    plt.tight_layout(); plt.savefig(os.path.join(outdir, 'fan.png'), dpi=140); plt.close()

    s = ex['summary']
    fig, ax = plt.subplots(figsize=(6.4, 4.2))
    x = np.arange(.25, cfg['goal']['horizon_years'] + .01, .25)
    for key, ls, lab in (('fn', '-', 'nominal goal'), ('fr', ':', "goal in today's dollars")):
        ax.plot(x, [(s[key] <= y).mean() * 100 for y in x], ls, color='#08306b', lw=2, label=lab)
    ax.set_xlabel('years'); ax.set_ylabel('% of paths that reached the goal'); ax.set_ylim(0, 100)
    ax.grid(alpha=.25); ax.legend(fontsize=8, frameon=False); ax.set_title('Time to goal')
    plt.tight_layout(); plt.savefig(os.path.join(outdir, 'time_to_goal.png'), dpi=140); plt.close()

    fig, ax = plt.subplots(figsize=(6.6, 4.5))
    fr = np.array(ex['an']['frontier'])
    ax.plot(fr[:, 0] * 100, fr[:, 1] * 100, color='#08306b', lw=2, label='bounded long-only frontier')
    sd = np.sqrt(np.diag(ex['cov6']))
    for i, a in enumerate(GROWTH):
        ax.scatter(sd[i] * 100, ex['mu6'][i] * 100, color='#969696')
        ax.annotate(LABELS[i], (sd[i] * 100, ex['mu6'][i] * 100), fontsize=8, xytext=(4, 9 if a == 'tips' else 3), textcoords='offset points')
    for k, (v, r) in ex['pts'].items():
        rec = k == out['meta']['recommended']
        ax.scatter(v * 100, r * 100, color='#d94801' if rec else '#fd8d3c', marker='D', s=70 if rec else 40)
        ax.annotate(k, (v * 100, r * 100), fontsize=7, xytext={'Growth 90/10': (-52, 8), 'All global equity': (6, -20)}.get(k, (4, -10)), textcoords='offset points')
    ax.set_xlabel('volatility % per year'); ax.set_ylabel('expected arithmetic return % per year')
    ax.grid(alpha=.25); ax.legend(fontsize=8, frameon=False, loc='lower right')
    plt.tight_layout(); plt.savefig(os.path.join(outdir, 'frontier.png'), dpi=140); plt.close()

    fig, ax = plt.subplots(figsize=(7.2, 3.4))
    names = {'as_is': 'As is today', 'after_plan': 'After the plan', 'median_at_horizon': 'Median at horizon'}
    cols = ['#08306b', '#2171b5', '#6baed6', '#9ecae1', '#c6dbef', '#fdae6b', '#bdbdbd', '#d94801']
    bot = np.zeros(3)
    for j in range(8):
        v = np.array([out['risk'][k]['risk_share'][j] for k in names]) * 100
        if v.sum() < .05:
            continue
        ax.barh(list(names.values()), v, left=bot, color=cols[j], label=LABELS[j]); bot += v
    ax.invert_yaxis(); ax.set_xlabel('% of total portfolio risk (variance)')
    ax.legend(ncol=4, fontsize=7, frameon=False, loc='upper center', bbox_to_anchor=(.5, -.2))
    plt.tight_layout(); plt.savefig(os.path.join(outdir, 'risk.png'), dpi=140); plt.close()


def _pct(x):
    return f"{x * 100:.1f}%"


def _yr(x):
    return '>horizon' if x is None else f"{x:.1f}"


def summary_md(cfg, out):
    H, goal = cfg['goal']['horizon_years'], cfg['goal']['amount']
    rec = out['meta']['recommended']
    k = lambda d, y: d[str(y)] if str(y) in d else d[y]
    g = {(r['engine'], r['portfolio']): r for r in out['grid']}
    b = g[('t', rec)]
    L = [f"# Results: {rec}", "",
         f"Goal ${goal:,.0f} in {H} years, inflation {cfg['goal']['inflation'] * 100:.1f}%, {out['meta']['paths']:,} paths, "
         f"calibration `{out['assumptions']['calibration']}`.", "",
         "| | Today's dollars | Nominal |", "|---|---|---|",
         f"| Median years to goal | {_yr(b['t_real'])} | {_yr(b['t_nom'])} |",
         f"| Reached within {H} years | {_pct(k(b['by_real'], H))} | {_pct(k(b['by_nom'], H))} |",
         f"| Wealth at horizon, p5 / p50 / p95 | ${b['end_real'][0] / 1e3:,.0f}k / ${b['end_real'][2] / 1e3:,.0f}k / ${b['end_real'][4] / 1e3:,.0f}k "
         f"| ${b['end_nom'][0] / 1e3:,.0f}k / ${b['end_nom'][2] / 1e3:,.0f}k / ${b['end_nom'][4] / 1e3:,.0f}k |", "",
         "## Allocations compared (Student-t engine)", "",
         "| Portfolio | Reached (today's $) | Reached (nominal) | Median years (today's $) | Wealth p5 / p50 (today's $) | Core drawdown typical / bad 1-in-10 |",
         "|---|---|---|---|---|---|"]
    for r in out['grid']:
        if r['engine'] != 't':
            continue
        L.append(f"| {r['portfolio']}{' **(recommended)**' if r['portfolio'] == rec else ''} | {_pct(k(r['by_real'], H))} | {_pct(k(r['by_nom'], H))} "
                 f"| {_yr(r['t_real'])} | ${r['end_real'][0] / 1e3:,.0f}k / ${r['end_real'][2] / 1e3:,.0f}k "
                 f"| {r['ddc_med'] * 100:.0f}% / {r['ddc_bad'] * 100:.0f}% |")
    L += ["", "## Sensitivities (recommended allocation)", "", "| Change | Reached (today's $) | Reached (nominal) | Median years (today's $) |", "|---|---|---|---|"]
    for name, r in out['sensitivity'].items():
        L.append(f"| {name} | {_pct(k(r['by_real'], H))} | {_pct(k(r['by_nom'], H))} | {_yr(r['t_real'])} |")
    L += ["", "## Contribution ladder", "", "| Monthly contribution | Reached (today's $) | Reached (nominal) | Median years (today's $) |", "|---|---|---|---|"]
    for c, r in out['contribution_ladder'].items():
        L.append(f"| ${float(c):,.0f} | {_pct(k(r['by_real'], H))} | {_pct(k(r['by_nom'], H))} | {_yr(r['t_real'])} |")
    if out['stress']:
        L += ["", "## Stress tests", "", "| Shock | Reached (today's $) | Reached (nominal) | Median years (nominal) |", "|---|---|---|---|"]
        for name, r in out['stress'].items():
            L.append(f"| {name} | {_pct(k(r['by_real'], H))} | {_pct(k(r['by_nom'], H))} | {_yr(r['t_nom'])} |")
    L += ["", "## Mean-variance portfolios (growth assets)", "", "| Portfolio | Volatility | Expected return | Weights |", "|---|---|---|---|"]
    for name, w in out['mpt']['portfolios'].items():
        s = out['mpt']['stats'][name]
        L.append(f"| {name} | {s['vol'] * 100:.1f}% | {s['ret'] * 100:.1f}% | " + ", ".join(f"{a} {v * 100:.0f}%" for a, v in w.items() if v >= .005) + " |")
    L += ["", "![fan](charts/fan.png)", "", "![time to goal](charts/time_to_goal.png)", "",
          "![frontier](charts/frontier.png)", "", "![risk](charts/risk.png)", ""]
    return "\n".join(L)


def write_all(cfg, out, ex, outdir):
    os.makedirs(outdir, exist_ok=True)
    charts(cfg, out, ex, os.path.join(outdir, 'charts'))
    with open(os.path.join(outdir, 'results.json'), 'w') as f:
        json.dump(out, f, indent=1, default=lambda o: float(o) if isinstance(o, (np.floating, np.integer)) else str(o))
    with open(os.path.join(outdir, 'summary.md'), 'w') as f:
        f.write(summary_md(cfg, out))
