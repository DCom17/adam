// Jarvis Voice Local — Gmail Bridge (v1)
// Paste this whole file into a NEW Apps Script project's Code.gs, run
// setGmailBridgeToken() once, copy the logged token into your .env as
// GMAIL_BRIDGE_TOKEN, then Deploy as a Web app (Execute as: Me,
// Access: Anyone). The setup wizard walks you through every step.
//
// This bridge runs entirely in YOUR Google account. Jarvis never sees your
// Google password and never holds a Google credential — the bridge only
// trusts requests that carry the random token YOU generated below.
//
/****************************************************
 * WHAT THIS BRIDGE CAN AND CANNOT DO
 *   read  : list inbox/search results, get one message   (action: list / get)
 *   audit : aggregate promotional senders so JARVIS can SUGGEST Gmail filters
 *           (action: audit) — read-only; it counts senders, it never archives,
 *           deletes, or creates a filter. You apply any suggested filter in
 *           Gmail yourself. Paginated, because a multi-month inbox can exceed the
 *           Apps Script run limit in one pass.
 *   write : create a DRAFT reply for you to review        (action: create_draft)
 *   send  : send an email                                 (action: send)
 *           — but the SERVER refuses to send unless YOU set allow_send=true,
 *             and even then only after you approve the specific message. Out of
 *             the box Jarvis can only draft.
 *   DELETE / ARCHIVE / TRASH: NOT SUPPORTED. There is deliberately no code path
 *           here that deletes, archives, trashes, or marks mail — so nothing
 *           (not Jarvis, not Claude, not a stray request) can remove or hide a
 *           message through this bridge. Those stay manual actions you take in
 *           Gmail yourself.
 ****************************************************/

const GMAIL_BRIDGE_TOKEN_PROP = 'GMAIL_BRIDGE_TOKEN';
const MAX_RESULTS = 50;          // cap on how many messages a search returns
const MAX_RECIPIENTS = 25;       // cap on total to+cc+bcc addresses per message
const MAX_BODY_CHARS = 25000;    // cap on a draft/send body length
const SNIPPET_CHARS = 240;       // how much body text a list summary includes
const MAX_AUDIT_THREADS = 500;   // hard cap on threads scanned in one audit pass

function setGmailBridgeToken() {
  const token =
    Utilities.getUuid().replace(/-/g, '') +
    Utilities.getUuid().replace(/-/g, '');
  PropertiesService.getScriptProperties().setProperty(GMAIL_BRIDGE_TOKEN_PROP, token);
  Logger.log('GMAIL_BRIDGE_TOKEN=' + token);
  return token;
}

// Unauthenticated liveness ping only — returns NO mail data. The wizard hits
// this to confirm the URL is a deployed bridge before it asks for the token.
function doGet(e) {
  return jsonResponse_({
    ok: true,
    service: 'jarvis-gmail-bridge',
    version: 1,
    capabilities: ['list', 'get', 'audit', 'create_draft', 'send'],
    delete_supported: false,
    timestamp: new Date().toISOString()
  });
}

function doPost(e) {
  try {
    if (!e || !e.postData || !e.postData.contents) {
      return jsonResponse_({ ok: false, error: 'Missing POST body.' });
    }
    const payload = JSON.parse(e.postData.contents);

    const expectedToken = PropertiesService
      .getScriptProperties()
      .getProperty(GMAIL_BRIDGE_TOKEN_PROP);
    if (!expectedToken) {
      return jsonResponse_({
        ok: false,
        error: 'Bridge has no token yet. Run setGmailBridgeToken first.'
      });
    }
    if (payload.token !== expectedToken) {
      return jsonResponse_({ ok: false, error: 'Unauthorized: invalid token.' });
    }

    const action = String(payload.action || 'list').trim().toLowerCase();
    let result;
    switch (action) {
      case 'list':         result = listMessages_(payload);     break;
      case 'get':          result = getMessage_(payload);       break;
      case 'audit':        result = auditMarketing_(payload);   break;
      case 'create_draft': result = createDraft_(payload);      break;
      case 'send':         result = sendMessage_(payload);      break;
      // 'delete' / 'archive' / 'trash' intentionally omitted — there is no
      // handler for them by design.
      default:
        return jsonResponse_({ ok: false, error: 'Unknown action: ' + action });
    }
    return jsonResponse_({ ok: true, action: action, result: result, timestamp: new Date().toISOString() });
  } catch (err) {
    return jsonResponse_({ ok: false, error: String(err) });
  }
}

// ---- READ -----------------------------------------------------------------

function listMessages_(payload) {
  const query = String(payload.query || 'in:inbox').trim();
  let max = parseInt(payload.max, 10);
  if (isNaN(max) || max < 1) max = 20;
  if (max > MAX_RESULTS) max = MAX_RESULTS;
  const threads = GmailApp.search(query, 0, max);
  const messages = [];
  for (let t = 0; t < threads.length; t++) {
    const msgs = threads[t].getMessages();
    const msg = msgs[msgs.length - 1]; // most recent in the thread
    messages.push(summarizeMessage_(msg, threads[t]));
  }
  return {
    account: Session.getActiveUser().getEmail(),
    query: query,
    count: messages.length,
    messages: messages
  };
}

function getMessage_(payload) {
  const messageId = String(payload.message_id || '').trim();
  if (!messageId) throw new Error('get requires message_id.');
  const msg = GmailApp.getMessageById(messageId);
  if (!msg) throw new Error('Message not found: ' + messageId);
  const full = summarizeMessage_(msg, msg.getThread());
  full.body = (msg.getPlainBody() || '').substring(0, MAX_BODY_CHARS);
  return { account: Session.getActiveUser().getEmail(), message: full };
}

// ---- READ: audit promotional senders (for filter SUGGESTIONS) -------------
// Aggregates senders over a window so JARVIS can PROPOSE Gmail filters. Strictly
// read-only — it counts and flags, it never archives, deletes, marks, or creates
// a filter. You apply any suggested filter yourself in Gmail. Paginated via
// `offset`: a busy multi-month inbox can exceed the Apps Script run limit in one
// pass, so when `next_offset` comes back non-null, run again with that offset.

function auditMarketing_(payload) {
  let days = parseInt(payload.days, 10);
  if (isNaN(days) || days < 1) days = 90;
  const query = String(payload.query || ('newer_than:' + days + 'd category:promotions')).trim();
  let max = parseInt(payload.max_threads, 10);
  if (isNaN(max) || max < 1) max = 200;
  if (max > MAX_AUDIT_THREADS) max = MAX_AUDIT_THREADS;
  let offset = parseInt(payload.offset, 10);
  if (isNaN(offset) || offset < 0) offset = 0;

  const threads = GmailApp.search(query, offset, max);
  const bySender = {};
  for (let t = 0; t < threads.length; t++) {
    const msgs = threads[t].getMessages();
    const msg = msgs[msgs.length - 1]; // most recent in the thread
    const rawFrom = msg.getFrom() || 'unknown';
    const email = extractEmail_(rawFrom);
    let marketing = false;
    try { marketing = !!msg.getHeader('List-Unsubscribe'); } catch (e) {}
    if (!bySender[email]) {
      bySender[email] = {
        from_email: email,
        from_name: extractName_(rawFrom),
        domain: email.indexOf('@') >= 0 ? email.split('@')[1] : '',
        count: 0,
        marketing: false,
        sample_subject: (msg.getSubject() || '').substring(0, 200)
      };
    }
    bySender[email].count += 1;
    if (marketing) bySender[email].marketing = true;
  }
  const senders = Object.keys(bySender).map(function (k) { return bySender[k]; });
  senders.sort(function (a, b) { return b.count - a.count; });

  const scanned = threads.length;
  // A full page probably means there is more — hand back the next offset so the
  // caller can run another pass (exactly what a big inbox needs).
  const nextOffset = (scanned === max) ? (offset + scanned) : null;
  return {
    account: Session.getActiveUser().getEmail(),
    query: query,
    window_days: days,
    offset: offset,
    scanned_threads: scanned,
    next_offset: nextOffset,
    more_likely: nextOffset !== null,
    unique_senders: senders.length,
    senders: senders
  };
}

// ---- WRITE: create a draft (never sends) ----------------------------------

function createDraft_(payload) {
  const lock = LockService.getScriptLock();
  lock.waitLock(30000);
  try {
    const fields = validateOutgoing_(payload);
    const options = buildOptions_(fields);
    let draft;
    if (fields.reply_to_message_id) {
      const orig = GmailApp.getMessageById(fields.reply_to_message_id);
      if (!orig) throw new Error('Cannot reply: original message not found.');
      draft = orig.createDraftReply(fields.body, options);
    } else {
      draft = GmailApp.createDraft(fields.to, fields.subject, fields.body, options);
    }
    const dmsg = draft.getMessage();
    return {
      mode: 'draft',
      draft_id: draft.getId(),
      to: fields.to,
      subject: dmsg.getSubject(),
      sent: false
    };
  } finally {
    lock.releaseLock();
  }
}

// ---- SEND -----------------------------------------------------------------
// The bridge can technically send, but the JARVIS SERVER refuses to call this
// unless the user set allow_send=true AND approved the specific message. There
// is no auto-send path on the client side.

function sendMessage_(payload) {
  const lock = LockService.getScriptLock();
  lock.waitLock(30000);
  try {
    const fields = validateOutgoing_(payload);
    const options = buildOptions_(fields);
    if (fields.reply_to_message_id) {
      const orig = GmailApp.getMessageById(fields.reply_to_message_id);
      if (!orig) throw new Error('Cannot reply: original message not found.');
      orig.reply(fields.body, options);
    } else {
      GmailApp.sendEmail(fields.to, fields.subject, fields.body, options);
    }
    return { mode: 'send', to: fields.to, subject: fields.subject, sent: true };
  } finally {
    lock.releaseLock();
  }
}

// ---- helpers --------------------------------------------------------------

function validateOutgoing_(payload) {
  const to = String(payload.to || '').trim();
  if (!to) throw new Error('Recipient (to) is required.');
  const subject = (payload.subject === undefined || payload.subject === null)
    ? '' : String(payload.subject);
  let body = String(payload.body || '');
  if (body.length > MAX_BODY_CHARS) {
    throw new Error('Body exceeds ' + MAX_BODY_CHARS + ' characters. Refusing for safety.');
  }
  const cc = String(payload.cc || '').trim();
  const bcc = String(payload.bcc || '').trim();
  const recipientCount = countAddresses_(to) + countAddresses_(cc) + countAddresses_(bcc);
  if (recipientCount > MAX_RECIPIENTS) {
    throw new Error('Refusing to address more than ' + MAX_RECIPIENTS + ' recipients in one message.');
  }
  return {
    to: to, subject: subject, body: body, cc: cc, bcc: bcc,
    reply_to_message_id: String(payload.reply_to_message_id || '').trim()
  };
}

function buildOptions_(fields) {
  const options = {};
  if (fields.cc) options.cc = fields.cc;
  if (fields.bcc) options.bcc = fields.bcc;
  return options;
}

function countAddresses_(addrField) {
  if (!addrField) return 0;
  return addrField.split(',').map(function (s) { return s.trim(); })
    .filter(function (s) { return s.length > 0; }).length;
}

function summarizeMessage_(msg, thread) {
  const tz = Session.getScriptTimeZone();
  let snippet = '';
  try { snippet = (msg.getPlainBody() || '').replace(/\s+/g, ' ').substring(0, SNIPPET_CHARS); } catch (e) {}
  return {
    message_id: msg.getId(),
    thread_id: thread ? thread.getId() : '',
    from: msg.getFrom() || '',
    to: msg.getTo() || '',
    subject: (msg.getSubject() || '').substring(0, 250),
    date: Utilities.formatDate(msg.getDate(), tz, "yyyy-MM-dd'T'HH:mm:ssXXX"),
    unread: thread ? thread.isUnread() : false,
    snippet: snippet
  };
}

function extractEmail_(from) {
  const m = from.match(/<([^>]+)>/);
  if (m) return m[1].toLowerCase().trim();
  const bare = from.match(/[^\s<>]+@[^\s<>]+/);
  return bare ? bare[0].toLowerCase().trim() : from.toLowerCase().trim();
}

function extractName_(from) {
  const m = from.match(/^"?([^"<]+?)"?\s*</);
  return m ? m[1].trim() : '';
}

function jsonResponse_(obj) {
  return ContentService
    .createTextOutput(JSON.stringify(obj, null, 2))
    .setMimeType(ContentService.MimeType.JSON);
}

// Run from the Apps Script editor to confirm read + draft work locally. Lists
// one inbox message and drafts a throwaway email TO YOURSELF (never sends).
// Delete the draft yourself afterward — the bridge cannot delete.
function testGmailBridgeLocal() {
  const me = Session.getActiveUser().getEmail();
  const listed = listMessages_({ query: 'in:inbox', max: 1 });
  const drafted = createDraft_({
    to: me,
    subject: 'Jarvis Bridge Self-Test (draft only)',
    body: 'Local bridge test draft. Nothing was sent. Delete me manually.'
  });
  Logger.log(JSON.stringify({ account: me, listed_count: listed.count, drafted: drafted }, null, 2));
}
