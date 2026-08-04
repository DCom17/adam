"""
Adam — meal-photo estimator tests (Phase H2).

  * estimate_meal_photo via an INJECTED fake vision runner (no claude.exe, no
    real image): rows normalized to the meal-candidate shape;
  * corrections memory overrides a known food's macros;
  * JSON extractor tolerates fences/prose;
  * the /health/meal-photo endpoint (containment check + returns candidates,
    estimator monkeypatched) and the /health/correction learning loop.

Synthetic data. Run:  python test_health_import.py   (exit 0 = all passed)
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import config

if not config.ADAM_TOKEN:
    config.ADAM_TOKEN = "test-token-" + "i" * 48
if not config.CLAUDE_EXE:
    config.CLAUDE_EXE = sys.executable

import health_store as hs      # noqa: E402
import health_import as hi     # noqa: E402
import server                  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

AUTH = {"Authorization": "Bearer " + server.ADAM_TOKEN}
client = TestClient(server.app)

_passed = 0
_failed = 0


def check(name, cond):
    global _passed, _failed
    if cond: _passed += 1; print(f"  PASS  {name}")
    else: _failed += 1; print(f"  FAIL  {name}")


def main() -> int:
    sandbox = Path(tempfile.mkdtemp(prefix="adam_health_import_"))
    config.HEALTH_DB = sandbox / "health.db"
    config.UPLOAD_DIR = sandbox / "uploads"
    config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    hs.close(); hs.init(config.HEALTH_DB)

    print("\n[1] estimate_meal_photo via fake runner")
    def fake_runner(path):
        return '```json\n[{"name":"Chicken breast","qty":"6 oz","kcal":280,"protein_g":52,"carbs_g":0,"fat_g":6},' \
               '{"name":"White rice","portion":"1 cup","calories":205,"protein":4,"carbs":45,"fat":0.4}]\n```'
    cands = hi.estimate_meal_photo("whatever.jpg", runner=fake_runner)
    check("two candidates", len(cands) == 2)
    check("first normalized", cands[0]["name"] == "Chicken breast" and cands[0]["kcal"] == 280 and cands[0]["source"] == "photo")
    check("alt keys map (calories/protein)", cands[1]["kcal"] == 205 and cands[1]["protein_g"] == 4 and cands[1]["carbs_g"] == 45)

    print("\n[2] corrections override a known food")
    hs.upsert_correction("chicken breast", 300, 55, 1, 7)
    cands2 = hi.estimate_meal_photo("x.jpg", runner=fake_runner)
    c0 = cands2[0]
    check("correction applied", c0["kcal"] == 300 and c0["protein_g"] == 55 and c0.get("corrected") is True)
    check("uncorrected food unchanged", cands2[1]["kcal"] == 205)

    print("\n[3] JSON extractor")
    check("plain array", len(hi._extract_json_array('[{"name":"A","kcal":1}]')) == 1)
    check("prose+fence", len(hi._extract_json_array('Sure!\n```json\n[{"name":"B","kcal":2}]\n```')) == 1)
    check("garbage -> []", hi._extract_json_array("no json") == [])

    print("\n[4] /health/meal-photo endpoint (estimator monkeypatched)")
    img = config.UPLOAD_DIR / "meal.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0 fake jpeg bytes")
    _orig = hi.estimate_meal_photo
    hi.estimate_meal_photo = lambda p: [{"name": "Oatmeal", "qty": "1 bowl", "kcal": 300,
                                         "protein_g": 10, "carbs_g": 54, "fat_g": 6, "source": "photo"}]
    try:
        r = client.post("/health/meal-photo", headers=AUTH, json={"file_path": str(img)})
        check("photo estimate 200 + candidate", r.status_code == 200 and r.json()["candidates"][0]["name"] == "Oatmeal")
        bad = client.post("/health/meal-photo", headers=AUTH, json={"file_path": str(sandbox / "not-under-uploads.jpg")})
        check("path outside uploads -> 400", bad.status_code == 400)
    finally:
        hi.estimate_meal_photo = _orig

    print("\n[5] correction endpoint + logging a photo meal")
    r = client.post("/health/correction", headers=AUTH,
                    json={"name": "Oatmeal", "kcal": 320, "protein_g": 12, "carbs_g": 55, "fat_g": 6})
    check("correction saved", r.status_code == 200)
    check("correction round-trips in store", hs.match_correction("oatmeal")["kcal"] == 320)
    r = client.post("/health/meal", headers=AUTH,
                    json={"name": "Oatmeal", "date": "2030-01-15", "kcal": 320, "protein_g": 12,
                          "carbs_g": 55, "fat_g": 6, "source": "photo"})
    check("photo meal logged with source", r.status_code == 200 and r.json()["source"] == "photo")

    print("\n[6] Text estimate ('1 banana') via fake runner + endpoint")
    def fake_text(sysp, msg):
        return '```json\n{"name":"Banana","qty":"1 banana","kcal":105,"protein_g":1.3,"carbs_g":27,"fat_g":0.4}\n```'
    est = hi.estimate_meal_text("1 banana", runner=fake_text)
    check("text estimate returns a candidate", est is not None and est["name"] == "Banana")
    check("estimate carries scaled macros", est["kcal"] == 105 and est["carbs_g"] == 27)
    check("empty text -> None", hi.estimate_meal_text("", runner=fake_text) is None)
    hi_hs = __import__("health_store")
    hi_hs.upsert_correction("banana", 120, 2, 30, 1)
    est2 = hi.estimate_meal_text("1 banana", runner=fake_text)
    check("saved correction overrides the estimate", est2["kcal"] == 120 and est2.get("corrected") is True)
    _orig = hi.estimate_meal_text
    hi.estimate_meal_text = lambda t: {"name": "Banana", "qty": "1 banana", "kcal": 105, "protein_g": 1.3, "carbs_g": 27, "fat_g": 0.4, "source": "photo"}
    try:
        r = client.post("/health/meal-estimate", headers=AUTH, json={"text": "1 banana"})
        check("estimate endpoint 200 + candidate", r.status_code == 200 and r.json()["name"] == "Banana")
        check("empty estimate -> 400", client.post("/health/meal-estimate", headers=AUTH, json={"text": ""}).status_code == 400)
    finally:
        hi.estimate_meal_text = _orig
    check("JSON object extractor tolerates prose", hi._extract_json_object('sure: {"kcal":90} done')["kcal"] == 90)

    print("\n[7] Calories are reconciled against the macros (Python owns the arithmetic)")
    check("4/4/9 sum", hi.kcal_from_macros(10, 20, 5) == 10 * 4 + 20 * 4 + 5 * 9)
    # Macros imply 2846 kcal; a stated 2860 is inside tolerance and is left alone.
    close = hi.normalize_candidate({"name": "Big breakfast", "kcal": 2860,
                                    "protein_g": 152, "carbs_g": 96, "fat_g": 206})
    check("small drift kept as stated", close["kcal"] == 2860 and "kcal_adjusted" not in close)
    # Macros imply 400 kcal; a stated 1200 is impossible, so the macros win.
    off = hi.normalize_candidate({"name": "Bad math", "kcal": 1200,
                                  "protein_g": 25, "carbs_g": 25, "fat_g": 22.2})
    check("impossible kcal replaced by macro math", off["kcal"] == hi.kcal_from_macros(25, 25, 22.2))
    check("original claim preserved for transparency", off["kcal_adjusted"] == 1200)
    # A row with only calories and no macros must not be zeroed out.
    only = hi.normalize_candidate({"name": "Just kcal", "kcal": 300})
    check("kcal-only row survives", only["kcal"] == 300 and "kcal_adjusted" not in only)

    print("\n[8] Portion: told-vs-assumed")
    check("digits count as a stated quantity", hi._states_quantity("3 eggs") is True)
    check("units count", hi._states_quantity("chorizo, six oz") is True)
    check("number words count", hi._states_quantity("two tortillas") is True)
    check("bare description does not", hi._states_quantity("chorizo and egg breakfast") is False)
    def fake_vague(sysp, msg):
        return '{"name":"Chorizo and egg breakfast","qty":"2 eggs, 3 oz chorizo","kcal":720,"protein_g":32,"carbs_g":42,"fat_g":46}'
    vague = hi.estimate_meal_text("chorizo and egg breakfast", runner=fake_vague)
    check("assumed portion is flagged", vague["qty_assumed"] is True)
    check("text estimate is tagged text, not photo", vague["source"] == "text")
    exact = hi.estimate_meal_text("3 eggs and 2 oz chorizo", runner=fake_vague)
    check("stated portion is not flagged as assumed", exact["qty_assumed"] is False)

    hs.close()
    print(f"\n{'=' * 48}\n  {_passed} passed, {_failed} failed\n  sandbox: {sandbox}\n{'=' * 48}\n")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
