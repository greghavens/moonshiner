/* Acceptance tests for dynarray.h / dynarray.c — the growable array.
 * Build and run with `make test`.
 */
#include "mintest.h"
#include "dynarray.h"

/* The struct payload used across the struct-element tests: one picking
 * line from the stockroom sheet. */
typedef struct {
    int id;
    char code[8];
    double qty;
} pick_line;

static pick_line mk(int id, const char *code, double qty) {
    pick_line p;
    p.id = id;
    memset(p.code, 0, sizeof p.code);
    strncpy(p.code, code, sizeof p.code - 1);
    p.qty = qty;
    return p;
}

TEST(init_starts_empty_with_no_buffer) {
    dynarray a;
    CHECK_EQ_INT(da_init(&a, sizeof(int)), 0, "init succeeds");
    CHECK_EQ_INT(da_len(&a), 0, "fresh array is empty");
    CHECK_EQ_INT(da_cap(&a), 0, "no capacity before first push");
    CHECK(da_get(&a, 0) == NULL, "get on empty array is NULL");
    da_free(&a);
}

TEST(init_rejects_zero_elem_size) {
    dynarray a;
    CHECK_EQ_INT(da_init(&a, 0), -1, "elem_size 0 is rejected");
}

TEST(push_grows_geometrically) {
    dynarray a;
    int v;
    CHECK_EQ_INT(da_init(&a, sizeof v), 0, "init");
    v = 100;
    CHECK_EQ_INT(da_push(&a, &v), 0, "first push");
    CHECK_EQ_INT(da_cap(&a), 8, "first allocation is 8 elements");
    for (v = 101; v <= 107; v++)
        CHECK_EQ_INT(da_push(&a, &v), 0, "fill to 8");
    CHECK_EQ_INT(da_len(&a), 8, "eight elements");
    CHECK_EQ_INT(da_cap(&a), 8, "still 8 at exactly 8");
    v = 108;
    CHECK_EQ_INT(da_push(&a, &v), 0, "ninth push");
    CHECK_EQ_INT(da_cap(&a), 16, "capacity doubles to 16");
    for (v = 109; v < 200; v++)
        CHECK_EQ_INT(da_push(&a, &v), 0, "push to 100 total");
    CHECK_EQ_INT(da_len(&a), 100, "one hundred elements");
    CHECK_EQ_INT(da_cap(&a), 128, "capacity followed 8,16,32,64,128");
    for (size_t i = 0; i < 100; i++) {
        int *p = da_get(&a, i);
        CHECK(p != NULL, "element reachable");
        if (p)
            CHECK_EQ_INT(*p, 100 + (int)i, "values survive every growth");
    }
    da_free(&a);
}

TEST(pop_returns_last_and_never_shrinks) {
    dynarray a;
    CHECK_EQ_INT(da_init(&a, sizeof(int)), 0, "init");
    for (int v = 1; v <= 10; v++)
        da_push(&a, &v);
    CHECK_EQ_INT(da_cap(&a), 16, "capacity is 16 after 10 pushes");
    int out = 0;
    CHECK_EQ_INT(da_pop(&a, &out), 0, "pop succeeds");
    CHECK_EQ_INT(out, 10, "pop returns the last element");
    CHECK_EQ_INT(da_len(&a), 9, "length drops by one");
    CHECK_EQ_INT(da_pop(&a, NULL), 0, "pop with NULL out just discards");
    CHECK_EQ_INT(da_len(&a), 8, "discarding pop also shrinks length");
    while (da_len(&a) > 0)
        CHECK_EQ_INT(da_pop(&a, &out), 0, "drain");
    CHECK_EQ_INT(out, 1, "last drained value is the first pushed");
    CHECK_EQ_INT(da_pop(&a, &out), -1, "pop on empty is an error");
    CHECK_EQ_INT(da_cap(&a), 16, "capacity never shrinks");
    da_free(&a);
}

TEST(insert_shifts_elements) {
    dynarray a;
    CHECK_EQ_INT(da_init(&a, sizeof(int)), 0, "init");
    int v;
    for (v = 1; v <= 4; v++)
        da_push(&a, &v); /* 1 2 3 4 */
    v = 10;
    CHECK_EQ_INT(da_insert(&a, 0, &v), 0, "insert at front");
    v = 20;
    CHECK_EQ_INT(da_insert(&a, 3, &v), 0, "insert in the middle");
    v = 30;
    CHECK_EQ_INT(da_insert(&a, da_len(&a), &v), 0, "insert at len appends");
    int want[] = {10, 1, 2, 20, 3, 4, 30};
    CHECK_EQ_INT(da_len(&a), 7, "seven elements after inserts");
    for (size_t i = 0; i < 7; i++) {
        int *p = da_get(&a, i);
        CHECK(p != NULL && *p == want[i], "order after shifts");
    }
    v = 99;
    CHECK_EQ_INT(da_insert(&a, da_len(&a) + 1, &v), -1,
                 "insert past len is rejected");
    CHECK_EQ_INT(da_len(&a), 7, "failed insert leaves length alone");
    da_free(&a);
}

TEST(insert_into_empty_and_growth_boundary) {
    dynarray a;
    CHECK_EQ_INT(da_init(&a, sizeof(int)), 0, "init");
    int v = 7;
    CHECK_EQ_INT(da_insert(&a, 0, &v), 0, "insert at 0 on empty array");
    CHECK_EQ_INT(da_len(&a), 1, "one element");
    for (v = 1; v <= 7; v++)
        da_push(&a, &v); /* len 8 == cap 8 */
    CHECK_EQ_INT(da_cap(&a), 8, "at capacity");
    v = 42;
    CHECK_EQ_INT(da_insert(&a, 4, &v), 0, "insert forces growth");
    CHECK_EQ_INT(da_cap(&a), 16, "grew to 16 on insert");
    int *p = da_get(&a, 4);
    CHECK(p != NULL && *p == 42, "inserted value in place after growth");
    CHECK_EQ_INT(da_len(&a), 9, "length 9 after insert");
    da_free(&a);
}

TEST(struct_elements_copy_by_value) {
    dynarray a;
    CHECK_EQ_INT(da_init(&a, sizeof(pick_line)), 0, "init struct array");
    pick_line tmp = mk(1, "BX-04", 2.5);
    CHECK_EQ_INT(da_push(&a, &tmp), 0, "push struct");
    tmp = mk(2, "BX-09", 1.0);
    CHECK_EQ_INT(da_push(&a, &tmp), 0, "push second struct");
    tmp.id = 999; /* caller's copy changes after push... */
    memset(tmp.code, 'Z', 4);
    pick_line *p0 = da_get(&a, 0);
    pick_line *p1 = da_get(&a, 1);
    CHECK(p0 != NULL && p1 != NULL, "both structs reachable");
    if (p0 && p1) {
        CHECK_EQ_INT(p0->id, 1, "...but the array copy is untouched");
        CHECK_EQ_STR(p0->code, "BX-04", "code copied byte-for-byte");
        CHECK(p0->qty == 2.5, "qty copied");
        CHECK_EQ_INT(p1->id, 2, "second element id");
        CHECK_EQ_STR(p1->code, "BX-09", "second element code");
    }
    tmp = mk(3, "BX-01", 9.0);
    CHECK_EQ_INT(da_insert(&a, 1, &tmp), 0, "insert struct in the middle");
    pick_line got;
    CHECK_EQ_INT(da_pop(&a, &got), 0, "pop struct into caller buffer");
    CHECK_EQ_INT(got.id, 2, "popped the shifted last element");
    CHECK_EQ_STR(got.code, "BX-09", "popped bytes intact");
    p1 = da_get(&a, 1);
    CHECK(p1 != NULL && p1->id == 3, "inserted struct now at index 1");
    da_free(&a);
}

TEST(get_is_a_live_pointer) {
    dynarray a;
    CHECK_EQ_INT(da_init(&a, sizeof(int)), 0, "init");
    int v = 5;
    da_push(&a, &v);
    int *p = da_get(&a, 0);
    CHECK(p != NULL, "pointer to element");
    if (p)
        *p = 77; /* mutate through the pointer */
    p = da_get(&a, 0);
    CHECK(p != NULL && *p == 77, "mutation is visible in the array");
    CHECK(da_get(&a, 1) == NULL, "index == len is NULL");
    CHECK(da_get(&a, 100) == NULL, "index past len is NULL");
    da_free(&a);
}

TEST(free_resets_and_is_reusable) {
    dynarray a;
    CHECK_EQ_INT(da_init(&a, sizeof(int)), 0, "init");
    for (int v = 0; v < 20; v++)
        da_push(&a, &v);
    da_free(&a);
    CHECK_EQ_INT(da_len(&a), 0, "len is 0 after free");
    CHECK_EQ_INT(da_cap(&a), 0, "cap is 0 after free");
    CHECK(da_get(&a, 0) == NULL, "no elements after free");
    da_free(&a); /* double free must be harmless */
    CHECK_EQ_INT(da_init(&a, sizeof(int)), 0, "re-init after free");
    int v = 3;
    CHECK_EQ_INT(da_push(&a, &v), 0, "array usable again");
    CHECK_EQ_INT(da_len(&a), 1, "one element after reuse");
    da_free(&a);
}

int main(void) {
    RUN(init_starts_empty_with_no_buffer);
    RUN(init_rejects_zero_elem_size);
    RUN(push_grows_geometrically);
    RUN(pop_returns_last_and_never_shrinks);
    RUN(insert_shifts_elements);
    RUN(insert_into_empty_and_growth_boundary);
    RUN(struct_elements_copy_by_value);
    RUN(get_is_a_live_pointer);
    RUN(free_resets_and_is_reusable);
    return mt_summary();
}
