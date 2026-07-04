/****************************************************
 * HUNTER LEVEL FIX — run once on your Hunter Dashboard Sheet.
 *
 * WHY: Your stats show Level 1 because the Level_Curve tab the level
 * formulas depend on is empty, AND the shipped curve differs from your
 * real system. This installs YOUR canonical curve  xp_to_next(L)=50*(L+1)
 * (cumulative L2=100, L3=250, L4=450, L5=700, L6=1000, ...) and points the
 * Stats + State level cells at it.  Your XP is never touched — only the
 * level lookups are repaired, so 886 XP reads as Level 5 again.
 *
 * HOW TO RUN:
 *   1. Open your Hunter Dashboard Sheet -> Extensions -> Apps Script.
 *   2. Add a new script file, paste this whole file in, Save.
 *   3. Select  fixHunterLevels  in the function dropdown, press Run.
 *   4. Approve the one-time permission prompt.
 *   5. Refresh the Sheet. No redeploy needed.
 ****************************************************/

function fixHunterLevels() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var log = [];
  var N = 40; // levels of headroom

  // ── 1. Rebuild Level_Curve with YOUR curve: xp_to_next(L) = 50*(L+1) ──────
  var rows = [], cum = 0;
  for (var L = 1; L <= N; L++) {
    var nxt = 50 * (L + 1);
    rows.push([L, nxt, cum]);   // [level, xp_to_next, cumulative_to_reach_level]
    cum += nxt;
  }
  var lc = ss.getSheetByName("Level_Curve");
  if (!lc) lc = ss.insertSheet("Level_Curve");
  lc.clearContents();
  lc.getRange(1, 1, 1, 3).setValues([["level", "xp_to_next", "cumulative_xp_required"]]);
  lc.getRange(2, 1, rows.length, 3).setValues(rows);
  log.push("Level_Curve rebuilt: levels 1-" + N + "  (cumulative L5=700).");

  function findCol(h, names) {
    for (var i = 0; i < names.length; i++) { var x = h.indexOf(names[i]); if (x >= 0) return x; }
    return -1;
  }
  function colLetter(c) {
    var s = ""; while (c > 0) { var r = (c - 1) % 26; s = String.fromCharCode(65 + r) + s; c = Math.floor((c - 1) / 26); } return s;
  }
  var lastCurveRow = N + 1;

  // ── 2. Stats level = LOOKUP(xp_total -> cumulative -> level) ──────────────
  var st = ss.getSheetByName("Stats");
  if (st && st.getLastRow() >= 2) {
    var sCols = st.getLastColumn();
    var sh = st.getRange(1, 1, 1, sCols).getValues()[0].map(function (h) { return String(h).trim().toLowerCase(); });
    var xpCol = findCol(sh, ["xp_total", "total_xp", "xp"]);
    var lvlCol = findCol(sh, ["level", "stat_level", "lv"]);
    if (xpCol >= 0 && lvlCol >= 0) {
      var xl = colLetter(xpCol + 1);
      for (var r = 2; r <= st.getLastRow(); r++) {
        st.getRange(r, lvlCol + 1).setFormula(
          '=IF(' + xl + r + '="","",LOOKUP(' + xl + r +
          ',Level_Curve!$C$2:$C$' + lastCurveRow + ',Level_Curve!$A$2:$A$' + lastCurveRow + '))'
        );
      }
      log.push("Stats level column re-pointed at the curve.");
    } else { log.push("Stats: xp_total/level column not found — skipped."); }
  } else { log.push("Stats tab missing/empty — skipped."); }

  // ── 3. State character_level = LOOKUP(total_xp -> level); rank reads off it ─
  var st2 = ss.getSheetByName("State");
  if (st2) {
    var stCols = st2.getLastColumn();
    var sth = st2.getRange(1, 1, 1, stCols).getValues()[0].map(function (h) { return String(h).trim().toLowerCase(); });
    var clCol = findCol(sth, ["character_level", "char_level", "level"]);
    var txCol = findCol(sth, ["total_xp", "xp_total", "total"]);
    if (clCol >= 0 && txCol >= 0) {
      var txl = colLetter(txCol + 1);
      st2.getRange(2, clCol + 1).setFormula(
        '=IF(' + txl + '2="","",LOOKUP(' + txl + '2' +
        ',Level_Curve!$C$2:$C$' + lastCurveRow + ',Level_Curve!$A$2:$A$' + lastCurveRow + '))'
      );
      log.push("State character_level re-pointed at total_xp (886 -> Level 5).");
    } else { log.push("State: character_level/total_xp column not found — skipped."); }
  } else { log.push("State tab missing — skipped."); }

  SpreadsheetApp.flush();
  Logger.log(log.join("\n"));
  try {
    SpreadsheetApp.getUi().alert("Hunter Level Fix complete:\n\n" + log.join("\n") + "\n\nRefresh the Sheet to verify.");
  } catch (e) { /* no UI in some run contexts — log only */ }
}
