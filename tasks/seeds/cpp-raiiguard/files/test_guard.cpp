/* Acceptance tests for the bench action-journal guards (guard.hpp/guard.cpp).
 * Build and run with `make test`.
 *
 * Contract pinned here:
 *   - every acquisition appends exactly one journal line at construction,
 *     every release appends exactly one line, and nothing else ever writes
 *     to the journal (moves are silent);
 *   - releases happen in reverse acquisition order at scope exit;
 *   - guards are move-only: ownership transfers, the source goes inactive,
 *     and each resource is released exactly once no matter how often the
 *     guard changed hands.
 */
#include "mintest.h"

#include "guard.hpp"

#include <algorithm>
#include <string>
#include <utility>
#include <vector>

/* CHECK_EQ_STR keeps only the char pointer, so bind the composed journal
 * string to a local first — a temporary's c_str() would dangle. */
#define CHECK_JOURNAL(got_str, want, msg) do {                              \
        const std::string mt_line = (got_str);                              \
        CHECK_EQ_STR(mt_line.c_str(), (want), (msg));                       \
    } while (0)

static std::string joined(const ActionLog &log, std::size_t from = 0) {
    std::string out;
    for (std::size_t i = from; i < log.size(); i++) {
        if (i > from)
            out += "|";
        out += log[i];
    }
    return out;
}

static long count_of(const ActionLog &log, const std::string &line) {
    return std::count(log.begin(), log.end(), line);
}

TEST(open_logs_immediately_and_close_on_scope_exit) {
    ActionLog log;
    {
        FileGuard f(log, "cal.tbl");
        CHECK(f.active(), "guard is active after acquiring");
        CHECK_EQ_STR(f.path().c_str(), "cal.tbl", "path reads back");
        CHECK_JOURNAL(joined(log), "open cal.tbl",
                     "open recorded at construction, nothing else");
    }
    CHECK_JOURNAL(joined(log), "open cal.tbl|close cal.tbl",
                 "close recorded exactly once at scope exit");
}

TEST(default_constructed_guard_is_inert) {
    ActionLog log;
    {
        FileGuard f;
        CHECK(!f.active(), "default guard is inactive");
        CHECK_EQ_STR(f.path().c_str(), "", "inactive guard has empty path");
        LockGuard l;
        CHECK(!l.active(), "default lock is inactive");
        CHECK_EQ_STR(l.name().c_str(), "", "inactive lock has empty name");
    }
    CHECK_EQ_INT(log.size(), 0, "inert guards never touch the journal");
}

TEST(scope_exit_releases_in_reverse_acquisition_order) {
    ActionLog log;
    {
        LockGuard l(log, "bench-4");
        FileGuard f(log, "bench-4/run.bin");
        CHECK_EQ_INT(log.size(), 2, "two acquisitions, two lines");
    }
    CHECK_JOURNAL(joined(log),
                 "lock bench-4|open bench-4/run.bin|"
                 "close bench-4/run.bin|unlock bench-4",
                 "file closes before the lock releases");
}

TEST(move_construction_transfers_ownership_silently) {
    ActionLog log;
    {
        FileGuard a(log, "probe.log");
        FileGuard b(std::move(a));
        CHECK(!a.active(), "moved-from guard is inactive");
        CHECK_EQ_STR(a.path().c_str(), "", "moved-from guard has empty path");
        CHECK(b.active(), "target owns the file");
        CHECK_EQ_STR(b.path().c_str(), "probe.log", "target reports the path");
        CHECK_EQ_INT(log.size(), 1, "moving writes nothing to the journal");
    }
    CHECK_JOURNAL(joined(log), "open probe.log|close probe.log",
                 "exactly one close despite the transfer");
}

static FileGuard make_run_file(ActionLog &log, int run) {
    FileGuard f(log, "run-" + std::to_string(run) + ".bin");
    return f;
}

TEST(guard_returned_from_a_factory_stays_owned_once) {
    ActionLog log;
    {
        FileGuard f = make_run_file(log, 7);
        CHECK(f.active(), "factory result is active");
        CHECK_EQ_STR(f.path().c_str(), "run-7.bin", "factory path survives");
        CHECK_EQ_INT(count_of(log, "open run-7.bin"), 1, "one open only");
        CHECK_EQ_INT(count_of(log, "close run-7.bin"), 0, "no close yet");
    }
    CHECK_EQ_INT(count_of(log, "close run-7.bin"), 1, "one close at the end");
}

TEST(move_assignment_releases_the_old_resource_first) {
    ActionLog log;
    {
        FileGuard a(log, "one.log");
        FileGuard b(log, "two.log");
        a = std::move(b);
        CHECK_JOURNAL(joined(log), "open one.log|open two.log|close one.log",
                     "assignment closes the target's old file immediately");
        CHECK(a.active(), "target now owns the source's file");
        CHECK_EQ_STR(a.path().c_str(), "two.log", "target reports the new path");
        CHECK(!b.active(), "source is inactive after assignment");
    }
    CHECK_JOURNAL(joined(log, 3), "close two.log",
                 "only the surviving file closes at scope exit");
}

TEST(move_assignment_into_an_inactive_guard_adds_no_close) {
    ActionLog log;
    {
        FileGuard a;
        FileGuard b(log, "late.bin");
        a = std::move(b);
        CHECK_EQ_INT(log.size(), 1, "adopting into an empty guard is silent");
        CHECK(a.active(), "empty guard adopted the file");
    }
    CHECK_JOURNAL(joined(log), "open late.bin|close late.bin",
                 "still exactly one close");
}

TEST(move_assignment_from_an_inactive_guard_releases_the_target) {
    ActionLog log;
    {
        FileGuard a(log, "old.bin");
        FileGuard b;
        a = std::move(b);
        CHECK(!a.active(), "target went inactive");
        CHECK_JOURNAL(joined(log), "open old.bin|close old.bin",
                     "old file closed at assignment");
    }
    CHECK_EQ_INT(log.size(), 2, "nothing further at scope exit");
}

TEST(self_move_is_a_harmless_no_op) {
    ActionLog log;
    {
        FileGuard a(log, "keep.dat");
        FileGuard *alias = &a; /* keep the self-move honest under -Werror */
        a = std::move(*alias);
        CHECK(a.active(), "self-move leaves the guard active");
        CHECK_EQ_STR(a.path().c_str(), "keep.dat", "path survives self-move");
        CHECK_EQ_INT(log.size(), 1, "self-move writes nothing");
    }
    CHECK_JOURNAL(joined(log), "open keep.dat|close keep.dat",
                 "exactly one close after a self-move");
}

TEST(release_is_early_idempotent_and_final) {
    ActionLog log;
    {
        FileGuard f(log, "tmp.swp");
        f.release();
        CHECK(!f.active(), "released guard is inactive");
        CHECK_EQ_STR(f.path().c_str(), "", "released guard has empty path");
        CHECK_JOURNAL(joined(log), "open tmp.swp|close tmp.swp",
                     "release closes right away");
        f.release();
        CHECK_EQ_INT(log.size(), 2, "second release is a no-op");
    }
    CHECK_EQ_INT(log.size(), 2, "destructor adds nothing after release");
}

TEST(moved_from_guard_can_be_reused) {
    ActionLog log;
    {
        FileGuard a(log, "first.bin");
        FileGuard b(std::move(a));
        a = FileGuard(log, "second.bin");
        CHECK(a.active(), "moved-from guard accepts a new file");
        CHECK_EQ_STR(a.path().c_str(), "second.bin", "new path reads back");
        CHECK(b.active(), "original transfer unaffected");
    }
    CHECK_EQ_INT(count_of(log, "close first.bin"), 1, "first closed once");
    CHECK_EQ_INT(count_of(log, "close second.bin"), 1, "second closed once");
    CHECK_EQ_INT(log.size(), 4, "two opens and two closes;"
                                " the temporary adds no extra lines");
}

TEST(guards_survive_vector_reallocation) {
    ActionLog log;
    {
        std::vector<FileGuard> open_files;
        open_files.reserve(1); /* force reallocations as we push */
        open_files.push_back(FileGuard(log, "seg-0.dat"));
        open_files.push_back(FileGuard(log, "seg-1.dat"));
        open_files.push_back(FileGuard(log, "seg-2.dat"));
        CHECK_EQ_INT(log.size(), 3, "three opens, moves stay silent");
        CHECK_EQ_INT(count_of(log, "open seg-0.dat"), 1, "seg-0 opened once");
        CHECK_EQ_INT(count_of(log, "open seg-1.dat"), 1, "seg-1 opened once");
        CHECK_EQ_INT(count_of(log, "open seg-2.dat"), 1, "seg-2 opened once");
        open_files.clear();
        CHECK_EQ_INT(count_of(log, "close seg-0.dat"), 1, "seg-0 closed once");
        CHECK_EQ_INT(count_of(log, "close seg-1.dat"), 1, "seg-1 closed once");
        CHECK_EQ_INT(count_of(log, "close seg-2.dat"), 1, "seg-2 closed once");
        CHECK_EQ_INT(log.size(), 6, "no stray journal lines from growth");
    }
}

TEST(lock_guard_mirrors_the_file_contract) {
    ActionLog log;
    {
        LockGuard l(log, "stage-2");
        CHECK(l.active(), "lock active after acquiring");
        CHECK_EQ_STR(l.name().c_str(), "stage-2", "name reads back");
        LockGuard m(std::move(l));
        CHECK(!l.active(), "moved-from lock inactive");
        CHECK(m.active(), "target holds the lock");
        m.release();
        CHECK_JOURNAL(joined(log), "lock stage-2|unlock stage-2",
                     "one lock, one unlock, silent move");
        m.release();
        CHECK_EQ_INT(log.size(), 2, "repeat release is a no-op");
    }
    CHECK_EQ_INT(log.size(), 2, "destructor adds nothing after release");
}

TEST(session_acquires_and_releases_as_a_unit) {
    ActionLog log;
    {
        StationSession s(log, "bench-9", "bench-9/cal.tbl");
        CHECK(s.active(), "session active after construction");
        CHECK_EQ_STR(s.station().c_str(), "bench-9", "station reads back");
        CHECK_EQ_STR(s.file().c_str(), "bench-9/cal.tbl", "file reads back");
        CHECK_JOURNAL(joined(log), "lock bench-9|open bench-9/cal.tbl",
                     "lock first, then open");
    }
    CHECK_JOURNAL(joined(log),
                 "lock bench-9|open bench-9/cal.tbl|"
                 "close bench-9/cal.tbl|unlock bench-9",
                 "session releases file first, lock last");
}

TEST(session_release_is_ordered_and_idempotent) {
    ActionLog log;
    {
        StationSession s(log, "bench-1", "bench-1/a.bin");
        s.release();
        CHECK(!s.active(), "released session inactive");
        CHECK_JOURNAL(joined(log, 2),
                     "close bench-1/a.bin|unlock bench-1",
                     "early release keeps close-before-unlock order");
        s.release();
        CHECK_EQ_INT(log.size(), 4, "second release is a no-op");
    }
    CHECK_EQ_INT(log.size(), 4, "destructor adds nothing after release");
}

TEST(session_move_transfers_the_whole_unit) {
    ActionLog log;
    {
        StationSession a(log, "bench-3", "bench-3/r.bin");
        StationSession b(std::move(a));
        CHECK(!a.active(), "moved-from session inactive");
        CHECK(b.active(), "target session active");
        CHECK_EQ_STR(b.station().c_str(), "bench-3", "station transferred");
        CHECK_EQ_STR(b.file().c_str(), "bench-3/r.bin", "file transferred");
        CHECK_EQ_INT(log.size(), 2, "moving a session is silent");
    }
    CHECK_EQ_INT(count_of(log, "close bench-3/r.bin"), 1, "file closed once");
    CHECK_EQ_INT(count_of(log, "unlock bench-3"), 1, "station unlocked once");
    CHECK_EQ_INT(log.size(), 4, "four lines total for one session");
}

TEST(interleaved_sessions_keep_their_own_order) {
    ActionLog log;
    {
        StationSession outer(log, "bench-5", "bench-5/base.tbl");
        {
            StationSession inner(log, "bench-6", "bench-6/diff.tbl");
        }
        CHECK_JOURNAL(joined(log),
                     "lock bench-5|open bench-5/base.tbl|"
                     "lock bench-6|open bench-6/diff.tbl|"
                     "close bench-6/diff.tbl|unlock bench-6",
                     "inner session fully released while outer holds on");
    }
    CHECK_JOURNAL(joined(log, 6),
                 "close bench-5/base.tbl|unlock bench-5",
                 "outer session releases last, in order");
}

int main(void) {
    RUN(open_logs_immediately_and_close_on_scope_exit);
    RUN(default_constructed_guard_is_inert);
    RUN(scope_exit_releases_in_reverse_acquisition_order);
    RUN(move_construction_transfers_ownership_silently);
    RUN(guard_returned_from_a_factory_stays_owned_once);
    RUN(move_assignment_releases_the_old_resource_first);
    RUN(move_assignment_into_an_inactive_guard_adds_no_close);
    RUN(move_assignment_from_an_inactive_guard_releases_the_target);
    RUN(self_move_is_a_harmless_no_op);
    RUN(release_is_early_idempotent_and_final);
    RUN(moved_from_guard_can_be_reused);
    RUN(guards_survive_vector_reallocation);
    RUN(lock_guard_mirrors_the_file_contract);
    RUN(session_acquires_and_releases_as_a_unit);
    RUN(session_release_is_ordered_and_idempotent);
    RUN(session_move_transfers_the_whole_unit);
    RUN(interleaved_sessions_keep_their_own_order);
    return mt_summary();
}
