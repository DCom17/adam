"""
Adam — meal-photo macro estimator (Phase H2).

Snap a photo of a meal; Adam identifies the foods, estimates portions, and
returns kcal + macros for you to confirm or tweak before it's logged. The vision
step (the one non-deterministic part) sits behind an injectable `runner` — the
real one drives claude.exe with the Read tool over the uploaded image; tests
inject a fake that returns canned JSON, so the pipeline is exercised without a
model or a real photo.

Design (docs/PLAN-FINANCE-HEALTH.md §5, H2):
  - No bundled CV model. The vision LLM identifies + estimates; the numbers are
    then grounded/overridden by the user's own corrections memory
    (health_store.corrections) so a food you've fixed once comes back right.
  - Estimates are returned for REVIEW, never auto-logged. The caller (the router)
    hands them to the UI; logging happens only when the user confirms.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import config
import health_store as hs

# Shared by both estimators. Generic nutrition-database entries are built from the
# most calorie-dense form of an ingredient, so a model reaching for "chorizo" lands
# on cured Spanish salami-style chorizo (~455 kcal/100 g) when the dish obviously
# means fresh Mexican pork chorizo (~271 kcal/100 g, per the tube's own label) — a
# 1.7x error on that ingredient alone. Measured: a 10-egg / 9-oz-chorizo / 3-tortilla
# / cheese breakfast is ~2000 kcal, and the estimator was returning 2860 (+43%).
_INGREDIENT_FORM_RULE = (
    "INGREDIENT FORM — most error here comes from pricing the WRONG FORM of a food, "
    "not from bad arithmetic. Decide which form the dish implies, then use real "
    "supermarket-label / USDA values for THAT form. Generic composite database entries "
    "lean toward the most calorie-dense version of a food. Watch these traps:\n"
    "  - Fresh vs cured/dried. Fresh Mexican pork chorizo is ~270 kcal/100 g; cured "
    "Spanish chorizo is ~455. Same split for sausage, ham, jerky, and dried fruit. A "
    "chorizo-and-egg breakfast means the fresh kind.\n"
    "  - Cooked vs dry weight. Rice, pasta, oats and beans roughly TRIPLE in weight "
    "when cooked: '1 cup of rice' almost always means cooked (~205 kcal), not dry "
    "(~675). Meat runs the other way, losing about 25% of its weight cooking.\n"
    "  - Rendered fat. Ground beef, bacon and chorizo are usually drained — price the "
    "as-eaten food, not the fat that left the pan.\n"
    "  - Cut and fat level. 70/30 ground beef is nearly double 93/7. Whole vs skim "
    "milk, skin-on vs skinless, dark vs white meat, oil-packed vs water-packed.\n"
    "  - Bone-in weights (wings, ribs, bone-in chicken) include bone nobody eats.\n"
    "Do NOT simply shave everything downward — that is its own error. Count what is "
    "genuinely there: the oil or butter it was cooked in, dressings, sauces, cheese, "
    "and the fact that restaurant and takeout servings run well above home portions. "
    "Fat is the macro these estimates miss most often, in BOTH directions; check it "
    "against the foods actually present before answering.\n"
    "WEIGHT CHECK — whenever you name a gram or ounce weight in \"qty\", make the macros "
    "match THAT weight at the food's standard per-100 g composition. Multiply it out; "
    "do not recall a vague serving figure. Cooked white rice is ~130 kcal/100 g, so "
    "158 g (1 cup) is ~205 kcal and ~45 g carbs — not 150 kcal. A stated weight and the "
    "macros beside it must agree."
)

_VISION_SYSTEM_PROMPT = (
    "You estimate the nutrition of a meal from a photo. Use the Read tool to view "
    "the image file whose path is given, then output ONLY a JSON array — no prose, "
    "no markdown fences. One object per distinct food/item you can see:\n"
    '  "name"      (string, the food)\n'
    '  "qty"       (string, your portion estimate, e.g. "1 cup", "6 oz")\n'
    '  "kcal"      (number, calories)\n'
    '  "protein_g" (number)  "carbs_g" (number)  "fat_g" (number)\n'
    "Estimate realistically from typical portions; if unsure, give your best single "
    "estimate rather than a range. Do not include drinks unless they have calories.\n"
    + _INGREDIENT_FORM_RULE
)


def _extract_json_array(text: str) -> list[dict]:
    """Pull a JSON array of objects from a model reply that may be fenced or prose-
    wrapped. Returns [] if nothing parseable is found."""
    if not text:
        return []
    t = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if fence:
        t = fence.group(1).strip()
    if not t.startswith("["):
        a, b = t.find("["), t.rfind("]")
        if a == -1 or b == -1 or b < a:
            return []
        t = t[a:b + 1]
    try:
        data = json.loads(t)
    except ValueError:
        return []
    return [d for d in data if isinstance(d, dict)] if isinstance(data, list) else []


def _num(v) -> float:
    try:
        return round(float(v), 1)
    except (TypeError, ValueError):
        return 0.0


# Atwater factors: the calories in a gram of each macronutrient. Fixed constants,
# so calories-vs-macros is arithmetic, not another thing the model can get wrong.
_KCAL_PER_G = {"protein_g": 4.0, "carbs_g": 4.0, "fat_g": 9.0}

# How far the model's stated calorie number may drift from its own macros before we
# stop believing it. Small slips are rounding; a big gap means one of the two numbers
# is wrong, and the macros (three independent figures) are the better bet.
_KCAL_TOLERANCE = 0.10
_KCAL_TOLERANCE_FLOOR = 20.0


def kcal_from_macros(protein_g: float, carbs_g: float, fat_g: float) -> float:
    """Calories implied by the macros (4/4/9). Plain arithmetic — the model never
    does this sum for us."""
    return round(protein_g * _KCAL_PER_G["protein_g"]
                 + carbs_g * _KCAL_PER_G["carbs_g"]
                 + fat_g * _KCAL_PER_G["fat_g"], 1)


def normalize_candidate(raw: dict, source: str = "photo") -> dict:
    """One loosely-shaped model row -> the meal-candidate shape the UI logs.

    Also reconciles calories against the macros: if the stated kcal disagrees with
    what the macros add up to by more than the tolerance, the macro-derived figure
    wins and `kcal_adjusted` records what was claimed. Same principle the finance
    engine runs on — the model supplies estimates, Python owns the arithmetic."""
    def pick(*keys):
        for k in keys:
            if raw.get(k) not in (None, ""):
                return raw[k]
        return ""
    cand = {
        "name": str(pick("name", "food", "item")).strip() or "Food",
        "qty": str(pick("qty", "portion", "serving")).strip(),
        "kcal": _num(pick("kcal", "calories")),
        "protein_g": _num(pick("protein_g", "protein")),
        "carbs_g": _num(pick("carbs_g", "carbs", "carbohydrates")),
        "fat_g": _num(pick("fat_g", "fat")),
        "source": source,
    }
    derived = kcal_from_macros(cand["protein_g"], cand["carbs_g"], cand["fat_g"])
    # Only reconcile when there ARE macros to reconcile against — a row carrying just
    # a calorie count would otherwise be zeroed out.
    if derived > 0:
        allowed = max(derived * _KCAL_TOLERANCE, _KCAL_TOLERANCE_FLOOR)
        if abs(cand["kcal"] - derived) > allowed:
            cand["kcal_adjusted"] = cand["kcal"]
            cand["kcal"] = derived
    return cand


def apply_corrections(candidate: dict, store=hs) -> dict:
    """If the user has previously corrected this food's macros, use those numbers
    (the learning loop). Matched by lowercased name."""
    hit = store.match_correction(candidate["name"])
    if hit:
        candidate = dict(candidate)
        candidate.update({"kcal": hit["kcal"], "protein_g": hit["protein_g"],
                          "carbs_g": hit["carbs_g"], "fat_g": hit["fat_g"],
                          "corrected": True})
    return candidate


def _default_runner(image_path: str, timeout: int = config.CLAUDE_TIMEOUT_SECONDS) -> str:
    """Drive claude.exe for a single vision turn that Reads the image and returns
    the JSON estimate. Read is allowed (it needs to view the file) and the image's
    folder is granted via --add-dir; write/shell tools stay denied. Mirrors
    run_claude's spawn/auth conventions. Not exercised in tests (fake runner)."""
    exe = config.CLAUDE_EXE
    if not exe:
        raise RuntimeError("Claude executable not configured (config.CLAUDE_EXE).")
    folder = str(Path(image_path).resolve().parent)
    cmd = [exe, "-p", "--output-format", "json"]
    if config.VOICE_MODEL and config.VOICE_MODEL.lower() != "default":
        cmd += ["--model", config.VOICE_MODEL]
    cmd += ["--add-dir", folder]
    cmd += ["--disallowedTools", "Write", "Edit", "NotebookEdit", "Bash",
            "WebFetch", "WebSearch"]   # Read stays enabled to view the image
    cmd += ["--append-system-prompt", _VISION_SYSTEM_PROMPT]
    cmd += [f"Estimate the nutrition of the meal in this image: {image_path}"]

    child_env = dict(os.environ)
    child_env.pop("ANTHROPIC_API_KEY", None)
    if config.AUTH_MODE == "api_key" and config.ANTHROPIC_API_KEY:
        child_env["ANTHROPIC_API_KEY"] = config.ANTHROPIC_API_KEY

    proc = subprocess.run(cmd, capture_output=True, timeout=timeout, env=child_env)
    out = proc.stdout.decode("utf-8", errors="replace") if proc.stdout else ""
    try:
        payload = json.loads(out)
        return payload.get("result", "") if isinstance(payload, dict) else out
    except ValueError:
        return out


def estimate_meal_photo(image_path: str, *, runner=_default_runner, store=hs) -> list[dict]:
    """Estimate meal-candidate rows from a photo via the injected runner, then
    apply the user's macro corrections. Returns [] if nothing was recognized."""
    text = runner(image_path)
    rows = _extract_json_array(text)
    return [apply_corrections(normalize_candidate(r), store) for r in rows]


# --- Text estimate ("1 banana" -> macros) -----------------------------------

# Words that carry a quantity on their own, so "a dozen wings" counts as stated.
_QUANTITY_WORDS = (
    "half", "quarter", "dozen", "single", "double", "one", "two", "three", "four",
    "five", "six", "seven", "eight", "nine", "ten", "twelve",
)


def _states_quantity(text: str) -> bool:
    """True when the user's description already pins down HOW MUCH — a digit, a
    number word, or a unit. Used only to decide whether the returned portion is the
    user's or Adam's assumption; it never changes the estimate itself."""
    t = (text or "").lower()
    if re.search(r"\d", t):
        return True
    if re.search(r"\b(oz|ounce|ounces|lb|lbs|pound|pounds|g|gram|grams|kg|cup|cups|"
                 r"tbsp|tsp|slice|slices|serving|servings|plate|bowl|scoop|scoops)\b", t):
        return True
    return any(re.search(r"\b" + w + r"\b", t) for w in _QUANTITY_WORDS)

_TEXT_SYSTEM_PROMPT = (
    "You estimate the nutrition of a food or meal the user describes in plain text. "
    "Output ONLY a JSON object — no prose, no markdown fences — with these keys:\n"
    '  "name"      (string, the cleaned food name)\n'
    '  "qty"       (string, the portion, e.g. "1 banana", "2 eggs", "1 cup")\n'
    '  "kcal"      (number)  "protein_g" (number)  "carbs_g" (number)  "fat_g" (number)\n'
    "Scale the numbers to whatever quantity the user gave. Use realistic typical values; "
    "give your single best estimate, never a range. If the text names several foods, sum "
    "them into one estimate for the whole meal.\n"
    "PORTION RULE — this matters more than the macros. When the text does NOT state a "
    "quantity (e.g. 'chorizo and egg breakfast with tortillas'), assume ONE STANDARD "
    "SERVING FOR ONE ADULT. Do not invent a large, family-sized, or all-you-can-eat "
    "portion: a described breakfast is 2-3 eggs, not 10; a meat portion is 3-4 oz, not 9; "
    "2 tortillas, not 6. Only go above a single serving when the user explicitly says so "
    "('10 eggs', 'family size', 'double portion').\n"
    'Always spell the portion you assumed out in "qty" (e.g. "2 eggs, 3 oz chorizo, '
    '2 flour tortillas, 1 oz cheese") so the user can see exactly what was priced and '
    "correct it. Never leave qty vague.\n"
    + _INGREDIENT_FORM_RULE
)


def _extract_json_object(text: str) -> dict:
    """Pull a single JSON object out of a model reply that may be fenced/prose-wrapped."""
    if not text:
        return {}
    t = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if fence:
        t = fence.group(1).strip()
    if not t.startswith("{"):
        a, b = t.find("{"), t.rfind("}")
        if a == -1 or b == -1 or b < a:
            return {}
        t = t[a:b + 1]
    try:
        d = json.loads(t)
    except ValueError:
        return {}
    return d if isinstance(d, dict) else {}


def _default_text_runner(system_prompt: str, message: str,
                         timeout: int = config.CLAUDE_TIMEOUT_SECONDS) -> str:
    """A single tool-less claude.exe turn that returns raw text — used to estimate
    macros from a text description. Mirrors run_claude's spawn/auth conventions with
    no brain/system prompt and no tools. Not exercised in tests (fake runner)."""
    exe = config.CLAUDE_EXE
    if not exe:
        raise RuntimeError("Claude executable not configured (config.CLAUDE_EXE).")
    cmd = [exe, "-p", "--output-format", "json"]
    if config.VOICE_MODEL and config.VOICE_MODEL.lower() != "default":
        cmd += ["--model", config.VOICE_MODEL]
    cmd += ["--disallowedTools", "Write", "Edit", "NotebookEdit", "Bash",
            "Read", "WebFetch", "WebSearch"]
    cmd += ["--append-system-prompt", system_prompt, message]
    child_env = dict(os.environ)
    child_env.pop("ANTHROPIC_API_KEY", None)
    if config.AUTH_MODE == "api_key" and config.ANTHROPIC_API_KEY:
        child_env["ANTHROPIC_API_KEY"] = config.ANTHROPIC_API_KEY
    proc = subprocess.run(cmd, capture_output=True, timeout=timeout, env=child_env)
    out = proc.stdout.decode("utf-8", errors="replace") if proc.stdout else ""
    try:
        payload = json.loads(out)
        return payload.get("result", "") if isinstance(payload, dict) else out
    except ValueError:
        return out


def estimate_meal_text(text: str, *, runner=_default_text_runner, store=hs) -> dict | None:
    """Estimate one meal's macros from a plain-text description ("1 banana") via the
    injected runner, then apply the user's saved corrections. Returns the candidate
    dict, or None if nothing usable came back."""
    if not (text or "").strip():
        return None
    obj = _extract_json_object(runner(_TEXT_SYSTEM_PROMPT, "Food: " + text.strip()))
    if not obj:
        return None
    cand = normalize_candidate(obj, source="text")
    if not cand.get("qty"):
        cand["qty"] = text.strip()
    # Flag when the user named no quantity, so the UI can say the portion is an
    # ASSUMPTION rather than something they told us. An unnoticed wrong assumption is
    # how one description silently becomes a 4x-different calorie count.
    cand["qty_assumed"] = not _states_quantity(text)
    return apply_corrections(cand, store)
