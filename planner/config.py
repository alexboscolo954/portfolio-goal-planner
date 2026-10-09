"""Plan configuration: TOML file merged over defaults."""
import copy
import tomllib

ASSETS = ['global_equity', 'em_equity', 'small_value', 'bonds', 'tips', 'gold', 'cash', 'speculative']
GROWTH = ASSETS[:6]                      # rebalanced sleeve; 'cash' is the reserve, 'speculative' a fixed block
LABELS = ['Global equity', 'EM equity', 'Small value', 'Bonds', 'TIPS', 'Gold', 'Cash', 'Speculative']

# (compound annual return before fees, annual volatility, annual fee). Defaults are a rough blend of public
# 10-15 year capital market assumptions published in 2026 (JPMorgan LTCMA, Vanguard VCMM, BlackRock CMA).
# Replace them with your own views in the [assumptions.<asset>] tables.
ASSUMPTIONS = {
    'global_equity': (.065, .165, .0015),
    'em_equity': (.0675, .225, .0020),
    'small_value': (.070, .185, .0035),
    'bonds': (.046, .050, .0010),
    'tips': (.046, .055, .0010),
    'gold': (.045, .165, .0015),
    'cash': (.037, .005, .0007),
    'speculative': (.000, .550, .0),
}

DEFAULTS = {
    'goal': {'amount': 1_000_000, 'horizon_years': 15, 'inflation': 0.025},
    'income': {'monthly_contribution': 3200, 'contribution_noise': 0.10, 'annual_raise': 0.0,
               'gap_start_prob': 0.008, 'gap_months': [2, 6]},
    'liabilities': [],                   # [[liabilities]] name, monthly, months: still due during an income gap
    'lump_sums': [],                     # [[lump_sums]] month, amount_foreign, fx_start, fx_vol, fx_drift, conversion_cost, amount_sd
    'portfolio': {'reserve': 20_000, 'reserve_step_month': 0, 'reserve_step_amount': 0.0,
                  'no_sell_until_month': 0, 'rebalance_cost': 0.001, 'cash_haircut': 0.009,
                  'recommended': 'Balanced 80/20',
                  'holdings': {'global_equity': 45_000, 'gold': 6_000, 'cash': 25_000}},
    'speculative': {'start': 0.0, 'topup': 0.0, 'topup_every_months': 3},
    'market': {'calibration': 'default', 'cache': 'data/prices.csv'},   # "default" needs no network
    'simulation': {'paths': 10_000, 'seed': 1},
    'assumptions': {},
    'mpt_max_weight': {'global_equity': 1.0, 'em_equity': .20, 'small_value': .25, 'bonds': .60, 'tips': .30, 'gold': .15},
    'allocations': [
        {'name': 'All global equity', 'global_equity': 1.0},
        {'name': 'Growth 90/10', 'global_equity': .65, 'em_equity': .09, 'small_value': .16, 'bonds': .04, 'gold': .06},
        {'name': 'Balanced 80/20', 'global_equity': .58, 'em_equity': .08, 'small_value': .14, 'bonds': .13, 'gold': .07},
        {'name': 'Moderate 65/35', 'global_equity': .47, 'em_equity': .06, 'small_value': .12, 'bonds': .25, 'gold': .10},
        {'name': 'Conservative 50/50', 'global_equity': .36, 'em_equity': .05, 'small_value': .09, 'bonds': .35, 'gold': .15},
    ],
}


def _merge(base, over):
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v          # lists (allocations, liabilities, lump sums) replace, not merge
    return base


def load(path=None):
    cfg = copy.deepcopy(DEFAULTS)
    if path:
        with open(path, 'rb') as f:
            _merge(cfg, tomllib.load(f))
    cfg['T'] = int(cfg['goal']['horizon_years'] * 12)
    ret, vol, ter = (list(x) for x in zip(*(ASSUMPTIONS[a] for a in ASSETS)))
    for i, a in enumerate(ASSETS):
        o = cfg['assumptions'].get(a, {})
        ret[i], vol[i], ter[i] = o.get('return', ret[i]), o.get('vol', vol[i]), o.get('ter', ter[i])
    cfg['ret'], cfg['vol'], cfg['ter'] = ret, vol, ter
    names = [a['name'] for a in cfg['allocations']]
    if cfg['portfolio']['recommended'] not in names:
        cfg['portfolio']['recommended'] = names[0]
    return cfg


def weights(entry):
    """Allocation entry -> normalised weight vector over the 7 holdable assets (6 growth + cash)."""
    w = [float(entry.get(a, 0.0)) for a in ASSETS[:7]]
    s = sum(w)
    if s <= 0:
        raise ValueError(f"allocation {entry.get('name')!r} has no weights")
    return [x / s for x in w]
