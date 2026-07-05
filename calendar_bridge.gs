// Adam — Google Calendar Bridge (v2)
// Paste this whole file into a NEW Apps Script project's Code.gs, run
// setCalendarSyncToken() once, copy the logged token into your .env as
// GOOGLE_CALENDAR_TOKEN, then Deploy as a Web app (Execute as: Me,
// Access: Anyone). The setup wizard walks you through every step.
//
// This bridge runs entirely in YOUR Google account. Adam never sees your
// Google password and never holds a Google credential — the bridge only
// trusts requests that carry the random token YOU generated below.
//
/****************************************************
 * WHAT THIS BRIDGE CAN AND CANNOT DO
 *   read  : list events in a time window, get one event   (action: list / get)
 *   write : create events, edit an existing event          (action: create / update)
 *   DELETE: NOT SUPPORTED. There is deliberately no delete code path here, so
 *           nothing — not Adam, not Claude, not a stray request — can delete
 *           a calendar event through this bridge. Removing events stays a manual
 *           action you take in Google Calendar yourself.
 ****************************************************/

const CALENDAR_SYNC_TOKEN_PROP = 'CALENDAR_SYNC_TOKEN';
const MAX_CREATE_PER_REQUEST = 25;
const MAX_EVENT_HOURS = 12;

function setCalendarSyncToken() {
  const token =
    Utilities.getUuid().replace(/-/g, '') +
    Utilities.getUuid().replace(/-/g, '');
  PropertiesService.getScriptProperties().setProperty(CALENDAR_SYNC_TOKEN_PROP, token);
  Logger.log('GOOGLE_CALENDAR_TOKEN=' + token);
  return token;
}

// Unauthenticated liveness ping only — returns NO calendar data. The wizard hits
// this to confirm the URL is a deployed bridge before it asks for the token.
function doGet(e) {
  return jsonResponse_({
    ok: true,
    service: 'adam-calendar-bridge',
    version: 2,
    capabilities: ['list', 'get', 'create', 'update'],
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
      .getProperty(CALENDAR_SYNC_TOKEN_PROP);
    if (!expectedToken) {
      return jsonResponse_({
        ok: false,
        error: 'Bridge has no token yet. Run setCalendarSyncToken first.'
      });
    }
    if (payload.token !== expectedToken) {
      return jsonResponse_({ ok: false, error: 'Unauthorized: invalid token.' });
    }

    // Default to 'create' so existing create-only clients keep working unchanged.
    const action = String(payload.action || 'create').trim().toLowerCase();
    let result;
    switch (action) {
      case 'create': result = createCalendarEvents_(payload); break;
      case 'list':   result = listCalendarEvents_(payload);   break;
      case 'get':    result = getCalendarEvent_(payload);     break;
      case 'update': result = updateCalendarEvent_(payload);  break;
      // 'delete' intentionally omitted — there is no delete handler by design.
      default:
        return jsonResponse_({ ok: false, error: 'Unknown action: ' + action });
    }
    return jsonResponse_({ ok: true, action: action, result: result, timestamp: new Date().toISOString() });
  } catch (err) {
    return jsonResponse_({ ok: false, error: String(err) });
  }
}

// ---- READ -----------------------------------------------------------------

function listCalendarEvents_(payload) {
  const calendarId = String(payload.calendar_id || 'primary').trim();
  const calendar = getCalendar_(calendarId);
  const start = new Date(payload.time_min);
  const end = new Date(payload.time_max);
  if (isNaN(start.getTime()) || isNaN(end.getTime())) {
    throw new Error('list requires valid time_min and time_max.');
  }
  if (end <= start) {
    throw new Error('time_max must be after time_min.');
  }
  const events = calendar.getEvents(start, end).map(serializeEvent_);
  return { calendar_id: calendarId, count: events.length, events: events };
}

function getCalendarEvent_(payload) {
  const calendarId = String(payload.calendar_id || 'primary').trim();
  const eventId = String(payload.event_id || '').trim();
  if (!eventId) throw new Error('get requires event_id.');
  const calendar = getCalendar_(calendarId);
  const event = calendar.getEventById(eventId);
  if (!event) throw new Error('Event not found: ' + eventId);
  return { calendar_id: calendarId, event: serializeEvent_(event) };
}

// ---- WRITE: create --------------------------------------------------------

function createCalendarEvents_(payload) {
  const lock = LockService.getScriptLock();
  lock.waitLock(30000);
  try {
    const events = payload.events || [];
    if (!Array.isArray(events) || events.length === 0) {
      throw new Error('Payload must include a non-empty events array.');
    }
    if (events.length > MAX_CREATE_PER_REQUEST) {
      throw new Error('Refusing to create more than ' + MAX_CREATE_PER_REQUEST + ' events in one request.');
    }
    const created = [];
    const skipped = [];
    events.forEach((eventData, index) => {
      const validation = validateEventTimes_(eventData, index);
      if (!validation.ok) {
        skipped.push({ index: index, title: (eventData && eventData.title) || '', reason: validation.error });
        return;
      }
      const calendar = getCalendar_(String(eventData.calendar_id || 'primary').trim());
      const options = {};
      if (eventData.description) options.description = String(eventData.description);
      if (eventData.location) options.location = String(eventData.location);
      if (eventData.guests && eventData.allow_guests === true) {
        options.guests = String(eventData.guests);
        options.sendInvites = eventData.send_invites === true;
      }
      const event = calendar.createEvent(
        String(eventData.title).trim(),
        new Date(eventData.start),
        new Date(eventData.end),
        options
      );
      created.push(serializeEvent_(event));
    });
    return { mode: 'create', requested_count: events.length, created_count: created.length,
             skipped_count: skipped.length, created: created, skipped: skipped };
  } finally {
    lock.releaseLock();
  }
}

// ---- WRITE: update (edit only — never deletes) ----------------------------

function updateCalendarEvent_(payload) {
  const lock = LockService.getScriptLock();
  lock.waitLock(30000);
  try {
    const calendarId = String(payload.calendar_id || 'primary').trim();
    const eventId = String(payload.event_id || '').trim();
    if (!eventId) throw new Error('update requires event_id.');
    const changes = payload.changes || {};
    if (!changes || typeof changes !== 'object' || Object.keys(changes).length === 0) {
      throw new Error('update requires a non-empty changes object.');
    }
    const calendar = getCalendar_(calendarId);
    const event = calendar.getEventById(eventId);
    if (!event) throw new Error('Event not found: ' + eventId);

    // If either time is being changed, validate the resulting window.
    if (changes.start !== undefined || changes.end !== undefined) {
      const newStart = new Date(changes.start !== undefined ? changes.start : event.getStartTime());
      const newEnd = new Date(changes.end !== undefined ? changes.end : event.getEndTime());
      const v = validateEventTimes_({ start: newStart.toISOString(), end: newEnd.toISOString() }, 0);
      if (!v.ok) throw new Error(v.error);
      event.setTime(newStart, newEnd);
    }
    if (changes.title !== undefined) event.setTitle(String(changes.title));
    if (changes.location !== undefined) event.setLocation(String(changes.location));
    if (changes.description !== undefined) event.setDescription(String(changes.description));

    return { calendar_id: calendarId, updated: true, event: serializeEvent_(event) };
  } finally {
    lock.releaseLock();
  }
}

// ---- helpers --------------------------------------------------------------

function validateEventTimes_(eventData, index) {
  if (!eventData) return { ok: false, error: 'Event ' + index + ' is empty.' };
  if (!eventData.title && eventData.title !== '') {
    // title may be absent for an update that only touches times; create paths
    // pass a title and the check below still guards it.
  }
  if (eventData.title !== undefined && String(eventData.title).trim() === '' && index >= 0 && eventData.start) {
    return { ok: false, error: 'Event ' + index + ' is missing title.' };
  }
  if (!eventData.start) return { ok: false, error: 'Event ' + index + ' is missing start.' };
  if (!eventData.end) return { ok: false, error: 'Event ' + index + ' is missing end.' };
  const start = new Date(eventData.start);
  const end = new Date(eventData.end);
  if (isNaN(start.getTime())) return { ok: false, error: 'Event ' + index + ' has invalid start datetime.' };
  if (isNaN(end.getTime())) return { ok: false, error: 'Event ' + index + ' has invalid end datetime.' };
  if (end <= start) return { ok: false, error: 'Event ' + index + ' end must be after start.' };
  const hours = (end.getTime() - start.getTime()) / (1000 * 60 * 60);
  if (hours > MAX_EVENT_HOURS) {
    return { ok: false, error: 'Event ' + index + ' is longer than ' + MAX_EVENT_HOURS + ' hours. Refusing for safety.' };
  }
  return { ok: true };
}

function serializeEvent_(event) {
  return {
    event_id: event.getId(),
    title: event.getTitle(),
    start: event.getStartTime().toISOString(),
    end: event.getEndTime().toISOString(),
    all_day: event.isAllDayEvent(),
    location: event.getLocation() || '',
    description: event.getDescription() || ''
  };
}

function getCalendar_(calendarId) {
  if (!calendarId || calendarId === 'primary') return CalendarApp.getDefaultCalendar();
  const calendar = CalendarApp.getCalendarById(calendarId);
  if (!calendar) throw new Error('Calendar not found: ' + calendarId);
  return calendar;
}

function jsonResponse_(obj) {
  return ContentService
    .createTextOutput(JSON.stringify(obj, null, 2))
    .setMimeType(ContentService.MimeType.JSON);
}

// Run from the Apps Script editor to confirm read+create+update work locally.
// Creates a throwaway event 10 minutes out, reads it back, edits its title,
// then leaves it for you to delete manually (the bridge cannot delete).
function testCalendarBridgeLocal() {
  const now = new Date();
  const start = new Date(now.getTime() + 10 * 60 * 1000);
  const end = new Date(now.getTime() + 20 * 60 * 1000);
  const made = createCalendarEvents_({
    events: [{ title: 'Adam Bridge Self-Test', start: start.toISOString(),
               end: end.toISOString(), calendar_id: 'primary',
               description: 'Local bridge test. Delete manually.' }]
  });
  const id = made.created[0].event_id;
  const listed = listCalendarEvents_({ time_min: now.toISOString(), time_max: end.toISOString() });
  const edited = updateCalendarEvent_({ event_id: id, changes: { title: 'Adam Bridge Self-Test (edited)' } });
  Logger.log(JSON.stringify({ made: made, listed_count: listed.count, edited: edited.updated }, null, 2));
}
