// ─── HUNTER DASHBOARD — READ / VERIFY ENDPOINT (standalone fragment) ────────
// Ships with Adam. This is the MINIMAL read-only endpoint, for the
// case where you already have a sync script in your Sheet that lacks a verify
// path. If you pasted the full hunter_dashboard.gs, you do NOT need this — that
// file already contains a doGet with the verify action. (A Sheet can only have
// ONE doGet, so never paste both.)
//
// It is READ-ONLY: returns your current State / Stats / Daily_Quests so Adam
// can confirm a sync landed. It writes nothing and cannot delete anything.
//
// Called by the Hunter connector via GET:  ?token=...&action=verify
//
// Auth uses the SAME token model as the sync bridge: a HUNTER_TOKEN stored in
// Script Properties (run setHunterToken() once — see hunter_dashboard.gs). The
// token is never hardcoded in this file. After pasting/editing, redeploy:
//   Deploy → Manage deployments → Edit → New version → Deploy.

function doGet(e) {
  try {
    const token = e && e.parameter && e.parameter.token;
    const expected = PropertiesService.getScriptProperties().getProperty("HUNTER_TOKEN");
    if (!expected) {
      return ContentService
        .createTextOutput(JSON.stringify({ ok: false, error: "Token not set. Run setHunterToken first." }))
        .setMimeType(ContentService.MimeType.JSON);
    }
    if (token !== expected) {
      return ContentService
        .createTextOutput(JSON.stringify({ ok: false, error: "Unauthorized" }))
        .setMimeType(ContentService.MimeType.JSON);
    }

    // Read-only: the only supported action is "verify". No write/delete path exists here.
    const action = (e && e.parameter && e.parameter.action) || "verify";
    if (action !== "verify") {
      return ContentService
        .createTextOutput(JSON.stringify({ ok: false, error: "Unsupported action" }))
        .setMimeType(ContentService.MimeType.JSON);
    }

    const ss = SpreadsheetApp.getActiveSpreadsheet();

    const state        = readSheetAsObjects_(ss, "State")[0] || {};
    const stats        = readSheetAsObjects_(ss, "Stats");
    const daily_quests = readSheetAsObjects_(ss, "Daily_Quests");

    return ContentService
      .createTextOutput(JSON.stringify({
        ok: true,
        result: {
          state,
          stats,
          daily_quests,
          timestamp: new Date().toISOString()
        }
      }))
      .setMimeType(ContentService.MimeType.JSON);

  } catch (err) {
    return ContentService
      .createTextOutput(JSON.stringify({ ok: false, error: err.message }))
      .setMimeType(ContentService.MimeType.JSON);
  }
}

function readSheetAsObjects_(ss, sheetName) {
  const sh = ss.getSheetByName(sheetName);
  if (!sh) return [];

  const lastRow = sh.getLastRow();
  const lastCol = sh.getLastColumn();
  if (lastRow < 2 || lastCol < 1) return [];

  const headers = sh.getRange(1, 1, 1, lastCol).getValues()[0];
  const rows    = sh.getRange(2, 1, lastRow - 1, lastCol).getValues();

  return rows
    .filter(row => row.some(cell => cell !== ""))
    .map(row => {
      const obj = {};
      headers.forEach((h, i) => { if (h) obj[h] = row[i]; });
      return obj;
    });
}
