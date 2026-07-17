/* Acceptance tests for the archive-candidate sweep (sweep.h).
 * Build and run with `make test`.
 *
 * The NAS listing fixture below is taken from the real collector output
 * (byte counts checked by hand against the share). The reports are
 * pinned line for line: candidate set, MiB figures, and ordering.
 */
#include "mintest.h"

#include "sweep.h"

static const char NAS_LISTING[] =
    "536870912\t/data/backups/small.tgz\n"
    "3221225472\t/data/vm/dev-disk.qcow2\n"
    "5368709120\t/data/vm/ci-runner.qcow2\n"
    "4294967296\t/data/media/keynote-master.mov\n"
    "6979321856\t/data/media/plenary-4k.mov\n"
    "-1\t/data/quarantine/locked.bin\n"
    "2147483648\t/data/backups/q2-full.tar\n"
    "6442450944\t/data/vm/build-cache.img\n";

#define GIB2 2147483648LL
#define GIB4 4294967296LL

TEST(parse_stores_files_and_skips_unreadable) {
    sweep_scan s;
    CHECK_EQ_INT(sweep_parse(&s, NAS_LISTING), 7, "seven files stored");
    CHECK_EQ_INT(s.count, 7, "count matches return value");
    CHECK_EQ_INT(s.skipped, 1, "the -1 line counts as skipped");
    CHECK_EQ_STR(s.files[0].path, "/data/backups/small.tgz",
                 "first path stored verbatim");
    CHECK_EQ_STR(s.files[6].path, "/data/vm/build-cache.img",
                 "last path stored verbatim");
}

TEST(report_at_two_gib_threshold) {
    sweep_scan s;
    char out[512];
    CHECK_EQ_INT(sweep_parse(&s, NAS_LISTING), 7, "listing parses");
    int rc = sweep_render(&s, GIB2, out, sizeof out);
    CHECK_EQ_INT(rc, 6, "six candidates at the 2 GiB threshold");
    CHECK_EQ_STR(out,
                 "6656 MiB\t/data/media/plenary-4k.mov\n"
                 "6144 MiB\t/data/vm/build-cache.img\n"
                 "5120 MiB\t/data/vm/ci-runner.qcow2\n"
                 "4096 MiB\t/data/media/keynote-master.mov\n"
                 "3072 MiB\t/data/vm/dev-disk.qcow2\n"
                 "2048 MiB\t/data/backups/q2-full.tar\n",
                 "2 GiB report: sizes right, biggest first, boundary file in");
}

TEST(report_at_four_gib_threshold) {
    sweep_scan s;
    char out[512];
    CHECK_EQ_INT(sweep_parse(&s, NAS_LISTING), 7, "listing parses");
    int rc = sweep_render(&s, GIB4, out, sizeof out);
    CHECK_EQ_INT(rc, 4, "four candidates at the 4 GiB threshold");
    CHECK_EQ_STR(out,
                 "6656 MiB\t/data/media/plenary-4k.mov\n"
                 "6144 MiB\t/data/vm/build-cache.img\n"
                 "5120 MiB\t/data/vm/ci-runner.qcow2\n"
                 "4096 MiB\t/data/media/keynote-master.mov\n",
                 "4 GiB report keeps every file that big");
}

TEST(equal_sizes_fall_back_to_path_order) {
    sweep_scan s;
    char out[256];
    static const char LISTING[] =
        "3221225472\t/vm/zeta.img\n"
        "3221225472\t/vm/alpha.img\n"
        "1048576\t/vm/tiny.img\n";
    CHECK_EQ_INT(sweep_parse(&s, LISTING), 3, "three files stored");
    int rc = sweep_render(&s, GIB2, out, sizeof out);
    CHECK_EQ_INT(rc, 2, "two candidates");
    CHECK_EQ_STR(out,
                 "3072 MiB\t/vm/alpha.img\n"
                 "3072 MiB\t/vm/zeta.img\n",
                 "same size orders by path");
}

TEST(mib_figures_round_down) {
    sweep_scan s;
    char out[128];
    CHECK_EQ_INT(sweep_parse(&s, "2148532347\t/x/a.bin\n"), 1, "one file");
    CHECK_EQ_INT(sweep_render(&s, 0, out, sizeof out), 1, "one line");
    CHECK_EQ_STR(out, "2049 MiB\t/x/a.bin\n", "partial MiB is floored");
}

TEST(threshold_above_everything_gives_empty_report) {
    sweep_scan s;
    char out[128];
    CHECK_EQ_INT(sweep_parse(&s, NAS_LISTING), 7, "listing parses");
    CHECK_EQ_INT(sweep_render(&s, 8796093022208LL, out, sizeof out), 0,
                 "no candidates at 8 TiB");
    CHECK_EQ_STR(out, "", "empty report is an empty string");
}

TEST(empty_listing_is_fine) {
    sweep_scan s;
    char out[64];
    CHECK_EQ_INT(sweep_parse(&s, ""), 0, "no files");
    CHECK_EQ_INT(s.skipped, 0, "nothing skipped");
    CHECK_EQ_INT(sweep_render(&s, 0, out, sizeof out), 0, "no lines");
    CHECK_EQ_STR(out, "", "empty report");
}

TEST(malformed_lines_are_rejected) {
    sweep_scan s;
    CHECK_EQ_INT(sweep_parse(&s, "12345 /no/tab/here\n"), -1,
                 "space instead of tab is malformed");
    CHECK_EQ_INT(sweep_parse(&s, "big\t/data/x\n"), -1,
                 "missing byte count is malformed");
    CHECK_EQ_INT(sweep_parse(&s, "123\t\n"), -1, "empty path is malformed");
    CHECK_EQ_INT(sweep_parse(NULL, "1\t/x\n"), -1, "NULL scan rejected");
    CHECK_EQ_INT(sweep_parse(&s, NULL), -1, "NULL listing rejected");
}

TEST(render_rejects_tiny_buffers) {
    sweep_scan s;
    char out[10];
    CHECK_EQ_INT(sweep_parse(&s, "3221225472\t/vm/a.img\n"), 1, "one file");
    CHECK_EQ_INT(sweep_render(&s, 0, out, sizeof out), -1,
                 "report that cannot fit reports failure");
    CHECK_EQ_INT(sweep_render(&s, 0, NULL, 64), -1, "NULL out rejected");
}

int main(void) {
    RUN(parse_stores_files_and_skips_unreadable);
    RUN(report_at_two_gib_threshold);
    RUN(report_at_four_gib_threshold);
    RUN(equal_sizes_fall_back_to_path_order);
    RUN(mib_figures_round_down);
    RUN(threshold_above_everything_gives_empty_report);
    RUN(empty_listing_is_fine);
    RUN(malformed_lines_are_rejected);
    RUN(render_rejects_tiny_buffers);
    return mt_summary();
}
