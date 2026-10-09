#!/usr/bin/env python3
"""Run a plan: python3 run.py examples/example.toml [--quick] [--out results]"""
import argparse
import time

from planner import analysis, config, report


def main():
    ap = argparse.ArgumentParser(description="Goal-based portfolio planner (Monte Carlo + mean-variance)")
    ap.add_argument('config', nargs='?', help='TOML plan file (defaults are used if omitted)')
    ap.add_argument('--quick', action='store_true', help='3,000 paths instead of the configured number')
    ap.add_argument('--out', default='results', help='output folder (summary.md, results.json, charts/)')
    a = ap.parse_args()
    cfg = config.load(a.config)
    t0 = time.time()
    out, ex = analysis.run_all(cfg, quick=a.quick)
    report.write_all(cfg, out, ex, a.out)
    print(f"done in {time.time() - t0:.0f}s -> {a.out}/summary.md")


if __name__ == '__main__':
    main()
