"""Proactive reminders — the decision logic, the banner copy, and the panel API.

What matters most:
  * a reminder fires only when the thing genuinely hasn't happened (real data),
    at most once a day (water: 3 spaced nudges), never in quiet hours, never
    while paused or switched off, and never for a tracker the user doesn't use;
  * banner titles carry the whole ask and fit a lock screen;
  * every route is token-gated, and nothing here can reach the owner's live
    state or push devices (config root is a temp dir; the sender is a fake).
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("ADAM_CONFIG_ROOT", tempfile.mkdtemp(prefix="jvl_rem_cfg_"))

import config  # noqa: E402

if not config.ADAM_TOKEN:
    config.ADAM_TOKEN = "reminder-test-token-" + "r" * 32
if not config.CLAUDE_EXE:
    config.CLAUDE_EXE = sys.executable

import reminders as rm  # noqa: E402
import server  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(server.app)
AUTH = {"Authorization": f"Bearer {config.ADAM_TOKEN}"}


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """Prefs/state files in a temp dir; trackers pointed at files that don't exist."""
    monkeypatch.setattr(config, "STATE_DIR", tmp_path, raising=False)
    monkeypatch.setattr(config, "HEALTH_DB", tmp_path / "none-health.db", raising=False)
    monkeypatch.setattr(config, "FINANCE_DB", tmp_path / "none-finance.db", raising=False)
    monkeypatch.setattr(config, "VAULT_PATH", str(tmp_path / "vault"), raising=False)
    import reminder_schedule
    monkeypatch.setattr(reminder_schedule, "_calendar_events", lambda now: [])  # never the real bridge
    assert rm._prefs_file().parent == tmp_path
    yield


def P(**over):
    p = rm._merge_prefs(None)
    p.update(over)
    return p


ALL_ACTIVE = {
    "water_active": True, "water_ml": 0, "water_target_ml": 2400, "water_unit": "oz",
    "meal_active": True, "meals_today": 0,
    "weight_active": True, "weighed_today": False,
    "plan_active": True, "planned_today": False,
    "finance_active": True, "finance_last_import_ts": 0, "finance_unreviewed": 0,
}


def at(h, m=0, day=5):  # 2026-10-05 is a Monday
    return datetime(2026, 10, day, h, m)


# --- evaluate: gates ---------------------------------------------------------

def test_master_off_pause_and_quiet_hours_silence_everything():
    f = dict(ALL_ACTIVE)
    assert rm.evaluate(at(10, 30), P(), {}, f)  # sanity: something is due
    assert rm.evaluate(at(10, 30), P(enabled=False), {}, f) == []
    assert rm.evaluate(at(10, 30), P(paused_until=at(23).timestamp()), {}, f) == []
    assert rm.evaluate(at(6, 30), P(), {}, f) == []        # before quiet_end 07:00
    assert rm.evaluate(at(22, 15), P(), {}, f) == []       # after quiet_start 22:00


def test_quiet_hours_wrap_and_non_wrap():
    assert rm.in_quiet_hours(at(23), P())
    assert rm.in_quiet_hours(at(3), P())
    assert not rm.in_quiet_hours(at(12), P())
    p = P(quiet_start="13:00", quiet_end="14:00")
    assert rm.in_quiet_hours(at(13, 30), p) and not rm.in_quiet_hours(at(15), p)


def test_inactive_trackers_stay_silent():
    f = {k: v for k, v in ALL_ACTIVE.items()}
    for key in ("water_active", "meal_active", "weight_active", "plan_active", "finance_active"):
        f[key] = False
    for h in range(7, 22):
        assert rm.evaluate(at(h, 45, day=11), P(), {}, f) == []  # day 11 = Sunday


def test_kind_toggle_off():
    p = P()
    p["kinds"]["plan"]["on"] = False
    assert "plan" not in rm.evaluate(at(9, 30), p, {}, dict(ALL_ACTIVE))


# --- evaluate: each reminder ------------------------------------------------------

def test_plan_window_and_done_check():
    f = dict(ALL_ACTIVE)
    assert "plan" not in rm.evaluate(at(8, 59), P(), {}, f)
    assert "plan" in rm.evaluate(at(9, 0), P(), {}, f)
    assert "plan" not in rm.evaluate(at(12, 30), P(), {}, f)   # window closed
    assert "plan" not in rm.evaluate(at(9, 30), P(), {}, dict(f, planned_today=True))


def test_once_per_day():
    f = dict(ALL_ACTIVE)
    state = {"days": {"2026-10-05": {"fired": {"plan": 1.0}, "water_ts": []}}}
    assert "plan" not in rm.evaluate(at(9, 30), P(), state, f)
    # tomorrow is a fresh day
    assert "plan" in rm.evaluate(at(9, 30, day=6), P(), state, f)


def test_meals_am_and_pm_snapshot():
    f = dict(ALL_ACTIVE)
    assert "meal_am" in rm.evaluate(at(12), P(), {}, f)
    assert "meal_am" not in rm.evaluate(at(12), P(), {}, dict(f, meals_today=1))
    # PM: compares against the 15:00 snapshot — nothing new since then = due
    st = {"days": {"2026-10-05": {"fired": {}, "water_ts": [], "meals_at_snapshot": 2}}}
    assert "meal_pm" in rm.evaluate(at(19, 45), P(), st, dict(f, meals_today=2))
    assert "meal_pm" not in rm.evaluate(at(19, 45), P(), st, dict(f, meals_today=3))
    # no snapshot (server was down at 15:00): fewer than two today = due
    assert "meal_pm" in rm.evaluate(at(19, 45), P(), {}, dict(f, meals_today=1))
    assert "meal_pm" not in rm.evaluate(at(19, 45), P(), {}, dict(f, meals_today=2))


def test_weigh_in():
    f = dict(ALL_ACTIVE)
    assert "weigh_in" in rm.evaluate(at(8), P(), {}, f)
    assert "weigh_in" not in rm.evaluate(at(8), P(), {}, dict(f, weighed_today=True))
    assert "weigh_in" not in rm.evaluate(at(7, 15), P(), {}, f)
    # a morning weight only: missed by 11:00 = skipped, never nagged at night
    assert "weigh_in" not in rm.evaluate(at(11), P(), {}, f)
    assert "weigh_in" not in rm.evaluate(at(20, 30), P(), {}, f)


def test_finance_only_on_chosen_day_and_when_stale():
    f = dict(ALL_ACTIVE)
    sunday = at(18, day=11)
    assert "finance_csv" in rm.evaluate(sunday, P(), {}, f)
    assert "finance_csv" not in rm.evaluate(at(18, day=10), P(), {}, f)  # Saturday
    fresh = dict(f, finance_last_import_ts=sunday.timestamp() - 2 * 86400)
    assert "finance_csv" not in rm.evaluate(sunday, P(), {}, fresh)
    assert "finance_csv" in rm.evaluate(at(18, day=10), P(finance_day=5), {}, f)


def test_water_pace_gap_and_cap():
    f = dict(ALL_ACTIVE, water_target_ml=2400)
    # 13:00 = ~44% through 07:00-20:30 -> ~1070 ml expected
    assert "water" in rm.evaluate(at(13), P(), {}, dict(f, water_ml=200))
    assert "water" not in rm.evaluate(at(13), P(), {}, dict(f, water_ml=1000))  # on pace
    recent = {"days": {"2026-10-05": {"fired": {}, "water_ts": [at(12).timestamp()]}}}
    assert "water" not in rm.evaluate(at(13), P(), recent, dict(f, water_ml=0))  # < 2.5 h gap
    capped = {"days": {"2026-10-05": {"fired": {}, "water_ts": [1.0, 2.0, 3.0]}}}
    assert "water" not in rm.evaluate(at(16), P(), capped, dict(f, water_ml=0))


def test_user_time_after_window_still_gets_an_hour():
    p = P()
    p["kinds"]["plan"]["time"] = "13:00"   # later than plan's 12:30 'until'
    assert "plan" in rm.evaluate(at(13, 30), p, {}, dict(ALL_ACTIVE))


# --- copy -----------------------------------------------------------------------

@pytest.mark.parametrize("kind", rm.KIND_IDS)
def test_copy_fits_a_lock_screen_and_stays_in_character(kind):
    now = at(13)
    for facts in ({}, dict(ALL_ACTIVE, water_ml=300, finance_last_import_ts=now.timestamp() - 9 * 86400,
                           finance_unreviewed=2)):
        m = rm.build_message(kind, facts, P(), now)
        assert 0 < len(m["title"]) <= rm.TITLE_MAX
        assert 0 < len(m["body"]) <= rm.BODY_MAX
        assert "sir" in m["title"]
        assert m["action"] in ("plan", "health", "meal", "finance")


def test_water_copy_names_the_numbers():
    m = rm.build_message("water", dict(ALL_ACTIVE, water_ml=591, water_target_ml=2366), P(), at(15))
    assert m["title"] == "Water check, sir: 20 of 80 oz"
    assert "behind pace" in m["body"]


# --- tick: sends, records, calendar hold -------------------------------------------

def test_tick_sends_once_and_respects_calendar_hold():
    sent = []
    f = dict(ALL_ACTIVE, weighed_today=True)              # morning weigh-in would close first
    out = rm.tick(sent.append, now=at(9, 5), facts=f, blocks=[{"start": 0, "end": 1440, "label": "Call", "out": False, "busy": True, "source": "calendar"}])
    assert out == [] and sent == []                      # in a meeting: waits
    out = rm.tick(sent.append, now=at(9, 6), facts=f, blocks=[])
    assert [m["kind"] for m in out] == ["plan"]
    out = rm.tick(sent.append, now=at(9, 7), facts=f, blocks=[])
    assert out == []                                      # already sent today
    assert rm.load_state()["log"][0]["kind"] == "plan"


def test_tick_failed_send_is_retried():
    def boom(_m):
        raise RuntimeError("no devices")
    f = dict(ALL_ACTIVE)
    assert rm.tick(boom, now=at(9, 5), facts=f, blocks=[]) == []
    got = []
    assert rm.tick(got.append, now=at(9, 6), facts=f, blocks=[])


def test_tick_records_meal_snapshot_after_three():
    rm.tick(lambda m: None, now=at(15, 1), facts=dict(ALL_ACTIVE, meals_today=2), blocks=[])
    assert rm.load_state()["days"]["2026-10-05"]["meals_at_snapshot"] == 2


def test_gather_facts_without_trackers_is_all_quiet():
    f = rm.gather_facts(at(12))
    assert not any(f.get(k) for k in ("water_active", "meal_active", "weight_active",
                                      "plan_active", "finance_active"))
    assert not (Path(config.HEALTH_DB)).exists()   # a background read never creates the DB


# --- prefs validation -------------------------------------------------------------

def test_save_prefs_validates():
    out = rm.save_prefs({"quiet_start": "25:00", "finance_day": 9,
                         "kinds": {"water": {"on": False, "time": "nope"}, "bogus": {"on": True}}})
    assert out["quiet_start"] == "22:00" and out["finance_day"] == 6
    assert out["kinds"]["water"]["on"] is False and out["kinds"]["water"]["time"] == "10:00"
    assert "bogus" not in out["kinds"]


# --- API --------------------------------------------------------------------------

def test_routes_are_token_gated():
    assert client.get("/reminders").status_code in (401, 403)
    assert client.post("/reminders/prefs", json={"enabled": False}).status_code in (401, 403)
    assert client.post("/reminders/test", json={"kind": "plan"}).status_code in (401, 403)
    assert client.post("/reminders/pause", json={"mode": "resume"}).status_code in (401, 403)


def test_overview_and_prefs_roundtrip():
    r = client.get("/reminders", headers=AUTH)
    assert r.status_code == 200
    d = r.json()
    assert [k["id"] for k in d["kinds"]] == rm.KIND_IDS
    assert "push" in d and "preview" in d["kinds"][0]
    r = client.post("/reminders/prefs", headers=AUTH,
                    json={"enabled": False, "kinds": {"meal_pm": {"time": "20:15"}}})
    assert r.status_code == 200
    p = client.get("/reminders", headers=AUTH).json()["prefs"]
    assert p["enabled"] is False and p["kinds"]["meal_pm"]["time"] == "20:15"


def test_prefs_reject_unknown_fields():
    assert client.post("/reminders/prefs", headers=AUTH, json={"evil": 1}).status_code == 422


def test_pause_and_resume():
    d = client.post("/reminders/pause", headers=AUTH, json={"mode": "until_tomorrow"}).json()
    assert d["prefs"]["paused_until"] > datetime.now().timestamp()
    d = client.post("/reminders/pause", headers=AUTH, json={"mode": "resume"}).json()
    assert d["prefs"]["paused_until"] == 0
    assert client.post("/reminders/pause", headers=AUTH, json={"mode": "x"}).status_code == 400


def test_test_send_uses_push_and_logs_as_test(monkeypatch):
    sent = []
    monkeypatch.setattr(server, "push_status", lambda: {"state": "ok", "devices": 1})
    monkeypatch.setattr(server, "_send_reminder_push", sent.append)
    r = client.post("/reminders/test", headers=AUTH, json={"kind": "weigh_in"})
    assert r.status_code == 200 and sent and sent[0]["kind"] == "weigh_in"
    assert rm.load_state()["log"][0]["test"] is True
    # a test never uses up the real daily reminder
    assert not rm.load_state()["days"][datetime.now().strftime("%Y-%m-%d")]["fired"]
    assert client.post("/reminders/test", headers=AUTH, json={"kind": "nope"}).status_code == 400


def test_test_send_without_devices_says_so(monkeypatch):
    monkeypatch.setattr(server, "push_status", lambda: {"state": "no_devices", "devices": 0})
    r = client.post("/reminders/test", headers=AUTH, json={"kind": "plan"})
    assert r.status_code == 409 and "device" in r.json()["detail"]


def test_panel_page_served():
    r = client.get("/notifications")
    assert r.status_code == 200 and "Reminders from Adam" in r.text


def test_reminder_push_payload(monkeypatch):
    captured = {}
    monkeypatch.setattr(server, "webpush", object())
    monkeypatch.setattr(server, "VAPID_PUBLIC_KEY", "k")
    monkeypatch.setattr(server, "VAPID_PRIVATE_PEM", Path(__file__))   # any existing file
    monkeypatch.setattr(server, "_load_subs", lambda: [{"endpoint": "x"}])
    monkeypatch.setattr(server, "_deliver_push", lambda payload, subs, **kw: captured.update(p=payload, **kw))
    server._send_reminder_push({"kind": "water", "title": "Water check, sir", "body": "b", "action": "health"})
    import json
    p = json.loads(captured["p"])
    assert p["kind"] == "reminder" and p["rid"] == "water" and p["action"] == "health"
    monkeypatch.setattr(server, "_load_subs", lambda: [])
    with pytest.raises(RuntimeError):
        server._send_reminder_push({"kind": "water"})


# --- rethink pass: spacing, "ever used" gates, plan template --------------------------

def test_one_banner_per_tick_soonest_window_first_then_spaced():
    f = dict(ALL_ACTIVE)
    sunday = lambda h, m: datetime(2026, 10, 11, h, m)
    got = []
    send = got.append
    no = lambda n: False
    # 19:35 Sunday: water (closes 20:30), dinner (22:00), bank (22:00) all due
    rm.tick(send, now=sunday(19, 35), facts=f, blocks=[])
    assert [m["kind"] for m in got] == ["water"]
    rm.tick(send, now=sunday(19, 45), facts=f, blocks=[])     # inside the 20-min gap
    assert len(got) == 1
    rm.tick(send, now=sunday(19, 56), facts=f, blocks=[])
    rm.tick(send, now=sunday(20, 17), facts=f, blocks=[])
    rm.tick(send, now=sunday(20, 38), facts=f, blocks=[])     # weigh-in is morning-only now
    assert [m["kind"] for m in got] == ["water", "meal_pm", "finance_csv"]


def test_test_send_does_not_delay_real_reminders(monkeypatch):
    rm.send_test("plan", lambda m: None, now=at(9, 0))
    got = []
    rm.tick(got.append, now=at(9, 1), facts=dict(ALL_ACTIVE, weighed_today=True), blocks=[])
    assert [m["kind"] for m in got] == ["plan"]


def test_plan_template_is_not_usage():
    tmpl = (REPO_ROOT / "brain" / "06_calendar" / "latest_calendar_packet.md").read_text("utf-8")
    assert not rm._plan_used(tmpl)
    assert rm._plan_used("_Rebuilt 2026-10-02 (Fri)._")


def test_gather_facts_counts_a_lapsed_tracker_as_used(tmp_path):
    import health_store as hs
    db = Path(config.HEALTH_DB)
    assert str(db).startswith(str(tmp_path))      # never the owner's live DB
    hs.close()
    hs.init(db)
    try:
        hs.add_weight("2026-07-29", 181.0)        # last weigh-in two months ago
        f = rm.gather_facts(at(8, 45))
        assert f["weight_active"] is True and f["weighed_today"] is False
        assert f["water_active"] is False and f["meal_active"] is False
        assert "weigh_in" in rm.evaluate(at(8, 45), P(), {}, f)
    finally:
        hs.close()
    vault = Path(config.VAULT_PATH) / "06_calendar"
    vault.mkdir(parents=True)
    (vault / "latest_calendar_packet.md").write_text("# Latest Calendar Packet\n\n## Date:\n", "utf-8")
    assert not rm.gather_facts(at(9, 30)).get("plan_active")
    (vault / "latest_calendar_packet.md").write_text("_Rebuilt 2026-10-02_\n", "utf-8")
    assert rm.gather_facts(at(9, 30)).get("plan_active") is True


# --- test-mode push guard ---------------------------------------------------------

def test_suite_never_reaches_a_real_push_service(monkeypatch):
    """The Operator tests' fake questions were landing on the owner's phone."""
    assert os.environ.get("ADAM_NO_PUSH") == "1"     # set by tests/conftest.py
    calls = []
    def real(**kw):
        calls.append(kw)
    monkeypatch.setattr(server, "webpush", real)
    monkeypatch.setattr(server, "_REAL_WEBPUSH", real)    # i.e. the genuine sender
    monkeypatch.setattr(server, "_record_push_health", lambda *a, **k: calls.append("health"))
    server._deliver_push("{}", [{"endpoint": "https://push.example/x"}])
    assert calls == []          # nothing sent, push health untouched


def test_a_fake_sender_still_runs_in_test_mode(monkeypatch):
    calls = []
    monkeypatch.setattr(server, "webpush", lambda **kw: calls.append(kw))
    monkeypatch.setattr(server, "_record_push_health", lambda *a, **k: None)
    monkeypatch.setattr(server, "_save_subs", lambda subs: None)
    server._deliver_push("{}", [{"endpoint": "https://push.example/x"}])
    assert len(calls) == 1


def test_reminder_loop_does_not_start_in_test_mode():
    import asyncio
    asyncio.run(asyncio.wait_for(rm.run_loop(lambda m: None), timeout=2))   # returns at once


# --- schedule-aware (reminder_schedule) -------------------------------------------------

import reminder_schedule as rs  # noqa: E402


def blk(s, e, label="Work", out=True, busy=False, source="routine"):
    return {"start": s, "end": e, "label": label, "out": out, "busy": busy, "source": source}


@pytest.mark.parametrize("title,loc,mins,expect", [
    # the owner's real calendar titles: at-home task blocks are neither
    ("Front yard: cat poop + start laundry", "", 15, (False, False)),
    ("Highlander — polish & wax", "", 120, (False, False)),
    ("Cadence 4Runner — head unit gain/power diagnosis", "", 90, (False, False)),
    ("184 Loan Call — First Tribal Lending", "", 30, (False, True)),
    ("Family meeting — Halloween (trail + lunch)", "", 45, (False, True)),
    ("Pool shift", "", 480, (True, False)),            # long out block: reachable
    ("Work", "", 540, (True, False)),
    ("Work on the Highlander", "", 120, (False, False)),
    ("Dentist", "", 60, (True, True)),                  # short + out: busy
    ("Dinner at Grandma's", "", 120, (True, True)),
    ("Haircut", "123 Main St", 30, (True, True)),
    ("Anything", "Home", 60, (False, False)),
])
def test_classify(title, loc, mins, expect):
    assert rs.classify(title, loc, mins) == expect


@pytest.mark.parametrize("text,expect", [
    ("- **2:30–2:45 PM** — Front yard: clean cat poop", (870, 885, "Front yard: clean cat poop")),
    ("- 11–1 PM | Drive to Phoenix | 30m | notes", (660, 780, "Drive to Phoenix")),
    ("- 14:30-15:00 | Dentist", (870, 900, "Dentist")),
    ("- 7:00 PM - 8:30 PM — Church", (1140, 1230, "Church")),
    ("- do 3-4 sets of squats", None),
])
def test_parse_range(text, expect):
    assert rs.parse_range(text) == expect


def test_plan_blocks_reads_only_todays_section():
    packet = """# Latest Calendar Packet
_Rebuilt 2026-10-02 (Fri)._

## Friday, Oct 2 — car day
- **9:00–11:00 AM** — Dentist downtown

## Monday, Oct 5 — workday
- **8:00–9:00 AM** — Gym
- **2:30–2:45 PM** — Front yard cleanup
- **6:00–8:00 PM** — Dinner at Grandma's
"""
    got = rs.plan_blocks(packet, datetime(2026, 10, 5, 7, 0))
    assert [(b["label"], b["out"], b["busy"]) for b in got] == [
        ("Gym", True, True), ("Dinner at Grandma's", True, True)]


def test_routine_blocks_by_weekday():
    routine = [{"label": "Work", "days": [0, 2], "start": "13:30", "end": "21:30"}]
    assert rs.routine_blocks(routine, 0)[0]["start"] == 810
    assert rs.routine_blocks(routine, 1) == []


def test_calendar_blocks_all_day_vacation_only():
    now = datetime(2026, 10, 5, 12).astimezone()
    evs = [{"title": "Fam meeting 8pm", "all_day": True},
           {"title": "Vacation — Rocky Point", "all_day": True}]
    got = rs.calendar_blocks(evs, now)
    assert len(got) == 1 and got[0]["start"] == 0 and got[0]["end"] == 1440


def test_weigh_in_waits_until_home_then_says_so():
    f = dict(ALL_ACTIVE)
    gym = [blk(420, 540, "Gym")]                # Mon 7–9 AM
    assert "weigh_in" not in rm.evaluate(at(8, 45), P(), {}, f, gym)    # still out
    assert "weigh_in" not in rm.evaluate(at(9, 10), P(), {}, f, gym)    # 10 min after: in the buffer
    assert "weigh_in" in rm.evaluate(at(9, 16), P(), {}, f, gym)        # home
    m = rm.build_message("weigh_in", f, P(), at(9, 16), gym)
    assert m["title"] == "You're home, sir. Weigh-in?"


def test_out_past_window_skips_weigh_in():
    # out all morning: the weigh-in is dropped, not carried into the evening
    shift = [blk(420, 690)]                     # 7:00–11:30 AM
    for h, m in ((10, 59), (11, 50), (21, 46)):
        assert "weigh_in" not in rm.evaluate(at(h, m), P(), {}, dict(ALL_ACTIVE), shift)


def test_water_and_meals_still_come_at_work():
    f = dict(ALL_ACTIVE)
    shift = [blk(690, 1170)]                    # Tue 11:30 AM–7:30 PM
    assert "water" in rm.evaluate(at(14), P(), {}, f, shift)
    assert "meal_am" in rm.evaluate(at(12), P(), {}, f, shift)


def test_busy_event_holds_everything_until_it_ends():
    f = dict(ALL_ACTIVE)
    call = [blk(840, 870, "Loan call", out=False, busy=True, source="calendar")]
    assert rm.evaluate(at(14, 10), P(), {}, f, call) == []
    assert "water" in rm.evaluate(at(14, 31), P(), {}, f, call)


def test_plan_around_off_restores_clock_only_behavior():
    f = dict(ALL_ACTIVE)
    gym = [blk(420, 540, "Gym")]
    assert "weigh_in" in rm.evaluate(at(8, 45), P(hold_for_calendar=False), {}, f, gym)


def test_plan_nudge_moves_before_an_early_departure():
    f = dict(ALL_ACTIVE)
    friday = [blk(480, 1020)]                   # Fri 8 AM–5 PM
    fri = lambda h, m: datetime(2026, 10, 9, h, m)
    assert "plan" not in rm.evaluate(fri(7, 15), P(), {}, f, friday)
    assert "plan" in rm.evaluate(fri(7, 30), P(), {}, f, friday)
    m = rm.build_message("plan", f, P(), fri(7, 30), friday)
    assert m["title"] == "Before Work, sir: what's the plan?" and "8:00 AM" in m["body"]
    # a later shift (1:30 PM) doesn't move the 9 AM nudge
    assert "plan" not in rm.evaluate(at(8, 30), P(), {}, f, [blk(810, 1290)])
    assert "plan" in rm.evaluate(at(9, 0), P(), {}, f, [blk(810, 1290)])


def test_bank_reminder_rolls_to_next_day_when_missed():
    f = dict(ALL_ACTIVE)
    sun_out = [blk(900, 1380, "Family trip")]
    assert "finance_csv" not in rm.evaluate(at(18, day=11), P(), {}, f, sun_out)   # out all Sunday evening
    assert "finance_csv" in rm.evaluate(at(17, day=12), P(), {}, f, [])            # Monday: carried over
    sent_sunday = {"finance_fired_ts": at(17, day=11).timestamp()}
    assert "finance_csv" not in rm.evaluate(at(17, day=12), P(), sent_sunday, f, [])


def test_routine_prefs_validate():
    out = rm.save_prefs({"routine": [
        {"label": "Work", "days": [0, 2, 9], "start": "13:30", "end": "21:30"},
        {"label": "bad", "days": [1], "start": "18:00", "end": "09:00"},
        {"label": "", "days": [], "start": "08:00", "end": "09:00"},
    ]})
    assert out["routine"] == [{"label": "Work", "days": [0, 2], "start": "13:30", "end": "21:30"}]


def test_overview_explains_waiting(monkeypatch):
    rm.save_prefs({"routine": [{"label": "Work", "days": list(range(7)), "start": "00:00", "end": "23:59"}]})
    d = client.get("/reminders", headers=AUTH).json()
    assert d["today"] and d["today"][0]["label"] == "Work" and d["today"][0]["source"] == "routine"
    w = next(k for k in d["kinds"] if k["id"] == "weigh_in")
    assert w["needs_home"] and "until you're home" in w["note"]


def test_routine_over_the_api():
    r = client.post("/reminders/prefs", headers=AUTH, json={"routine": [
        {"label": "Work", "days": [4], "start": "08:00", "end": "17:00"}]})
    assert r.status_code == 200 and r.json()["prefs"]["routine"][0]["days"] == [4]


# --- fixes 2026-10-04: word-boundary labels, nonzero push TTL -----------------------------

@pytest.mark.parametrize("text,expect", [
    ("Church, White Mountain Bible Church (fixed time, 10:30)", "Church, White Mountain Bible Church…"),
    ("Pick up Cadence from practice at the high school gym", "Pick up Cadence from practice at the…"),
    ("Short one", "Short one"),
])
def test_short_label_cuts_at_a_word(text, expect):
    got = rs.short_label(text)
    assert got == expect and len(got) <= rs.LABEL_MAX


def test_plan_label_never_ends_mid_word():
    r = rs.parse_range("- **10:30 AM–12:00 PM** — Church, White Mountain Bible Church (fixed time, arrive early)")
    assert r == (630, 720, "Church, White Mountain Bible Church…")


@pytest.mark.parametrize("sender,args,ttl_name", [
    ("_send_push", ("done", "sess", 1), "PUSH_TTL_REPLY"),
    ("_send_ask_push", ("Red or blue?", "chat-1"), "PUSH_TTL_ASK"),
    ("_send_reminder_push", ({"kind": "water", "title": "t", "body": "b", "action": "health"},), "PUSH_TTL_REMINDER"),
])
def test_every_push_carries_a_nonzero_ttl(monkeypatch, sender, args, ttl_name):
    """Windows' push service rejects TTL 0 ("Ttl value conflicts with
    X-WNS-Cache-Policy") — no Adam push had ever reached a Windows browser."""
    seen = []
    monkeypatch.setattr(server, "webpush", lambda **kw: seen.append(kw))
    monkeypatch.setattr(server, "VAPID_PUBLIC_KEY", "k")
    monkeypatch.setattr(server, "VAPID_PRIVATE_PEM", Path(__file__))
    monkeypatch.setattr(server, "_load_subs", lambda: [{"endpoint": "https://wns2.notify.windows.com/x"}])
    monkeypatch.setattr(server, "_record_push_health", lambda *a, **k: None)
    getattr(server, sender)(*args)
    assert seen and seen[0]["ttl"] == getattr(server, ttl_name) and seen[0]["ttl"] >= 60
