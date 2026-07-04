#!/usr/bin/env python3
"""
performance_analysis.py — the statistical core of the n-of-1 performance engine.

Pure standard library (no numpy/pandas) so it runs wherever graph_linker.py does.
Called by Night Watch at an experiment's planned endpoint. It does NOT decide on its
own to write the operating model — it returns a verdict recommendation; the user
approves the write at weekly review.

Inputs:
  11_dashboard/performance_log.csv      — the daily rows
  11_dashboard/active_protocol.json     — the current experiment definition (optional)

Output:
  prints a JSON result to stdout AND writes 11_dashboard/performance_analysis_result.json

Decision rule (see 11_dashboard/performance_experiments.md):
  - min data: >=24 days, >=10 valid per arm, >=80% adherence-known
  - minimally important effect: >=0.3 within-person SD OR >=0.5 pt (7-pt) OR >=10% objective
  - Confirmed: randomization-test p<.05 AND effect >= threshold (replication still advised)
  - Irrelevant: data sufficient, effect small, p high
  - Inconclusive: everything else; forced if missingness >20% or condition-imbalanced
"""

import csv, json, os, sys, random, statistics, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(ROOT, "11_dashboard", "performance_log.csv")
PROTO = os.path.join(ROOT, "11_dashboard", "active_protocol.json")
OUT = os.path.join(ROOT, "11_dashboard", "performance_analysis_result.json")

OUTCOMES = ["energy", "focus", "follow_through", "mood", "stress"]
OBSERVATIONAL_LEVERS = ["sleep_quality", "caffeine", "alcohol", "nutrition",
                        "late_screens", "outdoors", "social"]
PERMUTATIONS = 10000
MIN_DAYS, MIN_PER_ARM, MIN_ADHERENCE_KNOWN = 24, 10, 0.80
EFFECT_SD, EFFECT_PT, EFFECT_OBJ = 0.30, 0.5, 0.10  # minimally important difference


def load_rows():
    if not os.path.exists(LOG):
        return []
    with open(LOG, newline="", encoding="utf-8") as f:
        return [r for r in csv.DictReader(f) if r.get("date", "").strip()]


def fnum(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def within_sd(vals):
    vals = [v for v in vals if v is not None]
    return statistics.pstdev(vals) if len(vals) > 1 else None


def randomization_test(a_vals, b_vals):
    """Two-sided permutation test on the mean difference (assignment-based)."""
    a = [v for v in a_vals if v is not None]
    b = [v for v in b_vals if v is not None]
    if len(a) < 2 or len(b) < 2:
        return None, None
    obs = statistics.mean(a) - statistics.mean(b)
    pool = a + b
    na = len(a)
    hits = 0
    rng = random.Random(20260617)  # fixed seed → reproducible verdicts
    for _ in range(PERMUTATIONS):
        rng.shuffle(pool)
        d = statistics.mean(pool[:na]) - statistics.mean(pool[na:])
        if abs(d) >= abs(obs) - 1e-12:
            hits += 1
    return obs, (hits + 1) / (PERMUTATIONS + 1)


def analyze_manipulated(rows, proto):
    primary = proto.get("primary_outcome", "focus")
    co = proto.get("objective_co_primary", "deep_work_pct")
    pid = proto.get("protocol_id", "?")

    # valid = outcome present AND adherence known (yes/partial/no, not blank/unknown/na-without-assignment)
    valid = [r for r in rows if r.get("active_protocol") == pid
             and r.get("condition") in ("A", "B")
             and fnum(r.get(primary)) is not None]
    adher_known = [r for r in valid if (r.get("adherence", "").strip().lower()
                                        in ("yes", "partial", "no"))]
    a = [r for r in valid if r["condition"] == "A"]
    b = [r for r in valid if r["condition"] == "B"]
    total_assigned = len([r for r in rows if r.get("active_protocol") == pid
                          and r.get("condition") in ("A", "B")])
    missing = 1 - (len(valid) / total_assigned) if total_assigned else 1.0
    adh_rate = (len(adher_known) / len(valid)) if valid else 0.0

    res = {"protocol_id": pid, "mode": "manipulated", "design": proto.get("design"),
           "primary_outcome": primary, "objective_co_primary": co,
           "valid_days": len(valid), "n_A": len(a), "n_B": len(b),
           "adherence_known_rate": round(adh_rate, 2), "missing_rate": round(missing, 2)}

    # --- gates ---
    if len(valid) < MIN_DAYS or len(a) < MIN_PER_ARM or len(b) < MIN_PER_ARM:
        res.update(verdict="Collecting",
                   note=f"Need >={MIN_DAYS} valid days & >={MIN_PER_ARM}/arm "
                        f"(have {len(valid)}; A={len(a)}, B={len(b)}).")
        return res
    if adh_rate < MIN_ADHERENCE_KNOWN:
        res.update(verdict="Inconclusive",
                   note=f"Adherence known on {adh_rate:.0%} of days (<{MIN_ADHERENCE_KNOWN:.0%}).")
        return res

    # --- primary effect + randomization test ---
    sd = within_sd([fnum(r.get(primary)) for r in valid])
    obs, p = randomization_test([fnum(r.get(primary)) for r in a],
                                [fnum(r.get(primary)) for r in b])
    eff_sd = (obs / sd) if (obs is not None and sd) else None
    res.update(primary_mean_diff=round(obs, 3) if obs is not None else None,
               primary_within_sd=round(sd, 3) if sd else None,
               primary_effect_in_sd=round(eff_sd, 3) if eff_sd is not None else None,
               primary_rand_p=round(p, 4) if p is not None else None)

    # --- objective co-primary (relative change) ---
    co_a = [fnum(r.get(co)) for r in a if fnum(r.get(co)) is not None]
    co_b = [fnum(r.get(co)) for r in b if fnum(r.get(co)) is not None]
    co_rel = None
    if co_a and co_b and statistics.mean(co_b) not in (0, None):
        co_rel = (statistics.mean(co_a) - statistics.mean(co_b)) / abs(statistics.mean(co_b))
        res["objective_rel_change"] = round(co_rel, 3)

    # --- missingness gate ---
    if missing > 0.20:
        res.update(verdict="Inconclusive",
                   note=f"Missingness {missing:.0%} (>20%). Sensitivity required before any claim.")
        return res

    # --- decision ---
    meaningful = ((eff_sd is not None and abs(eff_sd) >= EFFECT_SD)
                  or (obs is not None and abs(obs) >= EFFECT_PT)
                  or (co_rel is not None and abs(co_rel) >= EFFECT_OBJ))
    strong = (p is not None and p < 0.05)
    sign_agree = (eff_sd is None or co_rel is None or (eff_sd >= 0) == (co_rel >= 0))

    if strong and meaningful and sign_agree:
        res.update(verdict="Confirmed",
                   note="Randomized, adhered, effect clears the minimally-important bar, "
                        "p<.05, primary/objective signs agree. REPLICATION still advised; "
                        "interpret as open-label (intervention-as-experienced).")
    elif not meaningful and (p is None or p >= 0.20):
        res.update(verdict="Irrelevant",
                   note="Sufficient data; no materially beneficial or harmful effect detected.")
    else:
        res.update(verdict="Inconclusive",
                   note="Effect small relative to variance, signs disagree, or evidence weak.")
    if missing > 0.10:
        res["sensitivity_note"] = f"Missingness {missing:.0%} (>10%) — verdict carries a caveat."
    return res


def analyze_observational(rows):
    """Rolling lagged associations over recent valid days — ranked hypotheses ONLY."""
    recent = rows[-90:]
    out = []
    for lever in OBSERVATIONAL_LEVERS:
        for oc in OUTCOMES:
            pairs = [(fnum(r.get(lever)), fnum(r.get(oc))) for r in recent]
            pairs = [(x, y) for x, y in pairs if x is not None and y is not None]
            if len(pairs) < 20:
                continue
            xs, ys = [p[0] for p in pairs], [p[1] for p in pairs]
            try:
                r = statistics.correlation(xs, ys)  # py3.10+
            except (statistics.StatisticsError, ValueError, AttributeError):
                continue
            out.append({"lever": lever, "outcome": oc, "n": len(pairs),
                        "association_r": round(r, 3)})
    out.sort(key=lambda d: abs(d["association_r"]), reverse=True)
    return {"mode": "observational", "note": "Suggestive associations only — NOT causal, "
            "NOT eligible for Confirmed. Candidates for future randomized tests.",
            "top_associations": out[:10]}


def main():
    rows = load_rows()
    result = {"generated": datetime.datetime.now().isoformat(timespec="seconds"),
              "rows_in_log": len(rows)}
    proto = None
    if os.path.exists(PROTO):
        try:
            with open(PROTO, encoding="utf-8") as f:
                proto = json.load(f)
        except (json.JSONDecodeError, OSError):
            proto = None

    if proto and proto.get("class") in ("interventional", "support-randomizable"):
        result["experiment"] = analyze_manipulated(rows, proto)
    else:
        result["experiment"] = {"verdict": "No active manipulated protocol."}
    result["observational"] = analyze_observational(rows)

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
