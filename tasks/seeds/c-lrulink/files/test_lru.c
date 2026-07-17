/* Acceptance tests for the directory-entry LRU cache (lru.h).
 * Build and run with `make test`.
 *
 * Recency contract: lru_get and lru_put promote; lru_contains never
 * does. lru_keys_mru dumps keys most-recent-first and is the order
 * oracle used throughout.
 */
#include "mintest.h"

#include <stdint.h>

#include "lru.h"

static void dump_check(lru_cache *c, const uint32_t *want, size_t n,
                       const char *msg) {
    uint32_t got[16];
    size_t k = lru_keys_mru(c, got, 16);
    CHECK_EQ_INT(k, n, msg);
    if (k == n)
        for (size_t i = 0; i < n; i++)
            CHECK_EQ_INT(got[i], want[i], msg);
}

TEST(init_validates_storage) {
    lru_entry slots[4];
    lru_cache c;
    CHECK_EQ_INT(lru_init(&c, slots, 4), 0, "init succeeds");
    CHECK_EQ_INT(lru_len(&c), 0, "fresh cache is empty");
    CHECK_EQ_INT(lru_hits(&c), 0, "no hits yet");
    CHECK_EQ_INT(lru_misses(&c), 0, "no misses yet");
    CHECK_EQ_INT(lru_evictions(&c), 0, "no evictions yet");
    CHECK_EQ_INT(lru_init(&c, NULL, 4), -1, "NULL slots rejected");
    CHECK_EQ_INT(lru_init(&c, slots, 0), -1, "zero capacity rejected");
}

TEST(put_then_get_returns_the_value) {
    lru_entry slots[4];
    lru_cache c;
    lru_init(&c, slots, 4);
    lru_put(&c, 11, 100);
    CHECK_EQ_INT(lru_len(&c), 1, "one entry after put");
    uint32_t v = 777;
    CHECK_EQ_INT(lru_get(&c, 11, &v), 1, "present key hits");
    CHECK_EQ_INT(v, 100, "hit writes the value");
    v = 777;
    CHECK_EQ_INT(lru_get(&c, 12, &v), 0, "absent key misses");
    CHECK_EQ_INT(v, 777, "miss leaves value_out alone");
    CHECK_EQ_INT(lru_hits(&c), 1, "one hit counted");
    CHECK_EQ_INT(lru_misses(&c), 1, "one miss counted");
}

TEST(update_existing_key_promotes_and_keeps_len) {
    lru_entry slots[3];
    lru_cache c;
    lru_init(&c, slots, 3);
    lru_put(&c, 1, 10);
    lru_put(&c, 2, 20);
    lru_put(&c, 3, 30);
    lru_put(&c, 2, 21); /* update, not insert */
    CHECK_EQ_INT(lru_len(&c), 3, "update does not grow the cache");
    CHECK_EQ_INT(lru_evictions(&c), 0, "update never evicts");
    uint32_t want[] = {2, 3, 1};
    dump_check(&c, want, 3, "updated key moves to the front");
    uint32_t v = 0;
    lru_get(&c, 2, &v);
    CHECK_EQ_INT(v, 21, "update replaced the value");
}

TEST(get_promotes_to_mru) {
    lru_entry slots[3];
    lru_cache c;
    lru_init(&c, slots, 3);
    lru_put(&c, 1, 10);
    lru_put(&c, 2, 20);
    lru_put(&c, 3, 30);
    CHECK_EQ_INT(lru_get(&c, 1, NULL), 1, "oldest key hits");
    uint32_t want1[] = {1, 3, 2};
    dump_check(&c, want1, 3, "hit key moves to the front");
    lru_put(&c, 4, 40);
    CHECK_EQ_INT(lru_contains(&c, 2), 0, "true LRU key 2 was evicted");
    uint32_t want2[] = {4, 1, 3};
    dump_check(&c, want2, 3, "order after eviction");
}

TEST(contains_does_not_promote_or_count) {
    lru_entry slots[2];
    lru_cache c;
    lru_init(&c, slots, 2);
    lru_put(&c, 1, 10);
    lru_put(&c, 2, 20);
    CHECK_EQ_INT(lru_contains(&c, 1), 1, "key 1 is present");
    lru_put(&c, 3, 30); /* contains() must NOT have promoted key 1 */
    CHECK_EQ_INT(lru_contains(&c, 1), 0, "key 1 still got evicted");
    CHECK_EQ_INT(lru_contains(&c, 2), 1, "key 2 survived");
    CHECK_EQ_INT(lru_hits(&c), 0, "contains never counts a hit");
    CHECK_EQ_INT(lru_misses(&c), 0, "contains never counts a miss");
    CHECK_EQ_INT(lru_evictions(&c), 1, "one eviction");
}

TEST(eviction_is_strictly_least_recent) {
    lru_entry slots[3];
    lru_cache c;
    lru_init(&c, slots, 3);
    lru_put(&c, 1, 10);
    lru_put(&c, 2, 20);
    lru_put(&c, 3, 30);
    lru_get(&c, 1, NULL);
    lru_get(&c, 3, NULL);
    lru_put(&c, 4, 40); /* evicts 2 */
    lru_put(&c, 5, 50); /* evicts 1 */
    CHECK_EQ_INT(lru_contains(&c, 2), 0, "2 went first");
    CHECK_EQ_INT(lru_contains(&c, 1), 0, "1 went second");
    CHECK_EQ_INT(lru_evictions(&c), 2, "two evictions");
    uint32_t want[] = {5, 4, 3};
    dump_check(&c, want, 3, "survivors in recency order");
}

TEST(colliding_keys_chain_and_evict_cleanly) {
    /* 7, 39, 71, 103 all land in the same bucket (mod 32). */
    lru_entry slots[2];
    lru_cache c;
    lru_init(&c, slots, 2);
    lru_put(&c, 7, 70);
    lru_put(&c, 39, 390);
    CHECK_EQ_INT(lru_len(&c), 2, "two colliding keys stored");
    uint32_t v = 0;
    CHECK_EQ_INT(lru_get(&c, 7, &v), 1, "first chained key hits");
    CHECK_EQ_INT(v, 70, "chained value correct");
    lru_put(&c, 71, 710); /* evicts 39 */
    CHECK_EQ_INT(lru_get(&c, 39, &v), 0, "evicted chain member misses");
    CHECK_EQ_INT(lru_get(&c, 71, &v), 1, "new chain member hits");
    CHECK_EQ_INT(v, 710, "new chain value correct");
    CHECK_EQ_INT(lru_get(&c, 7, &v), 1, "old chain member still hits");
    CHECK_EQ_INT(v, 70, "old chain value survived the unlink");
    lru_put(&c, 103, 1030); /* evicts 71 (7 was just promoted) */
    CHECK_EQ_INT(lru_contains(&c, 71), 0, "71 evicted in turn");
    CHECK_EQ_INT(lru_contains(&c, 7), 1, "7 promoted and kept");
    CHECK_EQ_INT(lru_contains(&c, 103), 1, "103 inserted");
    CHECK_EQ_INT(lru_evictions(&c), 2, "two chain evictions");
}

TEST(cap_one_cache_thrashes_correctly) {
    lru_entry slots[1];
    lru_cache c;
    lru_init(&c, slots, 1);
    lru_put(&c, 5, 50);
    CHECK_EQ_INT(lru_get(&c, 5, NULL), 1, "single slot hits");
    lru_put(&c, 6, 60);
    CHECK_EQ_INT(lru_get(&c, 5, NULL), 0, "old key gone");
    uint32_t v = 0;
    CHECK_EQ_INT(lru_get(&c, 6, &v), 1, "new key present");
    CHECK_EQ_INT(v, 60, "new value present");
    CHECK_EQ_INT(lru_len(&c), 1, "len pinned at one");
    CHECK_EQ_INT(lru_evictions(&c), 1, "one eviction");
}

TEST(long_run_recycles_the_same_slots) {
    lru_entry slots[4];
    lru_cache c;
    lru_init(&c, slots, 4);
    for (uint32_t k = 0; k < 100; k++)
        lru_put(&c, k * 32, k); /* every key in bucket 0 */
    CHECK_EQ_INT(lru_len(&c), 4, "len stays at capacity");
    CHECK_EQ_INT(lru_evictions(&c), 96, "everything else evicted");
    uint32_t want[] = {3168, 3136, 3104, 3072};
    dump_check(&c, want, 4, "last four keys survive in order");
    uint32_t v = 0;
    CHECK_EQ_INT(lru_get(&c, 3072, &v), 1, "oldest survivor hits");
    CHECK_EQ_INT(v, 96, "value from the 97th put");
    uint32_t want2[] = {3072, 3168, 3136, 3104};
    dump_check(&c, want2, 4, "hit reorders the survivors");
    CHECK_EQ_INT(lru_contains(&c, 3040), 0, "95th key is long gone");
}

TEST(keys_mru_respects_max) {
    lru_entry slots[4];
    lru_cache c;
    lru_init(&c, slots, 4);
    for (uint32_t k = 1; k <= 4; k++)
        lru_put(&c, k, k * 10);
    uint32_t out[2] = {0, 0};
    CHECK_EQ_INT(lru_keys_mru(&c, out, 2), 2, "asks for two, gets two");
    CHECK_EQ_INT(out[0], 4, "most recent first");
    CHECK_EQ_INT(out[1], 3, "then the next");
    CHECK_EQ_INT(lru_keys_mru(&c, out, 0), 0, "max zero writes nothing");
    uint32_t all[16];
    CHECK_EQ_INT(lru_keys_mru(&c, all, 16), 4, "large max returns len");
}

TEST(stats_track_a_scripted_session) {
    lru_entry slots[2];
    lru_cache c;
    lru_init(&c, slots, 2);
    uint32_t v = 0;
    lru_get(&c, 5, &v);  /* miss */
    lru_put(&c, 5, 1);
    lru_get(&c, 5, &v);  /* hit */
    lru_put(&c, 6, 2);
    lru_put(&c, 7, 3);   /* evicts 5 */
    lru_get(&c, 5, &v);  /* miss */
    lru_get(&c, 6, &v);  /* hit */
    lru_get(&c, 7, &v);  /* hit */
    CHECK_EQ_INT(lru_hits(&c), 3, "three hits");
    CHECK_EQ_INT(lru_misses(&c), 2, "two misses");
    CHECK_EQ_INT(lru_evictions(&c), 1, "one eviction");
    CHECK_EQ_INT(lru_len(&c), 2, "two entries");
    uint32_t want[] = {7, 6};
    dump_check(&c, want, 2, "final recency order");
}

int main(void) {
    RUN(init_validates_storage);
    RUN(put_then_get_returns_the_value);
    RUN(update_existing_key_promotes_and_keeps_len);
    RUN(get_promotes_to_mru);
    RUN(contains_does_not_promote_or_count);
    RUN(eviction_is_strictly_least_recent);
    RUN(colliding_keys_chain_and_evict_cleanly);
    RUN(cap_one_cache_thrashes_correctly);
    RUN(long_run_recycles_the_same_slots);
    RUN(keys_mru_respects_max);
    RUN(stats_track_a_scripted_session);
    return mt_summary();
}
