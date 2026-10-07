"""
Adam — Health insights: what the last few weeks say, per Today card.

Each card on the Health page (Calories, Protein, Carbs · Fat · Meals, Water,
Weight, From your watch) expands into a detail view: a status, a one-line
read, a few averages, a 14-day series, what stands out, and how to improve.
Everything in that view is computed HERE, deterministically, from
health_store — same contract as health_metrics: the page does no arithmetic
and the model is not involved.

Rules that keep the read honest:
  * Today is never judged. It is still being logged, so it appears in the
    series (flagged) but never in an average or a status.
  * "No entry" is not "zero". A day without meals is unlogged, not a 0-kcal
    day. A day whose logged food is under half the calorie target is
    "partial" (a meal or two got logged, the rest didn't) and is left out of
    the averages too — it is counted, and said out loud, but never averaged.
  * A watch day with 0 steps is a day the watch wasn't worn.
  * Guidance is general wellness advice, not medical advice; the page says so.

Pure over the store, reference date always passed in (no wall clock), so the
results are reproducible and tests are deterministic.
"""

from __future__ import annotations

from datetime import date as _date, timedelta

import health_metrics as hm
import health_store as _default_store

WINDOW = 28          # days of history the averages draw on
CHART_DAYS = 14      # days shown in each detail chart
WEIGHT_DAYS = 90     # weigh-ins are sparser; look further back for a trend
PARTIAL_FRAC = 0.5   # logged kcal under this share of target = a partial day
STEP_GOAL = 8000

_STATUS_LABEL = {"good": "On track", "watch": "Worth a look", "bad": "Off track",
                 "none": "Needs data", "info": "For reference"}
_RANK = {"none": 0, "info": 0, "good": 1, "watch": 2, "bad": 3}


# --- small helpers ----------------------------------------------------------

def _d(s: str) -> _date:
    return _date.fromisoformat(s)


def _days_before(ref: str, n: int) -> list[str]:
    """The n complete calendar days before `ref`, oldest first (ref excluded)."""
    r = _d(ref)
    return [(r - timedelta(days=i)).isoformat() for i in range(n, 0, -1)]


def _axis(ref: str, n: int = CHART_DAYS) -> list[str]:
    """n calendar days ending ON ref (today included, flagged by the caller)."""
    r = _d(ref)
    return [(r - timedelta(days=i)).isoformat() for i in range(n - 1, -1, -1)]


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return (sum(xs) / len(xs)) if xs else None


def _n(x, nd: int = 0):
    if x is None:
        return None
    return int(round(x)) if nd == 0 else round(float(x), nd)


def _fmt(x, nd: int = 0) -> str:
    if x is None:
        return "—"
    if nd == 0:
        return f"{int(round(x)):,}"
    return f"{round(float(x), nd):,.{nd}f}"


def _stat(k: str, v: str, sub: str = "") -> dict:
    return {"k": k, "v": v, "sub": sub}


def _section(status: str, headline: str, **kw) -> dict:
    out = {"status": status, "label": _STATUS_LABEL[status], "headline": headline,
           "stats": [], "series": [], "target": None, "unit": "",
           "findings": [], "tips": []}
    out.update(kw)
    return out


def _worst(*statuses: str) -> str:
    best = "none"
    for s in statuses:
        if _RANK.get(s, 0) > _RANK.get(best, 0):
            best = s
    if best == "none" and any(s == "info" for s in statuses):
        return "info"
    return best


def _short(label: str) -> str:
    return label.replace("over the ", "")


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


# --- data gathering ---------------------------------------------------------

def _food_days(store, since: str) -> dict[str, dict]:
    """Per-day food totals + the meals themselves, from `since` onward."""
    days: dict[str, dict] = {}
    for m in store.list_meals():
        d = m.get("date") or ""
        if d < since:
            continue
        a = days.setdefault(d, {"kcal": 0.0, "protein_g": 0.0, "carbs_g": 0.0,
                                "fat_g": 0.0, "meals": 0, "items": []})
        for k in ("kcal", "protein_g", "carbs_g", "fat_g"):
            a[k] += float(m.get(k) or 0)
        a["meals"] += 1
        a["items"].append(m)
    return days


def _direction(latest: float | None, goal) -> str | None:
    """lose | gain | maintain from the goal vs the latest weigh-in."""
    if latest is None or not goal:
        return None
    diff = latest - float(goal)
    if diff > 1:
        return "lose"
    if diff < -1:
        return "gain"
    return "maintain"


# --- weight -----------------------------------------------------------------

def _weight(store, ref: str, settings: dict) -> dict:
    since = (_d(ref) - timedelta(days=WEIGHT_DAYS)).isoformat()
    pts = [w for w in store.list_weights(since=since) if w["date"] <= ref]
    goal = settings.get("weight_goal")
    unit = (pts[-1]["unit"] if pts else settings.get("weight_unit")) or "lb"
    # One point per day (the last weigh-in that day) so a re-weigh doesn't
    # double-count a day in the trend.
    by_day: dict[str, float] = {}
    for w in pts:
        by_day[w["date"]] = float(w["weight"])
    days = sorted(by_day)
    series = [{"date": d, "v": by_day[d]} for d in days]
    latest = by_day[days[-1]] if days else None
    direction = _direction(latest, goal)

    sec = _section("none", "Log a few weigh-ins to see where you're heading.",
                   series=series, target=goal, unit=unit, direction=direction,
                   rate_per_week=None, projected_date=None)
    if not days:
        sec["tips"] = ["Weigh in a few mornings a week — same time, after the bathroom, "
                       "before eating — and Adam will turn it into a trend."]
        return sec

    sec["stats"].append(_stat("Latest", f"{_fmt(latest, 1)} {unit}", days[-1]))
    if goal:
        sec["stats"].append(_stat("To goal", f"{_fmt(abs(latest - float(goal)), 1)} {unit}",
                                  f"goal {_fmt(goal, 1)}"))

    # Trend: least-squares slope over the window once there's enough spread;
    # two points a week+ apart give a plain difference instead.
    rate = None
    span = (_d(days[-1]) - _d(days[0])).days if len(days) > 1 else 0
    if len(days) >= 3 and span >= 14:
        xs = [(_d(d) - _d(days[0])).days for d in days]
        ys = [by_day[d] for d in days]
        mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
        den = sum((x - mx) ** 2 for x in xs)
        if den:
            rate = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den * 7
    elif len(days) >= 2 and span >= 7:
        rate = (by_day[days[-1]] - by_day[days[0]]) / span * 7
    sec["rate_per_week"] = _n(rate, 2)

    recent = [d for d in days if d >= (_d(ref) - timedelta(days=WINDOW)).isoformat()]
    gaps = [(_d(b) - _d(a)).days for a, b in zip(days, days[1:])]
    big_gap = max(gaps) if gaps else 0
    sec["stats"].append(_stat("Weigh-ins", str(len(recent)), f"last {WINDOW} days"))

    if rate is None:
        sec["headline"] = ("Not enough weigh-ins spread out yet to call a trend — "
                           "a week or two of regular weigh-ins will do it.")
        sec["tips"].append("Weigh in at least 3 mornings a week. Day-to-day numbers swing "
                           "1–3 lb with water and salt; the trend is what matters.")
        if big_gap >= 21:
            sec["findings"].append(f"There's a {big_gap}-day gap between weigh-ins.")
        return sec

    pct = abs(rate) / latest * 100 if latest else 0
    sign = "+" if rate > 0 else "−"
    sec["stats"].insert(1, _stat("Trend", f"{sign}{_fmt(abs(rate), 1)} {unit}/wk",
                                 f"{_fmt(pct, 1)}% of body weight"))
    toward = (direction == "lose" and rate < 0) or (direction == "gain" and rate > 0)
    flat = abs(rate) < (0.1 if unit == "lb" else 0.05)  # per week

    if direction in ("lose", "gain"):
        if toward and not flat:
            weeks = abs(latest - float(goal)) / abs(rate)
            eta = _d(days[-1]) + timedelta(days=round(weeks * 7))
            sec["projected_date"] = eta.isoformat()
            sec["stats"].append(_stat("Goal by", eta.strftime("%b %Y"), "at this pace"))
            if pct > 1.0:
                sec["status"] = "watch"
                sec["headline"] = (f"Moving toward your goal fast — about {_fmt(pct, 1)}% of "
                                   "body weight a week.")
                sec["findings"].append("Faster than ~1% a week usually costs muscle and is hard to keep up.")
                sec["tips"] += ["Keep protein high and keep lifting or doing resistance work — "
                                "that's what protects muscle while you lose.",
                                "Nudge calories up slightly; a steady 0.5–1% a week holds better."]
            else:
                sec["status"] = "good"
                sec["headline"] = (f"On pace: {_fmt(abs(rate), 1)} {unit} a week toward your goal.")
                sec["tips"].append("This is a sustainable pace. Keep weighing in so a stall shows up early.")
        elif flat:
            sec["status"] = "watch"
            sec["headline"] = "Weight has been flat — the trend isn't moving toward your goal."
            sec["tips"] += ["Check the Calories card: a flat trend with on-target logging usually "
                            "means portions or drinks are being under-counted.",
                            "Give any change two weeks before judging it — water weight hides fat loss."]
        else:
            sec["status"] = "bad"
            away = "up" if rate > 0 else "down"
            sec["headline"] = (f"Trending {away} {_fmt(abs(rate), 1)} {unit} a week — "
                               f"away from your {_fmt(goal, 1)} {unit} goal.")
            if direction == "lose":
                sec["tips"] += ["Log every meal for one full week, including drinks and snacks — "
                                "it almost always finds the gap.",
                                "Aim for about 500 kcal a day under maintenance; that's roughly "
                                f"1 {'lb' if unit == 'lb' else 'kg'}"
                                f"{'' if unit == 'lb' else ' every two weeks'} a week."]
            else:
                sec["tips"] += ["Add a calorie-dense snack (nuts, a shake, peanut butter) daily.",
                                "Make sure you're eating enough protein to build, not just maintain."]
    else:
        sec["status"] = "good" if abs(rate) < 0.5 else "watch"
        sec["headline"] = (f"{'Holding steady' if sec['status'] == 'good' else 'Drifting'}: "
                           f"{sign}{_fmt(abs(rate), 1)} {unit} a week.")
        if not goal:
            sec["tips"].append("Set a goal weight in Setup and Adam will show the pace and a goal date.")

    if big_gap >= 21:
        sec["findings"].append(f"There's a {big_gap}-day gap between weigh-ins, so the trend "
                               "is drawn across a stretch with no data.")
    if len(recent) < 4:
        sec["findings"].append(f"Only {_plural(len(recent), 'weigh-in')} in the last {WINDOW} days.")
        sec["tips"].append("More frequent weigh-ins make this trend much more trustworthy — "
                           "the morning reminder in Gear → Notifications helps.")
    return sec


# --- food -------------------------------------------------------------------

def _food(store, ref: str, settings: dict, weight: dict) -> dict:
    tg = hm.targets(store)
    window = _days_before(ref, WINDOW)
    last14 = set(window[-14:])
    fd = _food_days(store, window[0])
    k_target = tg.get("kcal")
    partial_line = (k_target or 2000) * PARTIAL_FRAC

    logged = [d for d in window if d in fd]
    full = [d for d in logged if fd[d]["kcal"] >= partial_line]
    partial = [d for d in logged if d not in full]
    # Judge on the most recent two weeks when there's enough of it, else 4 weeks.
    basis = [d for d in full if d in last14]
    basis_label = "last 14 days"
    if len(basis) < 3:
        basis, basis_label = full, f"last {WINDOW} days"

    def avg(key):
        return _mean([fd[d][key] for d in basis])

    a_k, a_p, a_c, a_f = avg("kcal"), avg("protein_g"), avg("carbs_g"), avg("fat_g")
    latest_w = weight["series"][-1]["v"] if weight.get("series") else None
    direction = weight.get("direction")

    today_row = _food_days(store, ref).get(ref)

    def series(key):
        out = []
        for d in _axis(ref):
            row = today_row if d == ref else fd.get(d)
            out.append({"date": d, "v": (_n(row[key], 0) if row else None),
                        "partial": bool(row and d != ref and row["kcal"] < partial_line),
                        "today": d == ref})
        return out

    consistency = len(logged) / WINDOW
    logging_tips = ["Log as you eat, not at the end of the day — a photo before the first bite takes "
                    "five seconds and Adam does the math.",
                    "A rough entry beats a blank day. “Chicken burrito” is enough for an estimate."]

    # ---- calories
    cal = _section("none", "Log a few full days of food and Adam will read your calories.",
                   series=series("kcal"), target=k_target, unit="kcal")
    cal["stats"] = [
        _stat("Daily avg", _fmt(a_k), (basis_label if len(basis) >= 3 else _plural(len(basis), "full day"))
              if a_k is not None else "no full days"),
        _stat("Days logged", f"{len(logged)}/{WINDOW}", _plural(len(partial), "partial day")),
        _stat("Target", _fmt(k_target) if k_target else "—", "kcal / day" if k_target else "set in Setup"),
    ]
    if partial:
        cal["findings"].append(f"{_plural(len(partial), 'logged day looks', 'logged days look')} partial "
                               f"(under {_fmt(partial_line)} kcal), so the averages skip "
                               f"{'it' if len(partial) == 1 else 'them'}.")
    if len(basis) >= 3 and k_target:
        ratio = a_k / k_target
        on = sum(1 for d in basis if abs(fd[d]["kcal"] / k_target - 1) <= 0.10)
        over = sum(1 for d in basis if fd[d]["kcal"] > k_target * 1.10)
        under = sum(1 for d in basis if fd[d]["kcal"] < k_target * 0.90)
        cal["stats"].insert(2, _stat("On target", f"{on}/{len(basis)}", f"{over} over · {under} under"))
        pct = round((ratio - 1) * 100)
        where = (f"{abs(pct)}% over" if pct > 0 else f"{abs(pct)}% under") if pct else "right on"
        if direction == "gain":
            st = "bad" if ratio < 0.90 else "watch" if ratio < 0.97 or ratio > 1.25 else "good"
        elif direction == "lose":
            st = "bad" if ratio > 1.10 else "watch" if ratio > 1.03 or ratio < 0.75 else "good"
        else:
            st = "good" if abs(ratio - 1) <= 0.10 else "watch" if abs(ratio - 1) <= 0.20 else "bad"
        cal["status"] = st
        cal["headline"] = f"Averaging {_fmt(a_k)} kcal on full days — {where} your target."
        if ratio > 1.03:
            cal["tips"] += ["Look at Top foods under Carbs · Fat · Meals — swapping the single "
                            "biggest item on over days is usually enough.",
                            "Drinks are the easiest cut: soda, juice, coffee drinks, and alcohol add up fast.",
                            "Eat protein first at each meal; it's the most filling per calorie."]
        elif ratio < 0.75:
            cal["tips"] += ["Eating this far under target is hard to sustain and tends to end in a rebound — "
                            "add a protein-forward snack.",
                            "If these days are real (not missed logs), raise your intake a little."]
        elif st == "good":
            cal["tips"].append("Steady — keep logging the same way so a drift shows up early.")
        # Energy-balance cross-check: intake says one thing, the scale another.
        rate = weight.get("rate_per_week")
        if direction == "lose" and rate is not None and rate > 0.2 and ratio <= 1.03:
            cal["findings"].append("Your weight is trending up while logged calories look on or under "
                                   "target. That usually means some meals, drinks, or portions aren't "
                                   "making it into the log.")
            if cal["status"] == "good":
                cal["status"] = "watch"
    elif len(basis) >= 3:
        cal["status"] = "info"
        cal["headline"] = f"Averaging {_fmt(a_k)} kcal on full days."
        cal["tips"].append("Set a calorie target in Setup and Adam will tell you how you're tracking.")
    if cal["status"] == "none" and logged:
        cal["headline"] = (f"Only {_plural(len(full), 'full day')} of food logged in the last {WINDOW} — "
                           "Adam needs at least 3 to read a trend.")
    if consistency < 0.5:
        cal["findings"].insert(0, f"Food logged on {len(logged)} of the last {WINDOW} days — trends "
                                  "need about 5 days a week to be trustworthy.")
        cal["tips"] = logging_tips + cal["tips"]
        if cal["status"] == "good":
            cal["status"] = "watch"
    cal["label"] = _STATUS_LABEL[cal["status"]]

    # ---- protein
    p_target = tg.get("protein_g")
    suggested = None
    if latest_w:
        suggested = latest_w * (0.7 if (weight.get("unit") or "lb") == "lb" else 1.6)
    prot = _section("none", "Log a few full days and Adam will read your protein.",
                    series=series("protein_g"), target=p_target or None, unit="g")
    goal_p = p_target or suggested
    prot["stats"] = [
        _stat("Daily avg", (_fmt(a_p) + " g") if a_p is not None else "—",
              (basis_label if len(basis) >= 3 else _plural(len(basis), "full day")) if a_p is not None else ""),
        _stat("Target", (_fmt(p_target) + " g") if p_target else ("~" + _fmt(suggested) + " g" if suggested else "—"),
              "per day" if p_target else ("suggested" if suggested else "set in Setup")),
    ]
    if latest_w and a_p is not None:
        prot["stats"].append(_stat("Per " + (weight.get("unit") or "lb"), _fmt(a_p / latest_w, 2) + " g",
                                   "of body weight"))
    if len(basis) >= 3 and goal_p:
        ratio = a_p / goal_p
        hit = sum(1 for d in basis if fd[d]["protein_g"] >= goal_p * 0.9)
        prot["stats"].insert(1, _stat("Days hit", f"{hit}/{len(basis)}", "≥ 90% of target"))
        prot["status"] = "good" if ratio >= 0.9 else "watch" if ratio >= 0.7 else "bad"
        gap = goal_p - a_p
        prot["headline"] = (f"Averaging {_fmt(a_p)} g — " +
                            ("you're hitting your protein." if ratio >= 0.9
                             else f"about {_fmt(gap)} g a day short."))
        if ratio < 0.9:
            prot["tips"] += ["Anchor every meal with a palm-sized protein — chicken, eggs, Greek yogurt, "
                             "tuna, lean beef, tofu. That's 30–40 g each.",
                             "Breakfast is where most people fall short; eggs or Greek yogurt fixes it.",
                             f"A protein shake closes a {_fmt(min(gap, 50))} g gap in one go."]
        else:
            prot["tips"].append("Spread it across the day — 3–4 servings of 30–40 g works better than one big dinner.")
        if not p_target:
            prot["findings"].append("No protein target is set, so Adam used ~0.7 g per lb of body weight. "
                                    "Set your own in Setup.")
    if prot["status"] == "none" and logged:
        prot["headline"] = "Needs at least 3 full days of food logged to read a trend."
        prot["tips"].append("When you log, name the protein (“6 oz chicken”) — it's the macro that's easiest to under-count.")
    if consistency < 0.5 and prot["status"] == "good":
        prot["status"] = "watch"
    prot["label"] = _STATUS_LABEL[prot["status"]]

    # top protein sources across the window
    agg: dict[str, dict] = {}
    for d in logged:
        for m in fd[d]["items"]:
            key = (m.get("name") or "Food").strip().lower()
            a = agg.setdefault(key, {"name": (m.get("name") or "Food").strip(), "count": 0,
                                     "kcal": 0.0, "protein_g": 0.0})
            a["count"] += 1
            a["kcal"] += float(m.get("kcal") or 0)
            a["protein_g"] += float(m.get("protein_g") or 0)
    top_p = sorted(agg.values(), key=lambda a: -a["protein_g"])[:5]
    prot["top"] = [{"name": a["name"], "count": a["count"], "v": _fmt(a["protein_g"]) + " g"}
                   for a in top_p if a["protein_g"] > 0]

    # ---- carbs · fat · meals
    c_t, f_t = tg.get("carbs_g"), tg.get("fat_g")
    mac = _section("none", "Log a few full days to see your macro balance.",
                   series=[{"date": d, "v": (fd[d]["meals"] if d in fd else
                                             (today_row["meals"] if d == ref and today_row else None)),
                            "today": d == ref} for d in _axis(ref)],
                   unit="meals")
    if len(basis) >= 3:
        pk, ck, fk = a_p * 4, a_c * 4, a_f * 9
        tot = (pk + ck + fk) or 1
        split = {"protein": round(pk / tot * 100), "carbs": round(ck / tot * 100),
                 "fat": round(fk / tot * 100)}
        mac["split"] = split
        avg_meals = _mean([fd[d]["meals"] for d in basis])
        mac["stats"] = [
            _stat("Carbs", f"{_fmt(a_c)} g", f"target {_fmt(c_t)} g" if c_t else "daily avg"),
            _stat("Fat", f"{_fmt(a_f)} g", f"target {_fmt(f_t)} g" if f_t else "daily avg"),
            _stat("Meals / day", _fmt(avg_meals, 1), basis_label),
        ]
        offs = []
        for name, val, tgt in (("carbs", a_c, c_t), ("fat", a_f, f_t)):
            if tgt:
                r = val / tgt
                if abs(r - 1) > 0.2:
                    offs.append((name, r))
        if c_t or f_t:
            mac["status"] = ("bad" if any(abs(r - 1) > 0.4 for _, r in offs)
                             else "watch" if offs else "good")
        else:
            mac["status"] = "info"
        if split["fat"] > 40:
            offs.append(("fat-share", None))
        mac["headline"] = (f"Calories come {split['protein']}% from protein, {split['carbs']}% carbs, "
                           f"{split['fat']}% fat.")
        for name, r in offs:
            if name == "fat" and r > 1 or name == "fat-share":
                mac["tips"].append("Fat adds up fastest: cooking oil (1 tbsp ≈ 120 kcal), butter, cheese, "
                                   "sauces, and fried food. Measure the oil once and you'll see it.")
            elif name == "carbs" and r > 1:
                mac["tips"].append("Most extra carbs hide in drinks, sweets, and refined snacks — swap one "
                                   "a day for fruit or a higher-fiber option.")
            elif name == "carbs" and r < 1:
                mac["tips"].append("Carbs well under target can leave workouts flat — rice, oats, potatoes, "
                                   "or fruit around training helps.")
            elif name == "fat" and r < 1:
                mac["tips"].append("Fat is low — some is needed for hormones and fullness: eggs, nuts, "
                                   "olive oil, avocado.")
        if mac["tips"] and split["fat"] > 40:
            mac["findings"].append(f"{split['fat']}% of calories from fat is on the high side.")
        if avg_meals is not None and avg_meals < 2.5:
            mac["findings"].append(f"About {_fmt(avg_meals, 1)} meals logged per day — snacks and "
                                   "drinks often go unlogged.")
        if mac["status"] == "good" and not mac["tips"]:
            mac["tips"].append("Balanced — nothing to fix here.")
    if mac["status"] == "none" and logged:
        mac["headline"] = "Needs at least 3 full days of food logged to show your macro balance."
    mac["tips"] = list(dict.fromkeys(mac["tips"]))
    top_k = sorted(agg.values(), key=lambda a: -a["kcal"])[:5]
    mac["top"] = [{"name": a["name"], "count": a["count"], "v": _fmt(a["kcal"]) + " kcal"}
                  for a in top_k if a["kcal"] > 0]
    mac["label"] = _STATUS_LABEL[mac["status"]]

    return {"kcal": cal, "protein": prot, "macros": mac}


# --- water ------------------------------------------------------------------

def _water(store, ref: str, settings: dict) -> dict:
    unit = settings.get("water_unit") or "oz"
    target_ml = settings.get("target_water_ml")
    window = _days_before(ref, WINDOW)
    by_day: dict[str, float] = {}
    for r in store.list_water(since=window[0]):
        by_day[r["date"]] = by_day.get(r["date"], 0.0) + float(r.get("ml") or 0)
    logged = [d for d in window if d in by_day]
    basis = [d for d in logged if d in set(window[-14:])]
    basis_label = "last 14 days"
    if len(basis) < 3:
        basis, basis_label = logged, f"last {WINDOW} days"
    avg_ml = _mean([by_day[d] for d in basis])
    series = [{"date": d, "v": hm.from_ml(by_day[d], unit) if d in by_day else None,
               "today": d == ref} for d in _axis(ref)]
    sec = _section("none", "Log water on a few days and Adam will show how you're doing.",
                   series=series, target=hm.from_ml(target_ml, unit) if target_ml else None, unit=unit)
    hit = sum(1 for d in logged if target_ml and by_day[d] >= target_ml * 0.95)
    streak = 0
    if target_ml:
        for d in reversed(window):
            if by_day.get(d, 0) >= target_ml * 0.95:
                streak += 1
            else:
                break
    ul = "cups" if unit == "cup" else unit
    sec["stats"] = [
        _stat("Daily avg", (_fmt(hm.from_ml(avg_ml, unit)) + " " + ul)
              if avg_ml is not None else "—", basis_label if avg_ml is not None else ""),
        _stat("Days logged", f"{len(logged)}/{WINDOW}", ""),
        _stat("Target hit", f"{hit}" if target_ml else "—",
              (f"streak {streak}" if streak else "days") if target_ml else "set in Setup"),
    ]
    if len(basis) >= 3 and target_ml:
        ratio = avg_ml / target_ml
        sec["status"] = "good" if ratio >= 0.9 else "watch" if ratio >= 0.6 else "bad"
        sec["headline"] = (f"Averaging {_fmt(hm.from_ml(avg_ml, unit))} {ul} on logged days — "
                           f"{round(ratio * 100)}% of your target.")
    elif len(basis) >= 3:
        sec["status"] = "info"
        sec["headline"] = f"Averaging {_fmt(hm.from_ml(avg_ml, unit))} {ul} on logged days."
    if len(logged) < WINDOW * 0.5:
        sec["findings"].append(f"Water logged on {len(logged)} of the last {WINDOW} days — on the "
                               "others there's no way to tell how much you drank.")
        if sec["status"] == "good":
            sec["status"] = "watch"
    if sec["status"] in ("watch", "bad", "none"):
        sec["tips"] += ["Drink a full glass with every meal — that's three glasses before you've tried.",
                        "Keep a bottle where you can see it; a one-tap glass on the Today card logs it.",
                        "Turn on the water-pace reminder in Gear → Notifications.",
                        "Hot days and workouts need more — add a glass per 30 minutes of sweat."]
    else:
        sec["tips"].append("Good hydration — keep the bottle habit going.")
    sec["label"] = _STATUS_LABEL[sec["status"]]
    return sec


# --- watch ------------------------------------------------------------------

def _watch(store, ref: str) -> dict:
    window = _days_before(ref, WINDOW)
    rows = {r["date"]: r for r in store.list_daily_metrics(since=window[0])}
    last7 = window[-7:]
    prior7 = window[-14:-7]

    def vals(days, key, ok=lambda v: v is not None):
        return [rows[d][key] for d in days if d in rows and ok(rows[d].get(key))]

    def ser(key, ok=lambda v: v is not None, scale=1.0, nd=0):
        out = []
        for d in _axis(ref):
            v = rows.get(d, {}).get(key)
            out.append({"date": d, "v": (_n(v / scale, nd) if ok(v) else None), "today": d == ref})
        return out

    metrics: dict[str, dict] = {}
    pos = lambda v: v is not None and v > 0  # noqa: E731 — 0 steps / 0 sleep = not worn

    def recent(key, ok=lambda v: v is not None):
        """The last 7 days when the watch reported on at least 3 of them; else
        the last 14 (a week with one synced day is not 'this week')."""
        if len(vals(last7, key, ok)) >= 3:
            return last7, "this week"
        if len(vals(window[-14:], key, ok)) >= 3:
            return window[-14:], "over the last 2 weeks"
        return [], ""

    # steps
    rd, rl = recent("steps", pos)
    s7 = _mean(vals(rd, "steps", pos))
    sp = _mean(vals(prior7, "steps", pos)) if rd is last7 else None
    st = _section("none", "Wear the watch for a few days to see your steps.",
                  series=ser("steps", pos), target=STEP_GOAL, unit="steps")
    if s7 is not None:
        days_goal = sum(1 for v in vals(rd, "steps", pos) if v >= STEP_GOAL)
        st["status"] = "good" if s7 >= STEP_GOAL else "watch" if s7 >= 5000 else "bad"
        st["headline"] = f"Averaging {_fmt(s7)} steps a day {rl}."
        st["stats"] = [_stat("Daily avg", _fmt(s7), _short(rl)),
                       _stat("Week before", _fmt(sp), (("▲ " if s7 >= sp else "▼ ") + _fmt(abs(s7 - sp)))
                             if sp is not None else ""),
                       _stat(f"{STEP_GOAL // 1000}k+ days", f"{days_goal}/{len(vals(rd, 'steps', pos))}", "days with data")]
        st["stats"] = [x for x in st["stats"] if x["v"] != "—"]
        if rd is not last7:
            st["findings"].append(f"The watch reported steps on only {len(vals(last7, 'steps', pos))} "
                                  "of the last 7 days, so this uses two weeks.")
        if sp and s7 < sp * 0.8:
            st["findings"].append(f"Down {round((1 - s7 / sp) * 100)}% from the week before.")
        if st["status"] != "good":
            st["tips"] += ["A 10-minute walk after each meal adds ~3,000 steps and helps blood sugar.",
                           "Take calls on your feet, park farther away, use the stairs.",
                           "Build up gradually — 1,000 more a day than this week is a fine next goal."]
        else:
            st["tips"].append("Solid movement — keep it up.")
    metrics["steps"] = st

    # sleep (stored in minutes)
    rd, rl = recent("sleep_min", pos)
    sl7 = _mean(vals(rd, "sleep_min", pos))
    slb = _mean(vals(window, "sleep_min", pos))
    sl = _section("none", "No sleep data lately — wear the watch to bed, or enter it in the Log tab.",
                  series=ser("sleep_min", pos, scale=60, nd=1), target=7, unit="h")
    if sl7 is not None:
        nights = vals(rd, "sleep_min", pos)
        short = sum(1 for v in nights if v < 420)
        sl["status"] = "good" if sl7 >= 420 else "watch" if sl7 >= 360 else "bad"
        sl["headline"] = f"Averaging {int(sl7 // 60)}h {int(round(sl7 % 60))}m a night {rl}."
        sl["stats"] = [_stat("Nightly avg", f"{int(sl7 // 60)}h {int(round(sl7 % 60))}m", _short(rl)),
                       _stat("Under 7h", f"{short}/{len(nights)}", "nights"),
                       _stat(f"{WINDOW}-day avg", f"{int(slb // 60)}h {int(round(slb % 60))}m" if slb else "—", "")]
        missing = len(rd) - len(nights)
        if missing >= 3:
            sl["findings"].append(f"{missing} of the last {len(rd)} nights have no sleep data — "
                                  "wear the watch to bed for a full picture.")
        if sl["status"] != "good":
            sl["tips"] += ["Keep the same wake time every day, weekends included — it anchors everything else.",
                           "Screens off and lights low 30–60 minutes before bed.",
                           "No caffeine after early afternoon; alcohol wrecks sleep quality even when you fall asleep fast.",
                           "Short sleep raises hunger the next day — it shows up in the Calories card."]
        else:
            sl["tips"].append("Good sleep — the foundation for everything else here.")
    metrics["sleep"] = sl

    # resting heart rate — judged against your own baseline, never a population number
    rd, rl = recent("resting_hr", pos)
    r7 = _mean(vals(rd, "resting_hr", pos))
    rb = _mean(vals([d for d in window if d not in rd], "resting_hr", pos))
    rh = _section("none", "Not enough resting heart rate data yet.",
                  series=ser("resting_hr", pos), target=None, unit="bpm")
    if r7 is not None:
        rh["stats"] = [_stat("Recent avg", f"{_fmt(r7)} bpm", _short(rl)),
                       _stat("Your baseline", f"{_fmt(rb)} bpm" if rb else "—", "the weeks before"),
                       _stat("Change", (("+" if r7 >= rb else "−") + _fmt(abs(r7 - rb)) + " bpm") if rb else "—", "")]
        if rb is None:
            rh["status"] = "info"
            rh["headline"] = f"Resting heart rate averaging {_fmt(r7)} bpm."
        elif r7 - rb >= 4:
            rh["status"] = "watch"
            rh["headline"] = f"Resting heart rate is up {_fmt(r7 - rb)} bpm from your baseline."
            rh["findings"].append("A rise like this usually tracks short sleep, stress, alcohol, hard training, "
                                  "or a bug coming on.")
            rh["tips"] += ["Prioritize sleep and an easy day or two; it usually settles within a week.",
                           "If it stays up and you feel unwell, rest — and check with a doctor if it persists."]
        else:
            rh["status"] = "good"
            rh["headline"] = (f"Resting heart rate is steady at {_fmt(r7)} bpm"
                              + (" — a little lower than usual." if r7 < rb - 2 else "."))
            rh["tips"].append("Steady or falling resting heart rate is a sign of good recovery and fitness.")
    metrics["resting_hr"] = rh

    # stress (Garmin 0-100: 0-25 rest, 26-50 low, 51-75 medium, 76+ high)
    rd, rl = recent("stress")
    x7 = _mean(vals(rd, "stress"))
    xs = _section("none", "No stress data this week.",
                  series=ser("stress"), target=None, unit="")
    if x7 is not None:
        band = "resting" if x7 <= 25 else "low" if x7 <= 50 else "medium" if x7 <= 75 else "high"
        xs["status"] = "good" if x7 <= 40 else "watch" if x7 <= 55 else "bad"
        xs["headline"] = f"Average stress {_fmt(x7)} {rl} — {band} on Garmin's scale."
        high = sum(1 for v in vals(rd, "stress") if v > 50)
        xs["stats"] = [_stat("Recent avg", _fmt(x7), band),
                       _stat("Days over 50", f"{high}/{len(vals(rd, 'stress'))}", _short(rl)),
                       _stat(f"{WINDOW}-day avg", _fmt(_mean(vals(window, 'stress'))), "")]
        if xs["status"] != "good":
            xs["tips"] += ["Two minutes of slow breathing (in 4, out 6) measurably drops it — the watch "
                           "has a guided version.",
                           "A short walk outside between tasks resets more than scrolling does.",
                           "High stress readings also come from illness, caffeine, and poor sleep — "
                           "check the Sleep tab."]
        else:
            xs["tips"].append("Stress is in a healthy range.")
    metrics["stress"] = xs

    # body battery — the stored value is the LAST reading at sync, which drains
    # through the day, so it's shown for reference and never graded.
    rd, rl = recent("body_battery")
    b7 = _mean(vals(rd, "body_battery"))
    bb = _section("none", "No Body Battery data this week.",
                  series=ser("body_battery"), target=None, unit="")
    if b7 is not None:
        bb["status"] = "info"
        bb["headline"] = f"Latest Body Battery readings average {_fmt(b7)}."
        bb["stats"] = [_stat("Recent avg", _fmt(b7), "last reading of the day"),
                       _stat(f"{WINDOW}-day avg", _fmt(_mean(vals(window, 'body_battery'))), "")]
        bb["findings"].append("This is the last reading each day, so it's naturally lower in the evening. "
                              "Use it as a trend, not a grade.")
        bb["tips"].append("Body Battery recharges mostly during sleep — low mornings point back to the Sleep tab.")
    metrics["body_battery"] = bb

    # active calories
    rd, rl = recent("active_kcal", pos)
    a7 = _mean(vals(rd, "active_kcal", pos))
    ac = _section("none", "No active calorie data this week.",
                  series=ser("active_kcal", pos), target=None, unit="kcal")
    if a7 is not None:
        ac["status"] = "info"
        ac["headline"] = f"Burning about {_fmt(a7)} active kcal a day {rl}."
        ac["stats"] = [_stat("Daily avg", f"{_fmt(a7)} kcal", _short(rl)),
                       _stat(f"{WINDOW}-day avg", _fmt(_mean(vals(window, 'active_kcal', pos))), "")]
        ac["tips"].append("Watch estimates of calories burned run high — don't eat them back one-for-one.")
    metrics["active_kcal"] = ac

    # Sleep has its own Today card, so it doesn't also grade the watch card.
    graded = [metrics[k]["status"] for k in ("steps", "resting_hr", "stress")]
    overall = _worst(*graded, metrics["body_battery"]["status"], metrics["active_kcal"]["status"])
    bad = [k for k in ("steps", "resting_hr", "stress") if metrics[k]["status"] in ("bad", "watch")]
    names = {"steps": "steps", "resting_hr": "resting heart rate", "stress": "stress"}
    if overall == "none":
        head = "Connect Garmin in Settings → Add-ons to see trends here."
    elif bad:
        head = "Worth a look: " + ", ".join(names[k] for k in bad) + "."
    else:
        head = "Your watch numbers look healthy."
    return {"status": overall, "label": _STATUS_LABEL[overall], "headline": head, "metrics": metrics}


# --- public -----------------------------------------------------------------

def insights(store=_default_store, date: str = "") -> dict:
    """Every Today card's expanded detail for the reference date."""
    settings = store.get_settings()
    weight = _weight(store, date, settings)
    food = _food(store, date, settings, weight)
    out = {
        "date": date,
        "window_days": WINDOW,
        "chart_days": CHART_DAYS,
        "kcal": food["kcal"],
        "protein": food["protein"],
        "macros": food["macros"],
        "water": _water(store, date, settings),
        "weight": weight,
        "watch": _watch(store, date),
    }
    out["sleep"] = out["watch"]["metrics"]["sleep"]   # the Sleep card (same object)
    # Statuses get revised as a section is built; derive every label once, here,
    # so a label can never disagree with its status.
    for k in ("kcal", "protein", "macros", "water", "weight", "sleep"):
        out[k]["label"] = _STATUS_LABEL[out[k]["status"]]
    for m in out["watch"]["metrics"].values():
        m["label"] = _STATUS_LABEL[m["status"]]
    return out
