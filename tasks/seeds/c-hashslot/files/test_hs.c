/* Acceptance tests for hs.h / hs.c — the string-key slot map.
 *
 * The layout policy (FNV-1a, initial capacity 16, linear probing, the
 * 7/10 load rule, tombstone reuse, slot-order iteration) is part of the
 * contract: we diff snapshot dumps of this table between runs and
 * boxes, so the expected sequences below are fixed, not incidental.
 *
 * Build and run with `make test`.
 */
#include "mintest.h"
#include "hs.h"

static int box[32]; /* stable value targets */

static void check_iter(hs_map *m, const char **want, size_t n,
                       const char *label) {
    size_t it = 0, i = 0;
    const char *k = NULL;
    void *v = NULL;
    while (hs_next(m, &it, &k, &v)) {
        if (i < n)
            CHECK_EQ_STR(k, want[i], label);
        i++;
    }
    CHECK_EQ_INT(i, n, label);
}

TEST(new_map_is_empty) {
    hs_map *m = hs_new();
    CHECK(m != NULL, "hs_new returns a map");
    CHECK_EQ_INT(hs_len(m), 0, "empty map has no entries");
    CHECK_EQ_INT(hs_cap(m), 16, "initial capacity is 16");
    CHECK(hs_get(m, "bolt-m3") == NULL, "get on empty map is NULL");
    CHECK_EQ_INT(hs_del(m, "bolt-m3"), -1, "del on empty map is -1");
    size_t it = 0;
    const char *k;
    void *v;
    CHECK_EQ_INT(hs_next(m, &it, &k, &v), 0, "iteration over empty map ends");
    hs_free(m);
}

TEST(put_get_update_basics) {
    hs_map *m = hs_new();
    CHECK_EQ_INT(hs_put(m, "washer-6", &box[0]), 0, "put ok");
    CHECK_EQ_INT(hs_put(m, "nut-m3", &box[1]), 0, "put ok");
    CHECK_EQ_INT(hs_put(m, "", &box[2]), 0, "empty string is a valid key");
    CHECK_EQ_INT(hs_len(m), 3, "three entries");
    CHECK(hs_get(m, "washer-6") == &box[0], "get returns stored pointer");
    CHECK(hs_get(m, "nut-m3") == &box[1], "second key");
    CHECK(hs_get(m, "") == &box[2], "empty-string key retrievable");
    CHECK(hs_get(m, "washer-8") == NULL, "absent key is NULL");
    CHECK_EQ_INT(hs_put(m, "nut-m3", &box[3]), 0, "update existing key");
    CHECK_EQ_INT(hs_len(m), 3, "update does not change len");
    CHECK(hs_get(m, "nut-m3") == &box[3], "updated value visible");
    hs_free(m);
}

TEST(keys_are_copied_on_insert) {
    hs_map *m = hs_new();
    char buf[16];
    strcpy(buf, "clip-9");
    CHECK_EQ_INT(hs_put(m, buf, &box[0]), 0, "put from caller buffer");
    memset(buf, 'x', 6); /* caller reuses the buffer */
    CHECK(hs_get(m, "clip-9") == &box[0], "map kept its own key copy");
    CHECK(hs_get(m, "xxxxxx") == NULL, "mutated buffer is not a key");
    size_t it = 0;
    const char *k = NULL;
    void *v = NULL;
    CHECK_EQ_INT(hs_next(m, &it, &k, &v), 1, "one entry to iterate");
    CHECK_EQ_STR(k, "clip-9", "iterated key text is the stored copy");
    CHECK(k != buf, "iterated key is not the caller's buffer");
    hs_free(m);
}

TEST(iteration_follows_slot_order) {
    hs_map *m = hs_new();
    const char *ins[] = {"washer-6", "bolt-m5", "nut-m3", "clip-9",
                         "nut-m4",   "plate-c", "washer-8"};
    for (int i = 0; i < 7; i++)
        CHECK_EQ_INT(hs_put(m, ins[i], &box[i]), 0, "insert");
    CHECK_EQ_INT(hs_len(m), 7, "seven entries");
    CHECK_EQ_INT(hs_cap(m), 16, "no growth at seven");
    /* slot order under FNV-1a & 15, not insertion order: */
    const char *want[] = {"clip-9",   "bolt-m5",  "nut-m4", "plate-c",
                          "washer-8", "washer-6", "nut-m3"};
    check_iter(m, want, 7, "slot-order iteration of distinct-slot keys");
    CHECK(hs_get(m, "plate-c") == &box[5], "values follow their keys");
    hs_free(m);
}

TEST(collision_chain_probes_linearly) {
    hs_map *m = hs_new();
    /* bolt-m3, rail-2m, anchor-l all hash to slot 5; nut-m4's home
     * slot 6 is taken by the chain, so it probes to 8. */
    CHECK_EQ_INT(hs_put(m, "bolt-m3", &box[0]), 0, "cluster insert 1");
    CHECK_EQ_INT(hs_put(m, "rail-2m", &box[1]), 0, "cluster insert 2");
    CHECK_EQ_INT(hs_put(m, "anchor-l", &box[2]), 0, "cluster insert 3");
    CHECK_EQ_INT(hs_put(m, "nut-m4", &box[3]), 0, "displaced insert");
    const char *want[] = {"bolt-m3", "rail-2m", "anchor-l", "nut-m4"};
    check_iter(m, want, 4, "chain occupies consecutive slots");
    CHECK(hs_get(m, "anchor-l") == &box[2], "probe walks the chain");
    CHECK(hs_get(m, "nut-m4") == &box[3], "displaced key still found");
    hs_free(m);
}

TEST(tombstone_keeps_chain_walkable_and_is_reused) {
    hs_map *m = hs_new();
    hs_put(m, "bolt-m3", &box[0]);
    hs_put(m, "rail-2m", &box[1]);
    hs_put(m, "anchor-l", &box[2]);
    hs_put(m, "nut-m4", &box[3]);
    CHECK_EQ_INT(hs_del(m, "rail-2m"), 0, "delete middle of the chain");
    CHECK_EQ_INT(hs_len(m), 3, "len drops");
    CHECK(hs_get(m, "rail-2m") == NULL, "deleted key is gone");
    CHECK(hs_get(m, "anchor-l") == &box[2],
          "probing continues past the deleted slot");
    CHECK(hs_get(m, "nut-m4") == &box[3], "tail of the chain reachable");
    CHECK_EQ_INT(hs_del(m, "rail-2m"), -1, "second delete is -1");
    const char *after_del[] = {"bolt-m3", "anchor-l", "nut-m4"};
    check_iter(m, after_del, 3, "iteration skips the deleted slot");
    /* dowel-8 also hashes to slot 5; its probe passes the tombstone
     * first, so the freed slot is reused. */
    CHECK_EQ_INT(hs_put(m, "dowel-8", &box[4]), 0, "insert same-slot key");
    CHECK_EQ_INT(hs_len(m), 4, "len back to four");
    const char *after_ins[] = {"bolt-m3", "dowel-8", "anchor-l", "nut-m4"};
    check_iter(m, after_ins, 4, "new key sits in the reused slot");
    CHECK(hs_get(m, "dowel-8") == &box[4], "reused-slot key retrievable");
    CHECK(hs_get(m, "anchor-l") == &box[2], "chain intact after reuse");
    hs_free(m);
}

static const char *eleven[] = {"bolt-m3", "bolt-m4",  "nut-m3",   "nut-m4",
                               "washer-6", "washer-8", "clip-9",  "rail-2m",
                               "screw-p1", "screw-p2", "anchor-s"};

TEST(rehash_doubles_at_the_twelfth_key) {
    hs_map *m = hs_new();
    for (int i = 0; i < 11; i++)
        CHECK_EQ_INT(hs_put(m, eleven[i], &box[i]), 0, "insert");
    CHECK_EQ_INT(hs_len(m), 11, "eleven live");
    CHECK_EQ_INT(hs_cap(m), 16, "eleven of sixteen stays put (11+1 <= 70%)");
    const char *at16[] = {"bolt-m4", "clip-9",   "screw-p2", "bolt-m3",
                          "nut-m4",  "rail-2m",  "anchor-s", "screw-p1",
                          "washer-8", "washer-6", "nut-m3"};
    check_iter(m, at16, 11, "layout before the rehash");
    CHECK_EQ_INT(hs_put(m, "brace-l", &box[11]), 0, "twelfth key");
    CHECK_EQ_INT(hs_cap(m), 32, "twelfth insert doubles capacity");
    CHECK_EQ_INT(hs_len(m), 12, "twelve live");
    const char *at32[] = {"bolt-m4",  "clip-9",   "bolt-m3", "nut-m4",
                          "rail-2m",  "screw-p1", "washer-8", "screw-p2",
                          "anchor-s", "brace-l",  "washer-6", "nut-m3"};
    check_iter(m, at32, 12, "layout re-derived at capacity 32");
    for (int i = 0; i < 11; i++)
        CHECK(hs_get(m, eleven[i]) == &box[i], "values survive the rehash");
    CHECK(hs_get(m, "brace-l") == &box[11], "new key present after rehash");
    hs_free(m);
}

TEST(tombstones_count_toward_the_load_factor) {
    hs_map *m = hs_new();
    for (int i = 0; i < 11; i++)
        hs_put(m, eleven[i], &box[i]);
    CHECK_EQ_INT(hs_del(m, "nut-m3"), 0, "del 1");
    CHECK_EQ_INT(hs_del(m, "washer-6"), 0, "del 2");
    CHECK_EQ_INT(hs_del(m, "clip-9"), 0, "del 3");
    CHECK_EQ_INT(hs_del(m, "screw-p2"), 0, "del 4");
    CHECK_EQ_INT(hs_len(m), 7, "seven live after deletes");
    CHECK_EQ_INT(hs_cap(m), 16, "deletes alone never resize");
    /* Only 7 live, but 11 slots are burned (7 live + 4 tombstones), so
     * the next insert must still rehash — and the rehash drops the
     * tombstones. */
    CHECK_EQ_INT(hs_put(m, "hinge-3", &box[20]), 0, "insert over tombstones");
    CHECK_EQ_INT(hs_cap(m), 32, "occupied slots (not live count) trigger it");
    CHECK_EQ_INT(hs_len(m), 8, "eight live after");
    const char *want[] = {"bolt-m4", "hinge-3",  "bolt-m3",  "nut-m4",
                          "rail-2m", "screw-p1", "washer-8", "anchor-s"};
    check_iter(m, want, 8, "clean layout after tombstones dropped");
    CHECK(hs_get(m, "nut-m3") == NULL, "deleted keys stay deleted");
    CHECK(hs_get(m, "hinge-3") == &box[20], "new key found");
    hs_free(m);
}

TEST(delete_all_then_reuse) {
    hs_map *m = hs_new();
    for (int i = 0; i < 7; i++)
        hs_put(m, eleven[i], &box[i]);
    for (int i = 0; i < 7; i++)
        CHECK_EQ_INT(hs_del(m, eleven[i]), 0, "drain");
    CHECK_EQ_INT(hs_len(m), 0, "map empty again");
    size_t it = 0;
    const char *k;
    void *v;
    CHECK_EQ_INT(hs_next(m, &it, &k, &v), 0, "nothing to iterate");
    CHECK_EQ_INT(hs_put(m, "strap-9", &box[9]), 0, "map still usable");
    CHECK(hs_get(m, "strap-9") == &box[9], "reinserted key found");
    CHECK_EQ_INT(hs_len(m), 1, "one live entry");
    hs_free(m);
}

int main(void) {
    RUN(new_map_is_empty);
    RUN(put_get_update_basics);
    RUN(keys_are_copied_on_insert);
    RUN(iteration_follows_slot_order);
    RUN(collision_chain_probes_linearly);
    RUN(tombstone_keeps_chain_walkable_and_is_reused);
    RUN(rehash_doubles_at_the_twelfth_key);
    RUN(tombstones_count_toward_the_load_factor);
    RUN(delete_all_then_reuse);
    return mt_summary();
}
