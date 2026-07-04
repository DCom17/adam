/****************************************************
 * HUNTER DASHBOARD — BOUND SCRIPT TEMPLATE
 * Ships with Jarvis Voice Local. De-personalized.
 *
 * This is the complete script for YOUR OWN copy of the Hunter dashboard Sheet.
 * It does three things:
 *   1. setupHunterDashboard()  — builds every tab, header, formula, and starter
 *      row from a BLANK spreadsheet. Run this once.
 *   2. doPost(e)               — the sync bridge. Jarvis POSTs a sheet_sync
 *      payload (with your token) and it updates the data tabs. WRITE side.
 *   3. doGet(e)?action=verify  — read-only. Returns your current State / Stats /
 *      Daily_Quests so Jarvis can confirm a sync landed. No write, no delete.
 *
 * SECURITY / TRUST:
 *   - The script runs in YOUR Google account. Jarvis never holds a Google
 *     credential — only a random token you generate here.
 *   - Run setHunterToken() ONCE. It generates a token, stores it in this script's
 *     Script Properties, and logs it. Copy that value into your .env as
 *     HUNTER_TOKEN. The token is NEVER hardcoded in this file.
 *   - There is deliberately NO delete capability. The sync clears+rewrites
 *     Daily_Quests (the intended daily refresh) but never deletes rows or tabs.
 *
 * SETUP ORDER:
 *   1) Paste this whole file into Apps Script (Extensions -> Apps Script).
 *   2) Run setupHunterDashboard()  (approve the permission prompt).
 *   3) Run setHunterToken()        (copy the logged HUNTER_TOKEN into .env).
 *   4) Deploy -> New deployment -> Web app -> Execute as: Me, Access: Anyone.
 *   5) Copy the /exec URL into the Jarvis Hunter setup wizard and Test.
 ****************************************************/

const HUNTER_TOKEN_PROP = 'HUNTER_TOKEN';

/**
 * Generate the sync/verify token, store it in Script Properties, and log it.
 * Run this ONCE, then copy the logged value into your .env as HUNTER_TOKEN.
 */
function setHunterToken() {
  const token =
    Utilities.getUuid().replace(/-/g, '') +
    Utilities.getUuid().replace(/-/g, '');

  PropertiesService.getScriptProperties().setProperty(HUNTER_TOKEN_PROP, token);

  Logger.log('HUNTER_TOKEN=' + token);
  return token;
}

function getHunterToken_() {
  return PropertiesService.getScriptProperties().getProperty(HUNTER_TOKEN_PROP);
}


/* ═══════════════════════════════════════════════════════════════════════════
 * 1. SETUP — build the whole dashboard from a blank spreadsheet
 * ═══════════════════════════════════════════════════════════════════════════ */

function setupHunterDashboard() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();

  const sheets = {
    Dashboard: [],
    State: [
      "date", "character_level", "rank", "total_xp", "xp_to_next",
      "fatigue", "momentum", "next_best_action", "active_boss",
      "main_quest", "last_updated"
    ],
    Stats: [
      "stat", "xp_total", "level", "xp_into_level", "xp_to_next",
      "progress_pct", "progress_bar", "trend_7d", "weakness_flag",
      "evidence_gate", "last_updated"
    ],
    Daily_Quests: [
      "date", "quest_id", "quest", "type", "linked_stat", "linked_boss",
      "xp_value", "status", "evidence", "time_block", "notes"
    ],
    Weekly_Quests: [
      "week_start", "quest_id", "quest", "linked_stat", "linked_boss",
      "xp_value", "progress", "status", "notes"
    ],
    Main_Quests: [
      "quest_id", "quest", "area", "linked_boss", "progress_pct",
      "status", "next_move", "notes"
    ],
    Bosses: [
      "boss_id", "name", "area", "difficulty", "difficulty_label", "status",
      "progress_pct", "current_phase", "next_move", "main_blocker",
      "reward_xp", "last_reviewed"
    ],
    Boss_Milestones: [
      "boss_id", "milestone_id", "milestone", "due_window", "xp_reward",
      "status", "evidence", "notes"
    ],
    Weaknesses: [
      "weakness_id", "pattern", "stat", "severity", "evidence_count_30d",
      "countermeasure", "status", "last_seen", "notes"
    ],
    XP_Log: [
      "timestamp", "source_type", "stat", "xp", "category", "evidence_ref",
      "approved", "notes"
    ],
    Rank_Rules: [
      "rank", "min_character_level", "min_average_stat", "min_lowest_stat",
      "boss_requirement", "consistency_requirement", "evidence_requirement"
    ],
    Config: ["key", "value", "notes"],
    Level_Curve: ["level", "xp_to_next", "cumulative_xp_required"],
    System_Log: ["timestamp", "event_type", "source", "message", "notes"]
  };

  Object.keys(sheets).forEach(name => {
    let sh = ss.getSheetByName(name);
    if (!sh) sh = ss.insertSheet(name);
    sh.clear();

    if (sheets[name].length > 0) {
      sh.getRange(1, 1, 1, sheets[name].length).setValues([sheets[name]]);
      sh.setFrozenRows(1);
      styleHeader_(sh, sheets[name].length);
    }

    sh.setTabColor("#1a1f3c");
    sh.setHiddenGridlines(true);
  });

  setupLevelCurve_(ss);
  setupInitialStats_(ss);
  setupInitialBosses_(ss);
  setupRankRules_(ss);
  setupState_(ss);
  setupDashboard_(ss);
  setupDropdowns_(ss);
  setupTheme_(ss);

  SpreadsheetApp.flush();
}

function styleHeader_(sheet, columns) {
  sheet.getRange(1, 1, 1, columns)
    .setFontWeight("bold")
    .setFontColor("#dff6ff")
    .setBackground("#111827")
    .setBorder(true, true, true, true, true, true, "#38bdf8", SpreadsheetApp.BorderStyle.SOLID);
}

function setupTheme_(ss) {
  ss.getSheets().forEach(sh => {
    const maxCols = Math.max(sh.getMaxColumns(), 12);
    const maxRows = Math.max(sh.getMaxRows(), 50);
    sh.getRange(1, 1, maxRows, maxCols)
      .setBackground("#0b1020")
      .setFontColor("#e5e7eb")
      .setFontFamily("Arial");
  });
}

function setupLevelCurve_(ss) {
  const sh = ss.getSheetByName("Level_Curve");
  const rows = [];
  let cumulative = 0;

  for (let level = 1; level <= 100; level++) {
    const xpToNext = Math.round(20 + 4 * level + 0.5 * Math.pow(level, 2));
    rows.push([level, xpToNext, cumulative]);
    cumulative += xpToNext;
  }

  sh.getRange(2, 1, rows.length, 3).setValues(rows);
}

function setupInitialStats_(ss) {
  const sh = ss.getSheetByName("Stats");

  const stats = [
    ["Discipline", 0], ["Knowledge", 0], ["Health", 0], ["Finance", 0],
    ["Career", 0], ["Spiritual", 0], ["Social", 0], ["Execution", 0]
  ];

  sh.getRange(2, 1, stats.length, 2).setValues(stats);

  for (let r = 2; r <= 9; r++) {
    sh.getRange(r, 3).setFormula(`=IF(B${r}="","",LOOKUP(B${r},Level_Curve!$C$2:$C$101,Level_Curve!$A$2:$A$101))`);
    sh.getRange(r, 4).setFormula(`=IF(B${r}="","",B${r}-LOOKUP(B${r},Level_Curve!$C$2:$C$101,Level_Curve!$C$2:$C$101))`);
    sh.getRange(r, 5).setFormula(`=IF(C${r}="","",INDEX(Level_Curve!$B$2:$B$101,MATCH(C${r},Level_Curve!$A$2:$A$101,0)))`);
    sh.getRange(r, 6).setFormula(`=IFERROR(D${r}/E${r},0)`);
    sh.getRange(r, 7).setFormula(`=SPARKLINE(F${r},{"charttype","bar";"max",1;"color1","#38bdf8"})`);
    sh.getRange(r, 8).setValue("Stable");
    sh.getRange(r, 9).setValue(false);
    sh.getRange(r, 10).setValue("Open");
  }
}

function setupInitialBosses_(ss) {
  const sh = ss.getSheetByName("Bosses");

  // Generic starter bosses. Edit/replace these with your own real goals.
  const bosses = [
    ["BOSS-001", "Finish Your Degree / Big Credential", "Education", 5, "S", "Active", 0, "Foundation", "Complete the next graded unit", "Consistency", 3750, ""],
    ["BOSS-002", "Build Your Career Path", "Career", 4, "A", "Active", 0, "Foundation", "Define your target role", "Clarity", 2400, ""],
    ["BOSS-003", "Stabilize Your Finances", "Finance", 4, "A", "Active", 0, "Foundation", "Keep a daily money log", "Consistency", 2400, ""],
    ["BOSS-004", "Ship a Personal Project", "Career", 3, "B", "Active", 0, "Foundation", "Create the first real artifact", "Execution", 1350, ""],
    ["BOSS-005", "Build a Daily Discipline System", "Discipline", 4, "A", "Active", 0, "Foundation", "Use daily planning for one week", "Consistency", 2400, ""],
    ["BOSS-006", "Improve Health & Fitness", "Health", 3, "B", "Active", 0, "Foundation", "Complete the first tracked workout", "Routine", 1350, ""]
  ];

  sh.getRange(2, 1, bosses.length, bosses[0].length).setValues(bosses);
}

function setupRankRules_(ss) {
  const sh = ss.getSheetByName("Rank_Rules");

  const rows = [
    ["E Rank", 0, 0, 0, "Starting rank", "None", "None"],
    ["D Rank", 15, 12, 5, "Two weeks usable data", "Basic consistency", "Review confirmed"],
    ["C Rank", 30, 24, 12, "Two major milestones or one mini-boss", "Visible weekly consistency", "Review confirmed"],
    ["B Rank", 45, 38, 22, "Two boss clears or equivalent", "60-day consistency >70%", "Review confirmed"],
    ["A Rank", 60, 52, 35, "Three boss clears", "90-day consistency >75%", "Real-world artifacts"],
    ["S Rank", 75, 68, 50, "Four boss clears", "180-day consistency >80%", "Multiple verified outcomes"],
    ["Master Rank", 90, 85, 75, "Long-horizon proof", "Sustained excellence", "Manual mastery gate"]
  ];

  sh.getRange(2, 1, rows.length, rows[0].length).setValues(rows);
}

function setupState_(ss) {
  const sh = ss.getSheetByName("State");

  sh.getRange(2, 1).setFormula("=TODAY()");
  sh.getRange(2, 2).setFormula(`=FLOOR(MIN(0.6*AVERAGE(Stats!C2:C9)+0.4*AVERAGE(SMALL(Stats!C2:C9,{1,2,3,4}))+MIN(10,SUMIF(Bosses!F:F,"Cleared",Bosses!D:D)*0.8),AVERAGE(Stats!C2:C9)+12,MIN(Stats!C2:C9)+20))`);
  sh.getRange(2, 3).setFormula(`=IFS(B2>=90,"Master Rank",B2>=75,"S Rank",B2>=60,"A Rank",B2>=45,"B Rank",B2>=30,"C Rank",B2>=15,"D Rank",TRUE,"E Rank")`);
  sh.getRange(2, 4).setFormula("=SUM(Stats!B2:B9)");
  sh.getRange(2, 5).setValue("");
  sh.getRange(2, 6).setValue(25);
  sh.getRange(2, 7).setValue("Stable");
  sh.getRange(2, 8).setValue("Run daily planning.");
  sh.getRange(2, 9).setValue("Build a Daily Discipline System");
  sh.getRange(2, 10).setValue("Use the system for one real week");
  sh.getRange(2, 11).setFormula("=NOW()");
}

function setupDashboard_(ss) {
  const sh = ss.getSheetByName("Dashboard");

  sh.clear();
  sh.setHiddenGridlines(true);

  sh.getRange("A1:H1").merge();
  sh.getRange("A1").setValue("HUNTER PROGRESSION DASHBOARD")
    .setFontSize(20).setFontWeight("bold").setFontColor("#dff6ff")
    .setHorizontalAlignment("center").setBackground("#020617");

  const labels = [
    ["A3", "LEVEL"], ["C3", "RANK"], ["E3", "TOTAL XP"], ["G3", "FATIGUE"],
    ["A6", "MOMENTUM"], ["C6", "ACTIVE BOSS"], ["E6", "MAIN QUEST"], ["G6", "NEXT ACTION"]
  ];

  labels.forEach(([cell, value]) => {
    sh.getRange(cell).setValue(value).setFontWeight("bold").setFontColor("#38bdf8").setBackground("#111827");
  });

  sh.getRange("B3").setFormula("=State!B2");
  sh.getRange("D3").setFormula("=State!C2");
  sh.getRange("F3").setFormula("=State!D2");
  sh.getRange("H3").setFormula("=State!F2");
  sh.getRange("B6").setFormula("=State!G2");
  sh.getRange("D6").setFormula("=State!I2");
  sh.getRange("F6").setFormula("=State!J2");
  sh.getRange("H6").setFormula("=State!H2");

  sh.getRange("A9:H9").merge();
  sh.getRange("A9").setValue("STATS").setFontWeight("bold").setFontColor("#dff6ff").setBackground("#1e1b4b");

  sh.getRange("A10:D10").setValues([["Stat", "Level", "Progress", "Trend"]])
    .setFontWeight("bold").setFontColor("#dff6ff").setBackground("#111827");

  for (let i = 0; i < 8; i++) {
    const row = 11 + i;
    const statsRow = 2 + i;
    sh.getRange(row, 1).setFormula(`=Stats!A${statsRow}`);
    sh.getRange(row, 2).setFormula(`=Stats!C${statsRow}`);
    sh.getRange(row, 3).setFormula(`=Stats!G${statsRow}`);
    sh.getRange(row, 4).setFormula(`=Stats!H${statsRow}`);
  }

  sh.getRange("E10:H10").merge();
  sh.getRange("E10").setValue("CURRENT BOSSES").setFontWeight("bold").setFontColor("#dff6ff").setBackground("#111827");

  sh.getRange("E11").setFormula("=Bosses!B2");
  sh.getRange("F11").setFormula("=Bosses!G2");
  sh.getRange("G11").setFormula("=SPARKLINE(Bosses!G2/100,{\"charttype\",\"bar\";\"max\",1;\"color1\",\"#818cf8\"})");
  sh.getRange("H11").setFormula("=Bosses!I2");

  sh.getRange("A21:H21").merge();
  sh.getRange("A21").setValue("DAILY QUESTS").setFontWeight("bold").setFontColor("#dff6ff").setBackground("#1e1b4b");

  sh.getRange("A22:H22").setValues([["Quest", "Stat", "Boss", "XP", "Status", "Evidence", "Time", "Notes"]])
    .setFontWeight("bold").setFontColor("#dff6ff").setBackground("#111827");

  for (let i = 0; i < 6; i++) {
    const row = 23 + i;
    const qRow = 2 + i;
    sh.getRange(row, 1).setFormula(`=Daily_Quests!C${qRow}`);
    sh.getRange(row, 2).setFormula(`=Daily_Quests!E${qRow}`);
    sh.getRange(row, 3).setFormula(`=Daily_Quests!F${qRow}`);
    sh.getRange(row, 4).setFormula(`=Daily_Quests!G${qRow}`);
    sh.getRange(row, 5).setFormula(`=Daily_Quests!H${qRow}`);
    sh.getRange(row, 6).setFormula(`=Daily_Quests!I${qRow}`);
    sh.getRange(row, 7).setFormula(`=Daily_Quests!J${qRow}`);
    sh.getRange(row, 8).setFormula(`=Daily_Quests!K${qRow}`);
  }

  sh.getRange("A1:H30")
    .setBackground("#0b1020").setFontColor("#e5e7eb")
    .setBorder(true, true, true, true, true, true, "#38bdf8", SpreadsheetApp.BorderStyle.SOLID);

  sh.setColumnWidths(1, 8, 150);
  sh.setRowHeights(1, 30, 28);
}

function setupDropdowns_(ss) {
  const statusRule = SpreadsheetApp.newDataValidation()
    .requireValueInList(["Planned", "Complete", "Skipped", "Blocked", "Pending"], true)
    .build();

  const bossStatusRule = SpreadsheetApp.newDataValidation()
    .requireValueInList(["Active", "Paused", "Cleared", "Archived"], true)
    .build();

  ss.getSheetByName("Daily_Quests").getRange("H2:H200").setDataValidation(statusRule);
  ss.getSheetByName("Weekly_Quests").getRange("H2:H200").setDataValidation(statusRule);
  ss.getSheetByName("Bosses").getRange("F2:F100").setDataValidation(bossStatusRule);
}


/* ═══════════════════════════════════════════════════════════════════════════
 * 2. SYNC BRIDGE (WRITE) + 3. VERIFY ENDPOINT (READ)
 * ═══════════════════════════════════════════════════════════════════════════ */

function doGet(e) {
  try {
    const token = e && e.parameter && e.parameter.token ? String(e.parameter.token) : "";
    const action = e && e.parameter && e.parameter.action ? String(e.parameter.action) : "";

    // Read-only verify endpoint: ?action=verify&token=YOUR_TOKEN
    if (action === "verify") {
      const expected = getHunterToken_();
      if (!expected) {
        return jsonResponse_({ ok: false, error: "Token not set. Run setHunterToken first." });
      }
      if (token !== expected) {
        return jsonResponse_({ ok: false, error: "Unauthorized" });
      }

      const ss = SpreadsheetApp.getActiveSpreadsheet();
      return jsonResponse_({
        ok: true,
        result: {
          state: readSheetAsObjects_(ss, "State")[0] || {},
          stats: readSheetAsObjects_(ss, "Stats"),
          daily_quests: readSheetAsObjects_(ss, "Daily_Quests"),
          bosses: readSheetAsObjects_(ss, "Bosses"),
          weaknesses: readSheetAsObjects_(ss, "Weaknesses"),
          rank_rules: readSheetAsObjects_(ss, "Rank_Rules"),
          timestamp: new Date().toISOString()
        }
      });
    }

    // Default: online status. No data, no auth required (nothing sensitive).
    return jsonResponse_({
      ok: true,
      message: "Hunter Dashboard endpoint is online.",
      supported_actions: ["verify"],
      timestamp: new Date().toISOString()
    });

  } catch (err) {
    return jsonResponse_({ ok: false, error: err && err.message ? err.message : String(err) });
  }
}

function doPost(e) {
  try {
    if (!e || !e.postData || !e.postData.contents) {
      return jsonResponse_({ ok: false, error: "Missing POST body." });
    }

    const payload = JSON.parse(e.postData.contents);
    const expectedToken = getHunterToken_();

    if (!expectedToken) {
      return jsonResponse_({ ok: false, error: "Token not set. Run setHunterToken first." });
    }

    if (payload.token !== expectedToken) {
      return jsonResponse_({ ok: false, error: "Unauthorized: invalid token." });
    }

    const result = syncHunterPayload_(payload);

    return jsonResponse_({ ok: true, result: result, timestamp: new Date().toISOString() });

  } catch (err) {
    return jsonResponse_({ ok: false, error: String(err) });
  }
}

function syncHunterPayload_(payload) {
  const lock = LockService.getScriptLock();
  lock.waitLock(30000);

  try {
    const ss = SpreadsheetApp.getActiveSpreadsheet();
    const now = new Date();
    const tz = Session.getScriptTimeZone();
    const syncDate = payload.date || Utilities.formatDate(now, tz, 'yyyy-MM-dd');

    const counts = {
      state_updated: false,
      stats_updated: 0,
      daily_quests_replaced: 0,
      weekly_quests_updated: 0,
      main_quests_updated: 0,
      bosses_updated: 0,
      weaknesses_updated: 0,
      xp_log_appended: 0,
      system_log_appended: 0
    };

    if (payload.state) {
      updateState_(ss, payload.state, syncDate, now);
      counts.state_updated = true;
    }

    if (Array.isArray(payload.stats)) {
      const normalizedStats = payload.stats
        .map(function(s) {
          const statName = String(s.stat || s.Stat || s.name || s.Name || "").trim();
          const xpValue =
            s.xp_total !== undefined ? s.xp_total :
            s.xp_current !== undefined ? s.xp_current :
            s.current_xp !== undefined ? s.current_xp :
            s.total_xp !== undefined ? s.total_xp :
            s.xp !== undefined ? s.xp : "";

          return {
            stat: statName,
            xp_total: xpValue,                          // Stats!B; Dashboard reads Stats!B.
            trend_7d: s.trend_7d || s.trend || s.Trend || "",
            weakness_flag:
              s.weakness_flag !== undefined ? s.weakness_flag :
              s.weakness !== undefined ? s.weakness : false,
            evidence_gate: s.evidence_gate || s.evidence || "",
            last_updated: now
          };
        })
        .filter(function(s) { return s.stat !== ""; });

      counts.stats_updated = upsertByKey_(
        ss, 'Stats', 'stat', normalizedStats, {},
        // Formula/display-driven columns — never overwrite from the payload.
        ['level', 'xp_into_level', 'xp_to_next', 'progress_pct', 'progress_bar']
      );
    }

    if (Array.isArray(payload.daily_quests)) {
      counts.daily_quests_replaced = clearAndReplaceSheet_(ss, 'Daily_Quests', payload.daily_quests);
    }

    if (Array.isArray(payload.weekly_quests)) {
      counts.weekly_quests_updated = upsertByKey_(ss, 'Weekly_Quests', 'quest_id', payload.weekly_quests, {});
    }

    if (Array.isArray(payload.main_quests)) {
      counts.main_quests_updated = upsertByKey_(ss, 'Main_Quests', 'quest_id', payload.main_quests, {});
    }

    if (Array.isArray(payload.bosses)) {
      const normalizedBosses = payload.bosses
        .map(function(boss) {
          const bossId = boss.boss_id || boss.id || "";
          const progressValue =
            boss.progress_pct !== undefined ? boss.progress_pct :
            boss.progress_percent !== undefined ? boss.progress_percent :
            boss.progress !== undefined ? boss.progress : "";

          const out = { boss_id: bossId };
          if (boss.name !== undefined) out.name = boss.name;
          if (boss.area !== undefined) out.area = boss.area;
          if (boss.difficulty !== undefined) out.difficulty = boss.difficulty;
          if (boss.difficulty_label !== undefined) out.difficulty_label = boss.difficulty_label;
          if (boss.status !== undefined) out.status = boss.status;
          if (progressValue !== "") out.progress_pct = progressValue;
          if (boss.current_phase !== undefined) out.current_phase = boss.current_phase;
          if (boss.next_move !== undefined) out.next_move = boss.next_move;
          if (boss.main_blocker !== undefined) out.main_blocker = boss.main_blocker;
          if (boss.reward_xp !== undefined) out.reward_xp = boss.reward_xp;
          if (boss.notes !== undefined) out.notes = boss.notes;
          return out;
        })
        .filter(function(boss) { return boss.boss_id; });

      counts.bosses_updated = upsertByKey_(ss, 'Bosses', 'boss_id', normalizedBosses, {});
    }

    if (Array.isArray(payload.weaknesses)) {
      counts.weaknesses_updated = upsertByKey_(ss, 'Weaknesses', 'weakness_id', payload.weaknesses, {});
    }

    if (Array.isArray(payload.xp_log)) {
      counts.xp_log_appended = appendUniqueRows_(
        ss, 'XP_Log', payload.xp_log,
        ['timestamp', 'source_type', 'stat', 'xp', 'category', 'evidence_ref']
      );
    }

    appendSystemLog_(ss, {
      timestamp: now,
      event_type: 'sync',
      source: payload.source || 'external_payload',
      message: 'Hunter Dashboard sync completed.',
      notes: JSON.stringify(counts)
    });
    counts.system_log_appended = 1;

    SpreadsheetApp.flush();
    return counts;

  } finally {
    lock.releaseLock();
  }
}

function updateState_(ss, state, syncDate, now) {
  const sh = mustGetSheet_(ss, 'State');
  const headers = getHeaders_(sh);

  // Preserve formula-driven fields from the sheet.
  const protectedFields = ['character_level', 'rank', 'xp_to_next'];

  const rowNum = 2;
  const lastCol = sh.getLastColumn();
  const row = sh.getRange(rowNum, 1, 1, lastCol).getValues()[0];

  const stateWithDefaults = Object.assign({}, state, {
    date: state.date || syncDate,
    last_updated: now
  });

  headers.forEach((h, i) => {
    if (!h) return;
    if (protectedFields.indexOf(h) !== -1) return;
    if (Object.prototype.hasOwnProperty.call(stateWithDefaults, h)) {
      row[i] = stateWithDefaults[h];
    }
  });

  sh.getRange(rowNum, 1, 1, lastCol).setValues([row]);
}

function upsertByKey_(ss, sheetName, keyHeader, objects, defaults, protectedFields) {
  const sh = mustGetSheet_(ss, sheetName);
  const headers = getHeaders_(sh);
  const keyIdx = headers.indexOf(keyHeader);

  if (keyIdx === -1) {
    throw new Error(`Sheet ${sheetName} is missing key header ${keyHeader}`);
  }

  const protectedSet = {};
  (protectedFields || []).forEach(f => { protectedSet[f] = true; });

  const lastCol = sh.getLastColumn();
  const lastRow = sh.getLastRow();
  const existingMap = {};

  if (lastRow >= 2) {
    const existing = sh.getRange(2, 1, lastRow - 1, lastCol).getValues();
    existing.forEach((row, idx) => {
      const key = String(row[keyIdx] || '').trim();
      if (key) existingMap[key] = idx + 2;
    });
  }

  let count = 0;

  objects.forEach(objRaw => {
    const obj = Object.assign({}, defaults || {}, objRaw || {});
    const key = String(obj[keyHeader] || '').trim();
    if (!key) return;

    const rowNum = existingMap[key];

    if (rowNum) {
      const row = sh.getRange(rowNum, 1, 1, lastCol).getValues()[0];
      headers.forEach((h, i) => {
        if (!h) return;
        if (protectedSet[h]) return;
        if (Object.prototype.hasOwnProperty.call(obj, h)) row[i] = obj[h];
      });
      sh.getRange(rowNum, 1, 1, lastCol).setValues([row]);
    } else {
      const row = new Array(lastCol).fill('');
      headers.forEach((h, i) => {
        if (!h) return;
        if (protectedSet[h]) return;
        if (Object.prototype.hasOwnProperty.call(obj, h)) row[i] = obj[h];
      });
      sh.appendRow(row);
    }

    count++;
  });

  return count;
}

function clearAndReplaceSheet_(ss, sheetName, objects) {
  const sh = mustGetSheet_(ss, sheetName);
  const headers = getHeaders_(sh);
  const lastCol = sh.getLastColumn();
  const lastRow = sh.getLastRow();

  // Clear CONTENT only — do NOT delete rows (would break cross-sheet formula refs).
  if (lastRow >= 2) {
    sh.getRange(2, 1, lastRow - 1, lastCol).clearContent();
  }

  if (!objects || objects.length === 0) return 0;

  const rows = [];
  objects.forEach(objRaw => {
    const obj = objRaw || {};
    const row = new Array(lastCol).fill('');
    headers.forEach((h, i) => {
      if (!h) return;
      if (Object.prototype.hasOwnProperty.call(obj, h)) row[i] = obj[h];
    });
    rows.push(row);
  });

  sh.getRange(2, 1, rows.length, lastCol).setValues(rows);
  return rows.length;
}

function appendUniqueRows_(ss, sheetName, objects, uniqueHeaders) {
  const sh = mustGetSheet_(ss, sheetName);
  const headers = getHeaders_(sh);
  const lastCol = sh.getLastColumn();
  const lastRow = sh.getLastRow();

  const existingKeys = {};

  if (lastRow >= 2) {
    const existing = sh.getRange(2, 1, lastRow - 1, lastCol).getDisplayValues();
    existing.forEach(row => {
      existingKeys[buildUniqueKeyFromRow_(headers, row, uniqueHeaders)] = true;
    });
  }

  const rows = [];

  objects.forEach(obj => {
    const row = new Array(lastCol).fill('');
    headers.forEach((h, i) => {
      if (!h) return;
      if (Object.prototype.hasOwnProperty.call(obj, h)) row[i] = obj[h];
    });

    const key = buildUniqueKeyFromObject_(obj, uniqueHeaders);
    if (!existingKeys[key]) {
      rows.push(row);
      existingKeys[key] = true;
    }
  });

  if (rows.length > 0) {
    sh.getRange(sh.getLastRow() + 1, 1, rows.length, lastCol).setValues(rows);
  }

  return rows.length;
}

function appendSystemLog_(ss, entry) {
  const sh = mustGetSheet_(ss, 'System_Log');
  const headers = getHeaders_(sh);
  const lastCol = sh.getLastColumn();
  const row = new Array(lastCol).fill('');

  headers.forEach((h, i) => {
    if (!h) return;
    if (Object.prototype.hasOwnProperty.call(entry, h)) row[i] = entry[h];
  });

  sh.appendRow(row);
}


/* ─── shared helpers ──────────────────────────────────────────────────────── */

function readSheetAsObjects_(ss, sheetName) {
  const sh = ss.getSheetByName(sheetName);
  if (!sh) return [];

  const lastRow = sh.getLastRow();
  const lastCol = sh.getLastColumn();
  if (lastRow < 2 || lastCol < 1) return [];

  const headers = sh.getRange(1, 1, 1, lastCol).getValues()[0];
  const rows = sh.getRange(2, 1, lastRow - 1, lastCol).getValues();

  return rows
    .filter(row => row.some(cell => cell !== ""))
    .map(row => {
      const obj = {};
      headers.forEach((h, i) => { if (h) obj[h] = row[i]; });
      return obj;
    });
}

function mustGetSheet_(ss, name) {
  const sh = ss.getSheetByName(name);
  if (!sh) throw new Error(`Missing sheet: ${name}`);
  return sh;
}

function getHeaders_(sheet) {
  const lastCol = sheet.getLastColumn();
  return sheet.getRange(1, 1, 1, lastCol).getValues()[0].map(h => String(h || '').trim());
}

function buildUniqueKeyFromRow_(headers, row, uniqueHeaders) {
  return uniqueHeaders.map(h => {
    const idx = headers.indexOf(h);
    return idx === -1 ? '' : String(row[idx] || '').trim();
  }).join('|');
}

function buildUniqueKeyFromObject_(obj, uniqueHeaders) {
  return uniqueHeaders.map(h => String(obj[h] || '').trim()).join('|');
}

function jsonResponse_(obj) {
  return ContentService
    .createTextOutput(JSON.stringify(obj, null, 2))
    .setMimeType(ContentService.MimeType.JSON);
}


/* ─── optional self-test (safe; writes only test rows you can clear) ───────── */

function testHunterSyncLocal() {
  const today = Utilities.formatDate(new Date(), Session.getScriptTimeZone(), 'yyyy-MM-dd');

  const payload = {
    date: today,
    source: 'apps_script_local_test',
    state: {
      fatigue: 12,
      momentum: 'Rising',
      next_best_action: 'Verify the Hunter sync bridge.',
      active_boss: 'Build a Daily Discipline System',
      main_quest: 'Automate the Hunter dashboard bridge'
    },
    stats: [
      { stat: 'Execution', xp_total: 25, trend_7d: 'Rising', weakness_flag: false, evidence_gate: 'Open' },
      { stat: 'Knowledge', xp_total: 15, trend_7d: 'Stable', weakness_flag: false, evidence_gate: 'Open' }
    ],
    daily_quests: [
      {
        date: today, quest_id: 'DQ-SYNC-TEST', quest: 'Verify the Apps Script bridge',
        type: 'standard', linked_stat: 'Execution', linked_boss: 'BOSS-005',
        xp_value: 5, status: 'Complete', evidence: 'Apps Script local test',
        time_block: '', notes: 'If this appears in Daily_Quests, local sync works.'
      }
    ],
    xp_log: [
      {
        timestamp: new Date().toISOString(), source_type: 'sync_test', stat: 'Execution',
        xp: 5, category: 'test', evidence_ref: 'Apps Script local test',
        approved: true, notes: 'Testing the Hunter dashboard bridge.'
      }
    ]
  };

  const result = syncHunterPayload_(payload);
  Logger.log(JSON.stringify(result, null, 2));
}
