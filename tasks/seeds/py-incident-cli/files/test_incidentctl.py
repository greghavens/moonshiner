"""Acceptance tests for incidentctl. Run: python3 test_incidentctl.py

Contract summary (full spec in the ticket):
- Entry point: incidents.cli.main(argv, *, now, state_path) -> int exit code.
  now is 'YYYY-MM-DDTHH:MM' logical time; nothing reads the wall clock.
- State is one JSON document at state_path; missing file = fresh state;
  unparseable file = exit 2. Mutations persist only on success (exit 0).
- Subcommands: rotation add, override add, oncall, handoffs, policy add,
  route add, ingest, ack, resolve, tick, list, timeline.
- Errors go to stderr as 'error: ...'; exit 1 = domain error, 2 = usage/state.
All test data lives under ./incidentctl_test_data (created and removed here).
"""
import contextlib
import io
import json
import os
import shutil

DATA_DIR = os.path.join(".", "incidentctl_test_data")
STATE = os.path.join(DATA_DIR, "state.json")
STATE2 = os.path.join(DATA_DIR, "other-state.json")


def run(args, now, state=STATE):
    from incidents.cli import main
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(args, now=now, state_path=state)
    assert isinstance(code, int), "main must return an int exit code"
    return code, out.getvalue(), err.getvalue()


def ok(args, now, state=STATE):
    code, out, err = run(args, now, state)
    assert code == 0, (args, code, out, err)
    assert err == "", (args, err)
    return out


def write_lines(name, lines):
    path = os.path.join(DATA_DIR, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path


def main():
    if os.path.exists(DATA_DIR):
        shutil.rmtree(DATA_DIR)
    os.makedirs(DATA_DIR)
    try:
        # ---- rotations: shift math on logical time -------------------------
        out = ok(["rotation", "add", "primary", "--people", "alice,bob,carol",
                  "--start", "2026-03-02T08:00", "--shift-hours", "8"],
                 now="2026-03-01T00:00")
        assert out == "added rotation primary\n", out

        code, out, err = run(["rotation", "add", "primary", "--people", "x",
                              "--start", "2026-03-02T08:00", "--shift-hours", "8"],
                             now="2026-03-01T00:00")
        assert code == 1 and err == "error: rotation primary already exists\n", (code, err)

        for at, want in [("2026-03-02T08:00", "alice"),   # first shift starts
                         ("2026-03-02T15:59", "alice"),
                         ("2026-03-02T16:00", "bob"),     # boundary -> new shift
                         ("2026-03-02T23:59", "bob"),
                         ("2026-03-03T00:00", "carol"),
                         ("2026-03-03T08:00", "alice"),   # wraps around
                         ("2026-03-04T09:30", "alice")]:
            out = ok(["oncall", "primary", "--at", at], now="2026-03-01T00:00")
            assert out == want + "\n", (at, out, want)

        # before the rotation starts nobody is on call
        out = ok(["oncall", "primary", "--at", "2026-03-02T07:59"], now="2026-03-01T00:00")
        assert out == "nobody\n", out

        # --at defaults to the invocation's now
        out = ok(["oncall", "primary"], now="2026-03-02T16:30")
        assert out == "bob\n", out

        code, out, err = run(["oncall", "nightshift"], now="2026-03-02T16:30")
        assert code == 1 and err == "error: unknown rotation: nightshift\n", (code, err)

        # ---- overrides: half-open window, last added wins ------------------
        out = ok(["override", "add", "primary", "dana",
                  "--from", "2026-03-02T12:00", "--until", "2026-03-02T13:00"],
                 now="2026-03-01T00:00")
        assert out == "added override on primary\n", out
        ok(["override", "add", "primary", "erin",
            "--from", "2026-03-02T12:30", "--until", "2026-03-02T12:45"],
           now="2026-03-01T00:00")

        for at, want in [("2026-03-02T11:59", "alice"),
                         ("2026-03-02T12:00", "dana"),    # from inclusive
                         ("2026-03-02T12:29", "dana"),
                         ("2026-03-02T12:40", "erin"),    # overlapping: last added wins
                         ("2026-03-02T12:50", "dana"),
                         ("2026-03-02T12:59", "dana"),
                         ("2026-03-02T13:00", "alice")]:  # until exclusive
            out = ok(["oncall", "primary", "--at", at], now="2026-03-01T00:00")
            assert out == want + "\n", (at, out, want)

        code, _, err = run(["override", "add", "nightshift", "pat",
                            "--from", "2026-03-02T12:00", "--until", "2026-03-02T13:00"],
                           now="2026-03-01T00:00")
        assert code == 1 and err == "error: unknown rotation: nightshift\n", (code, err)
        code, _, err = run(["override", "add", "primary", "pat",
                            "--from", "2026-03-02T13:00", "--until", "2026-03-02T13:00"],
                           now="2026-03-01T00:00")
        assert code == 2 and err.startswith("error:"), (code, err)

        # ---- handoffs: base schedule only, strictly after --at --------------
        out = ok(["handoffs", "primary", "--count", "3", "--at", "2026-03-02T09:00"],
                 now="2026-03-01T00:00")
        assert out == ("2026-03-02T16:00 bob\n"
                       "2026-03-03T00:00 carol\n"
                       "2026-03-03T08:00 alice\n"), out
        # before the start, the first handoff is the start itself
        out = ok(["handoffs", "primary", "--count", "1", "--at", "2026-03-02T07:00"],
                 now="2026-03-01T00:00")
        assert out == "2026-03-02T08:00 alice\n", out
        # a boundary is not strictly after itself
        out = ok(["handoffs", "primary", "--count", "1", "--at", "2026-03-02T16:00"],
                 now="2026-03-01T00:00")
        assert out == "2026-03-03T00:00 carol\n", out
        code, _, err = run(["handoffs", "primary", "--count", "0"], now="2026-03-01T00:00")
        assert code == 2 and err.startswith("error:"), (code, err)

        # ---- escalation policies + routes -----------------------------------
        out = ok(["policy", "add", "standard",
                  "--step", "rotation:primary@30",
                  "--step", "user:erin@45",
                  "--step", "rotation:primary"], now="2026-03-01T00:00")
        assert out == "added policy standard\n", out

        code, _, err = run(["policy", "add", "night", "--step", "rotation:nightshift@5",
                            "--step", "user:pat"], now="2026-03-01T00:00")
        assert code == 1 and err == "error: unknown rotation: nightshift\n", (code, err)
        code, _, err = run(["policy", "add", "p2", "--step", "erin@30",
                            "--step", "user:pat"], now="2026-03-01T00:00")
        assert code == 2 and err.startswith("error:"), (code, err)
        code, _, err = run(["policy", "add", "p3", "--step", "user:erin@x",
                            "--step", "user:pat"], now="2026-03-01T00:00")
        assert code == 2 and err.startswith("error:"), (code, err)
        # every step except the last needs @minutes
        code, _, err = run(["policy", "add", "p4", "--step", "user:erin",
                            "--step", "user:pat"], now="2026-03-01T00:00")
        assert code == 2 and err.startswith("error:"), (code, err)
        code, _, err = run(["policy", "add", "standard", "--step", "user:erin"],
                           now="2026-03-01T00:00")
        assert code == 1 and err == "error: policy standard already exists\n", (code, err)

        out = ok(["route", "add", "web", "standard"], now="2026-03-01T00:00")
        assert out == "routed web to standard\n", out
        ok(["route", "add", "web", "standard"], now="2026-03-01T00:00")  # re-route is fine
        code, _, err = run(["route", "add", "batch", "nosuch"], now="2026-03-01T00:00")
        assert code == 1 and err == "error: unknown policy: nosuch\n", (code, err)

        # ---- ingest: schema validation + fingerprint grouping ---------------
        # cache/* is deliberately unrouted: grouping without any paging.
        batch = write_lines("batch1.jsonl", [
            '{"at":"2026-03-02T09:00","service":"cache","check":"evictions","severity":"low","message":"evictions spiking"}',
            '{"at":"2026-03-02T09:10","service":"cache","check":"evictions","severity":"high","message":"evictions worse"}',
            '{"at": nope}',
            '{"at":"2026-03-02T09:25","service":"cache","check":"evictions","severity":"low","message":"still going"}',
            '{"at":"2026-03-02T09:41","service":"cache","check":"evictions","severity":"low","message":"new burst"}',
            '{"service":"cache","check":"evictions","severity":"low","message":"no timestamp"}',
            '{"at":"2026-03-02T09:45","service":"cache","check":"hitrate","severity":"urgent","message":"x"}',
            '{"at":"2026-03-02T09:50","service":"cache","check":"evictions","severity":"high","message":42}',
            '   ',
            '{"at":"2026-03-02T09:12","service":"cache","check":"evictions","severity":"low","message":"late arrival"}',
        ])
        code, out, err = run(["ingest", batch], now="2026-03-02T09:55")
        assert code == 0, (code, err)
        assert out == "ingested 5 alert(s): 2 new, 3 grouped, 4 rejected\n", out
        assert err == ("line 3: invalid json\n"
                       "line 6: missing field 'at'\n"
                       "line 7: bad severity 'urgent'\n"
                       "line 8: wrong type for field 'message'\n"), err

        # gap of exactly the window groups (09:10 -> 09:25); 16 minutes opens
        # a new incident; the late 09:12 alert joins the most recently active
        # open incident and never moves last-seen backwards.
        out = ok(["list"], now="2026-03-02T09:55")
        assert out == ("INC-1 open high cache/evictions alerts=3 last=2026-03-02T09:25\n"
                       "INC-2 open low cache/evictions alerts=2 last=2026-03-02T09:41\n"), out

        # per-invocation window override
        batch = write_lines("batch2.jsonl", [
            '{"at":"2026-03-02T10:00","service":"cache","check":"hitrate","severity":"low","message":"dip"}',
            '{"at":"2026-03-02T10:20","service":"cache","check":"hitrate","severity":"low","message":"dip again"}',
        ])
        out = ok(["ingest", batch, "--window", "25"], now="2026-03-02T10:30")
        assert out == "ingested 2 alert(s): 1 new, 1 grouped, 0 rejected\n", out

        # more reject reasons
        batch = write_lines("batch3.jsonl", [
            '[5]',
            '{"at":"2026-03-02","service":"cache","check":"evictions","severity":"low","message":"m"}',
            '{"at":"2026-03-02T11:00","service":"","check":"evictions","severity":"low","message":"m"}',
        ])
        code, out, err = run(["ingest", batch], now="2026-03-02T11:05")
        assert code == 0
        assert out == "ingested 0 alert(s): 0 new, 0 grouped, 3 rejected\n", out
        assert err == ("line 1: not an object\n"
                       "line 2: bad timestamp '2026-03-02'\n"
                       "line 3: empty field 'service'\n"), err

        # strict mode: first malformed line aborts, nothing is ingested
        batch = write_lines("strict.jsonl", [
            '{"at":"2026-03-02T11:10","service":"cache","check":"hitrate","severity":"low","message":"pre"}',
            '{"at":"2026-03-02T11:11","service":"cache","check":"hitrate","message":"no sev"}',
        ])
        code, out, err = run(["ingest", batch, "--strict"], now="2026-03-02T11:15")
        assert code == 1 and out == "", (code, out)
        assert err == "line 2: missing field 'severity'\n", err
        out = ok(["list"], now="2026-03-02T11:15")   # INC-3 untouched by the abort
        assert out == ("INC-1 open high cache/evictions alerts=3 last=2026-03-02T09:25\n"
                       "INC-2 open low cache/evictions alerts=2 last=2026-03-02T09:41\n"
                       "INC-3 open low cache/hitrate alerts=2 last=2026-03-02T10:20\n"), out

        code, _, err = run(["ingest", os.path.join(DATA_DIR, "missing.jsonl")],
                           now="2026-03-02T11:20")
        assert code == 2, code
        assert err == "error: alerts file not found: %s\n" % os.path.join(DATA_DIR, "missing.jsonl"), err

        empty = write_lines("empty.jsonl", ["", "   "])
        out = ok(["ingest", empty], now="2026-03-02T11:25")
        assert out == "ingested 0 alert(s): 0 new, 0 grouped, 0 rejected\n", out

        # ---- ack bookkeeping on an unrouted incident -------------------------
        out = ok(["ack", "INC-1", "--by", "casey"], now="2026-03-02T10:00")
        assert out == "acked INC-1\n", out
        code, _, err = run(["ack", "INC-1", "--by", "casey"], now="2026-03-02T10:05")
        assert code == 1 and err == "error: INC-1 already acked\n", (code, err)
        code, _, err = run(["ack", "INC-99", "--by", "casey"], now="2026-03-02T10:05")
        assert code == 1 and err == "error: unknown incident: INC-99\n", (code, err)

        # ---- escalation ladder: level 0 at open, tick materializes the rest --
        batch = write_lines("web1.jsonl", [
            '{"at":"2026-03-02T15:45","service":"web","check":"latency","severity":"critical","message":"p99 above budget"}',
        ])
        out = ok(["ingest", batch], now="2026-03-02T15:46")
        assert out == "ingested 1 alert(s): 1 new, 0 grouped, 0 rejected\n", out

        batch = write_lines("web2.jsonl", [
            '{"at":"2026-03-02T15:55","service":"web","check":"latency","severity":"high","message":"still bad"}',
        ])
        out = ok(["ingest", batch], now="2026-03-02T15:56")
        assert out == "ingested 1 alert(s): 0 new, 1 grouped, 0 rejected\n", out

        out = ok(["tick"], now="2026-03-02T16:00")
        assert out == "no pages due\n", out
        # level 1 (open 15:45 + 30m = 16:15) fires with its due time
        out = ok(["tick"], now="2026-03-02T16:20")
        assert out == "page INC-4 level 1 -> erin (2026-03-02T16:15)\n", out
        out = ok(["tick"], now="2026-03-02T16:20")   # tick is idempotent
        assert out == "no pages due\n", out

        ok(["ack", "INC-4", "--by", "bob"], now="2026-03-02T16:50")
        out = ok(["list"], now="2026-03-02T16:55")
        assert out.splitlines()[3] == "INC-4 acked critical web/latency alerts=2 last=2026-03-02T15:55", out
        # level 2 would be due 17:00, after the ack -> suppressed forever
        out = ok(["tick"], now="2026-03-02T18:00")
        assert out == "no pages due\n", out

        out = ok(["resolve", "INC-4"], now="2026-03-02T19:00")
        assert out == "resolved INC-4\n", out
        code, _, err = run(["resolve", "INC-4"], now="2026-03-02T19:05")
        assert code == 1 and err == "error: INC-4 is resolved\n", (code, err)
        code, _, err = run(["ack", "INC-4", "--by", "bob"], now="2026-03-02T19:05")
        assert code == 1 and err == "error: INC-4 is resolved\n", (code, err)

        # resolved incidents leave the default listing but not --all
        out = ok(["list"], now="2026-03-02T19:10")
        assert "INC-4" not in out, out
        out = ok(["list", "--all"], now="2026-03-02T19:10")
        assert out.splitlines()[3] == "INC-4 resolved critical web/latency alerts=2 last=2026-03-02T15:55", out

        # a page already due when the ack lands still fires on a later tick
        batch = write_lines("web3.jsonl", [
            '{"at":"2026-03-02T20:00","service":"web","check":"latency","severity":"critical","message":"query latency red"}',
        ])
        ok(["ingest", batch], now="2026-03-02T20:01")          # INC-5, L0 -> bob
        ok(["ack", "INC-5", "--by", "bob"], now="2026-03-02T20:40")
        out = ok(["tick"], now="2026-03-02T22:00")
        assert out == "page INC-5 level 1 -> erin (2026-03-02T20:30)\n", out

        # one late tick can fire several levels, in due order, and a
        # rotation step resolves against the rotation at the DUE time
        batch = write_lines("web4.jsonl", [
            '{"at":"2026-03-02T23:50","service":"web","check":"latency","severity":"high","message":"latency again"}',
        ])
        ok(["ingest", batch], now="2026-03-02T23:51")          # INC-6, L0 -> bob
        out = ok(["tick"], now="2026-03-03T02:00")
        assert out == ("page INC-6 level 1 -> erin (2026-03-03T00:20)\n"
                       "page INC-6 level 2 -> carol (2026-03-03T01:05)\n"), out
        out = ok(["tick"], now="2026-03-03T09:00")             # ladder exhausted
        assert out == "no pages due\n", out

        # rotation step before the rotation exists on the calendar -> nobody
        ok(["rotation", "add", "late", "--people", "pat",
            "--start", "2026-06-01T00:00", "--shift-hours", "24"], now="2026-03-02T21:00")
        ok(["policy", "add", "night", "--step", "rotation:late"], now="2026-03-02T21:00")
        ok(["route", "add", "batch", "night"], now="2026-03-02T21:00")
        batch = write_lines("batch-q.jsonl", [
            '{"at":"2026-03-02T21:00","service":"batch","check":"queue","severity":"low","message":"queue deep"}',
        ])
        ok(["ingest", batch], now="2026-03-02T21:01")          # INC-7
        out = ok(["timeline", "INC-7"], now="2026-03-02T21:05")
        assert out == ("timeline INC-7 (batch/queue)\n"
                       "2026-03-02T21:00 alert low queue deep\n"
                       "2026-03-02T21:00 page level=0 nobody\n"), out

        # ---- timelines: merged, ordered, text + json -------------------------
        out = ok(["timeline", "INC-4"], now="2026-03-03T09:00")
        assert out == ("timeline INC-4 (web/latency)\n"
                       "2026-03-02T15:45 alert critical p99 above budget\n"
                       "2026-03-02T15:45 page level=0 alice\n"
                       "2026-03-02T15:55 alert high still bad\n"
                       "2026-03-02T16:15 page level=1 erin\n"
                       "2026-03-02T16:50 ack bob\n"
                       "2026-03-02T19:00 resolve\n"), out

        out = ok(["timeline", "INC-5", "--format", "json"], now="2026-03-03T09:00")
        got = json.loads(out)
        assert got == {
            "incident": "INC-5",
            "fingerprint": "web/latency",
            "status": "acked",
            "events": [
                {"at": "2026-03-02T20:00", "kind": "alert",
                 "severity": "critical", "message": "query latency red"},
                {"at": "2026-03-02T20:00", "kind": "page", "level": 0, "target": "bob"},
                {"at": "2026-03-02T20:30", "kind": "page", "level": 1, "target": "erin"},
                {"at": "2026-03-02T20:40", "kind": "ack", "by": "bob"},
            ],
        }, got

        # unrouted incidents have alert/ack events only
        out = ok(["timeline", "INC-1", "--format", "json"], now="2026-03-03T09:00")
        got = json.loads(out)
        assert got["status"] == "acked" and got["fingerprint"] == "cache/evictions", got
        assert [e["kind"] for e in got["events"]] == ["alert", "alert", "alert", "ack"], got
        assert [e["at"] for e in got["events"]] == [
            "2026-03-02T09:00", "2026-03-02T09:10", "2026-03-02T09:25",
            "2026-03-02T10:00"], got

        code, _, err = run(["timeline", "INC-99"], now="2026-03-03T09:00")
        assert code == 1 and err == "error: unknown incident: INC-99\n", (code, err)

        # ---- persistence + isolation + failure modes -------------------------
        with open(STATE, encoding="utf-8") as f:
            state_doc = json.load(f)          # the state file is real JSON
        assert isinstance(state_doc, dict)

        out = ok(["list"], now="2026-03-03T09:00", state=STATE2)
        assert out == "no incidents\n", out   # fresh state path = fresh world
        code, _, err = run(["oncall", "primary"], now="2026-03-03T09:00", state=STATE2)
        assert code == 1 and err == "error: unknown rotation: primary\n", (code, err)

        with open(STATE2, "w", encoding="utf-8") as f:
            f.write("{{{ not json")
        code, _, err = run(["list"], now="2026-03-03T09:00", state=STATE2)
        assert code == 2 and err == "error: state file is corrupt: %s\n" % STATE2, (code, err)

        code, _, err = run(["frobnicate"], now="2026-03-03T09:00")
        assert code == 2 and err == "error: unknown command: frobnicate\n", (code, err)
        code, _, err = run(["rotation", "add", "solo"], now="2026-03-03T09:00")
        assert code == 2 and err.startswith("error:"), (code, err)
        code, _, err = run(["oncall", "primary", "--at", "yesterday"], now="2026-03-03T09:00")
        assert code == 2 and err.startswith("error:"), (code, err)

        # the original state survived everything above
        out = ok(["list", "--all"], now="2026-03-03T09:00")
        assert len(out.splitlines()) == 7, out
    finally:
        shutil.rmtree(DATA_DIR, ignore_errors=True)
    print("all incidentctl checks passed")


if __name__ == "__main__":
    main()
