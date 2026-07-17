/* Acceptance tests for the settings store (config.h + pool.h).
 * Build and run with `make test`.
 *
 * Invariants pinned here:
 *   - cfg_get returns exactly the value the current config text assigns
 *     to a key, for every key, after any sequence of loads and reloads.
 *   - the pool accounting matches the store: one slot per setting, so
 *     pool_in_use(pool) == cfg_count(cfg) whenever the store is at rest.
 *   - reloads never exhaust an 8-slot pool for a 4-5 key config, no
 *     matter how many times values flip back and forth.
 */
#include "mintest.h"

#include "config.h"
#include "pool.h"

#define NSLOTS 8

struct fix {
    char slab[NSLOTS * POOL_SLOT_SIZE];
    int next[NSLOTS];
    strpool pool;
    config cfg;
};

static const char BOOT[] =
    "# controller boot settings\n"
    "listen_port = 8080\n"
    "log_level = info\n"
    "timeout_ms = 2500\n"
    "cache_dir = /var/cache/app\n";

static const char ROLLOUT[] =
    "listen_port = 8080\n"
    "log_level = debug\n"
    "timeout_ms = 2500\n"
    "metrics_addr = 127.0.0.1:9100\n";

static int boot(struct fix *f) {
    if (pool_init(&f->pool, f->slab, f->next, NSLOTS) != 0)
        return -1;
    return cfg_load(&f->cfg, &f->pool, BOOT);
}

static void expect(const config *c, const char *key, const char *want,
                   const char *msg) {
    CHECK_EQ_STR(cfg_get(c, key), want, msg);
}

TEST(initial_load_reads_back) {
    struct fix f;
    CHECK_EQ_INT(boot(&f), 0, "boot file loads");
    CHECK_EQ_INT(cfg_count(&f.cfg), 4, "four settings after load");
    expect(&f.cfg, "listen_port", "8080", "listen_port after load");
    expect(&f.cfg, "log_level", "info", "log_level after load");
    expect(&f.cfg, "timeout_ms", "2500", "timeout_ms after load");
    expect(&f.cfg, "cache_dir", "/var/cache/app", "cache_dir after load");
    CHECK(cfg_get(&f.cfg, "metrics_addr") == NULL, "unknown key is NULL");
    CHECK_EQ_INT(pool_in_use(&f.pool), 4, "one slot per setting");
}

TEST(reload_applies_the_rollout_file) {
    struct fix f;
    CHECK_EQ_INT(boot(&f), 0, "boot file loads");
    CHECK_EQ_INT(cfg_reload(&f.cfg, ROLLOUT), 0, "rollout reload succeeds");
    CHECK_EQ_INT(cfg_count(&f.cfg), 4, "four settings after rollout");
    expect(&f.cfg, "listen_port", "8080", "untouched listen_port survives");
    expect(&f.cfg, "log_level", "debug", "changed log_level updates");
    expect(&f.cfg, "timeout_ms", "2500", "untouched timeout_ms survives");
    expect(&f.cfg, "metrics_addr", "127.0.0.1:9100", "new key appears");
    CHECK(cfg_get(&f.cfg, "cache_dir") == NULL, "dropped key disappears");
    CHECK_EQ_INT(pool_in_use(&f.pool), 4,
                 "pool accounting matches the settings count");
}

TEST(reload_with_identical_text_changes_nothing) {
    struct fix f;
    CHECK_EQ_INT(boot(&f), 0, "boot file loads");
    CHECK_EQ_INT(cfg_reload(&f.cfg, BOOT), 0, "no-op reload succeeds");
    CHECK_EQ_INT(cfg_count(&f.cfg), 4, "still four settings");
    expect(&f.cfg, "listen_port", "8080", "listen_port after no-op reload");
    expect(&f.cfg, "log_level", "info", "log_level after no-op reload");
    expect(&f.cfg, "timeout_ms", "2500", "timeout_ms after no-op reload");
    expect(&f.cfg, "cache_dir", "/var/cache/app",
           "cache_dir after no-op reload");
    CHECK_EQ_INT(pool_in_use(&f.pool), 4,
                 "no-op reload keeps one slot per setting");
}

TEST(reload_back_and_forth_stays_consistent) {
    struct fix f;
    CHECK_EQ_INT(boot(&f), 0, "boot file loads");
    CHECK_EQ_INT(cfg_reload(&f.cfg, ROLLOUT), 0, "first reload succeeds");
    CHECK_EQ_INT(cfg_reload(&f.cfg, BOOT), 0, "reload back succeeds");
    CHECK_EQ_INT(cfg_count(&f.cfg), 4, "four settings after revert");
    expect(&f.cfg, "listen_port", "8080", "listen_port after revert");
    expect(&f.cfg, "log_level", "info", "log_level reverts");
    expect(&f.cfg, "timeout_ms", "2500", "timeout_ms after revert");
    expect(&f.cfg, "cache_dir", "/var/cache/app", "cache_dir is back");
    CHECK(cfg_get(&f.cfg, "metrics_addr") == NULL, "metrics_addr is gone");
    CHECK_EQ_INT(pool_in_use(&f.pool), 4, "accounting after revert");
}

TEST(reload_that_only_adds_keeps_old_values) {
    struct fix f;
    CHECK_EQ_INT(boot(&f), 0, "boot file loads");
    static const char WIDER[] =
        "listen_port = 8080\n"
        "log_level = info\n"
        "timeout_ms = 2500\n"
        "cache_dir = /var/cache/app\n"
        "audit = on\n";
    CHECK_EQ_INT(cfg_reload(&f.cfg, WIDER), 0, "additive reload succeeds");
    CHECK_EQ_INT(cfg_count(&f.cfg), 5, "five settings after addition");
    expect(&f.cfg, "listen_port", "8080", "listen_port unchanged");
    expect(&f.cfg, "log_level", "info", "log_level unchanged");
    expect(&f.cfg, "timeout_ms", "2500", "timeout_ms unchanged");
    expect(&f.cfg, "cache_dir", "/var/cache/app", "cache_dir unchanged");
    expect(&f.cfg, "audit", "on", "new audit key present");
    CHECK_EQ_INT(pool_in_use(&f.pool), 5, "five slots for five settings");
}

TEST(reload_that_shrinks_releases_settings) {
    struct fix f;
    CHECK_EQ_INT(boot(&f), 0, "boot file loads");
    static const char TRIMMED[] =
        "listen_port = 8080\n"
        "log_level = warn\n";
    CHECK_EQ_INT(cfg_reload(&f.cfg, TRIMMED), 0, "shrinking reload succeeds");
    CHECK_EQ_INT(cfg_count(&f.cfg), 2, "two settings remain");
    expect(&f.cfg, "listen_port", "8080", "kept key reads back");
    expect(&f.cfg, "log_level", "warn", "changed value reads back");
    CHECK(cfg_get(&f.cfg, "timeout_ms") == NULL, "timeout_ms removed");
    CHECK(cfg_get(&f.cfg, "cache_dir") == NULL, "cache_dir removed");
    CHECK_EQ_INT(pool_in_use(&f.pool), 2, "two slots for two settings");
}

TEST(flip_flop_reloads_never_exhaust_the_pool) {
    struct fix f;
    CHECK_EQ_INT(boot(&f), 0, "boot file loads");
    for (int round = 0; round < 6; round++) {
        const char *text = (round % 2 == 0) ? ROLLOUT : BOOT;
        CHECK_EQ_INT(cfg_reload(&f.cfg, text), 0, "flip reload succeeds");
        CHECK_EQ_INT(pool_in_use(&f.pool), cfg_count(&f.cfg),
                     "accounting matches count after every flip");
    }
    /* six rounds end on BOOT */
    expect(&f.cfg, "log_level", "info", "log_level after final flip");
    expect(&f.cfg, "timeout_ms", "2500", "timeout_ms after final flip");
    expect(&f.cfg, "cache_dir", "/var/cache/app",
           "cache_dir after final flip");
    CHECK(cfg_get(&f.cfg, "metrics_addr") == NULL,
          "metrics_addr gone after final flip");
}

TEST(lookups_handle_nulls) {
    struct fix f;
    CHECK_EQ_INT(boot(&f), 0, "boot file loads");
    CHECK(cfg_get(NULL, "listen_port") == NULL, "NULL config is NULL");
    CHECK(cfg_get(&f.cfg, NULL) == NULL, "NULL key is NULL");
    CHECK_EQ_INT(cfg_count(NULL), 0, "NULL config counts zero");
}

int main(void) {
    RUN(initial_load_reads_back);
    RUN(reload_applies_the_rollout_file);
    RUN(reload_with_identical_text_changes_nothing);
    RUN(reload_back_and_forth_stays_consistent);
    RUN(reload_that_only_adds_keeps_old_values);
    RUN(reload_that_shrinks_releases_settings);
    RUN(flip_flop_reloads_never_exhaust_the_pool);
    RUN(lookups_handle_nulls);
    return mt_summary();
}
