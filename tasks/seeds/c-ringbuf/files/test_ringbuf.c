/* Acceptance tests for ringbuf.h / ringbuf.c — the fixed-capacity byte
 * ring between the scanner reader and the uploader.
 * Build and run with `make test`.
 */
#include "mintest.h"
#include "ringbuf.h"

TEST(init_and_argument_checks) {
    unsigned char store[8];
    ringbuf rb;
    CHECK_EQ_INT(rb_init(&rb, store, sizeof store), 0, "init ok");
    CHECK_EQ_INT(rb_len(&rb), 0, "starts empty");
    CHECK_EQ_INT(rb_avail(&rb), 8, "all capacity free");
    CHECK_EQ_INT(rb_high(&rb), 0, "watermark starts at zero");
    CHECK_EQ_INT(rb_init(&rb, NULL, 8), -1, "NULL storage rejected");
    CHECK_EQ_INT(rb_init(&rb, store, 0), -1, "zero capacity rejected");
}

TEST(write_then_read_back) {
    unsigned char store[8], out[16];
    ringbuf rb;
    rb_init(&rb, store, sizeof store);
    CHECK_EQ_INT(rb_write(&rb, "abc", 3), 3, "three bytes accepted");
    CHECK_EQ_INT(rb_len(&rb), 3, "three stored");
    CHECK_EQ_INT(rb_avail(&rb), 5, "five free");
    CHECK_EQ_INT(rb_read(&rb, out, 3), 3, "three bytes out");
    CHECK(memcmp(out, "abc", 3) == 0, "bytes come back in order");
    CHECK_EQ_INT(rb_len(&rb), 0, "empty after read");
    CHECK_EQ_INT(rb_avail(&rb), 8, "capacity fully free again");
}

TEST(fifo_order_across_wraparound) {
    unsigned char store[8], out[16];
    ringbuf rb;
    rb_init(&rb, store, sizeof store);
    CHECK_EQ_INT(rb_write(&rb, "abcdef", 6), 6, "six in");
    CHECK_EQ_INT(rb_read(&rb, out, 4), 4, "four out");
    CHECK(memcmp(out, "abcd", 4) == 0, "first four bytes");
    CHECK_EQ_INT(rb_write(&rb, "ghijk", 5), 5,
                 "five more fit (write wraps past the end)");
    CHECK_EQ_INT(rb_len(&rb), 7, "seven stored");
    CHECK_EQ_INT(rb_read(&rb, out, 7), 7, "drain (read wraps too)");
    CHECK(memcmp(out, "efghijk", 7) == 0, "FIFO order survives the wrap");
    CHECK_EQ_INT(rb_len(&rb), 0, "empty after drain");
}

TEST(write_accepts_only_what_fits) {
    unsigned char store[8], out[16];
    ringbuf rb;
    rb_init(&rb, store, sizeof store);
    CHECK_EQ_INT(rb_write(&rb, "0123456789", 10), 8,
                 "ten offered, eight accepted");
    CHECK_EQ_INT(rb_len(&rb), 8, "buffer holds all eight slots");
    CHECK_EQ_INT(rb_avail(&rb), 0, "no room left");
    CHECK_EQ_INT(rb_write(&rb, "x", 1), 0, "full buffer accepts nothing");
    CHECK_EQ_INT(rb_read(&rb, out, 3), 3, "make some room");
    CHECK(memcmp(out, "012", 3) == 0, "the accepted prefix was kept");
    CHECK_EQ_INT(rb_write(&rb, "abcde", 5), 3,
                 "five offered, three accepted");
    CHECK_EQ_INT(rb_read(&rb, out, 16), 8, "read returns what is stored");
    CHECK(memcmp(out, "34567abc", 8) == 0,
          "partial write stored the prefix only");
}

TEST(full_then_empty_then_reusable) {
    unsigned char store[4], out[8];
    ringbuf rb;
    rb_init(&rb, store, sizeof store);
    CHECK_EQ_INT(rb_write(&rb, "wxyz", 4), 4, "fill to capacity");
    CHECK_EQ_INT(rb_len(&rb), 4, "count equals capacity when full");
    CHECK_EQ_INT(rb_avail(&rb), 0, "full means zero available");
    CHECK_EQ_INT(rb_read(&rb, out, 4), 4, "drain completely");
    CHECK_EQ_INT(rb_len(&rb), 0, "count zero when empty");
    CHECK_EQ_INT(rb_avail(&rb), 4, "empty means all available");
    CHECK_EQ_INT(rb_write(&rb, "12", 2), 2, "usable after full/empty cycle");
    CHECK_EQ_INT(rb_read(&rb, out, 2), 2, "and reads fine");
    CHECK(memcmp(out, "12", 2) == 0, "fresh data intact");
}

TEST(peek_does_not_consume) {
    unsigned char store[8], a[8], b[8];
    ringbuf rb;
    rb_init(&rb, store, sizeof store);
    rb_write(&rb, "abcdef", 6);
    rb_read(&rb, a, 5); /* advance head to 5 so the next data wraps */
    rb_write(&rb, "ghij", 4);
    CHECK_EQ_INT(rb_len(&rb), 5, "five stored around the seam");
    CHECK_EQ_INT(rb_peek(&rb, a, 5), 5, "peek all five");
    CHECK(memcmp(a, "fghij", 5) == 0, "peek sees the wrap seam correctly");
    CHECK_EQ_INT(rb_len(&rb), 5, "peek consumed nothing");
    CHECK_EQ_INT(rb_peek(&rb, b, 3), 3, "short peek");
    CHECK(memcmp(b, "fgh", 3) == 0, "short peek is a prefix");
    CHECK_EQ_INT(rb_peek(&rb, b, 8), 5, "peek past count returns count");
    CHECK_EQ_INT(rb_read(&rb, b, 5), 5, "read after peek");
    CHECK(memcmp(b, "fghij", 5) == 0, "read matches what peek promised");
}

TEST(zero_length_ops_are_noops) {
    unsigned char store[4], out[4];
    ringbuf rb;
    rb_init(&rb, store, sizeof store);
    rb_write(&rb, "ab", 2);
    CHECK_EQ_INT(rb_write(&rb, "zz", 0), 0, "zero-byte write accepts zero");
    CHECK_EQ_INT(rb_read(&rb, out, 0), 0, "zero-byte read returns zero");
    CHECK_EQ_INT(rb_peek(&rb, out, 0), 0, "zero-byte peek returns zero");
    CHECK_EQ_INT(rb_len(&rb), 2, "nothing changed");
    CHECK_EQ_INT(rb_high(&rb), 2, "watermark unchanged by no-ops");
}

TEST(binary_bytes_pass_through) {
    unsigned char store[8], out[8];
    const unsigned char frame[] = {0x00, 0xFF, 0x7F, 0x0A, 0x00, 0x1B};
    ringbuf rb;
    rb_init(&rb, store, sizeof store);
    CHECK_EQ_INT(rb_write(&rb, frame, 6), 6, "binary frame accepted");
    CHECK_EQ_INT(rb_read(&rb, out, 6), 6, "binary frame read");
    CHECK(memcmp(out, frame, 6) == 0, "NUL and high bytes are just bytes");
}

TEST(high_watermark_tracks_peak_count) {
    unsigned char store[8], out[8];
    ringbuf rb;
    rb_init(&rb, store, sizeof store);
    rb_write(&rb, "abc", 3);
    CHECK_EQ_INT(rb_high(&rb), 3, "watermark follows the first burst");
    rb_read(&rb, out, 2);
    CHECK_EQ_INT(rb_high(&rb), 3, "draining does not lower it");
    rb_write(&rb, "defghi", 6);
    CHECK_EQ_INT(rb_len(&rb), 7, "seven stored now");
    CHECK_EQ_INT(rb_high(&rb), 7, "new peak recorded");
    rb_read(&rb, out, 7);
    rb_write(&rb, "x", 1);
    CHECK_EQ_INT(rb_high(&rb), 7, "smaller loads keep the old peak");
    rb_write(&rb, "yzabcde", 7);
    CHECK_EQ_INT(rb_len(&rb), 8, "topped up to capacity");
    CHECK_EQ_INT(rb_high(&rb), 8, "peak can reach full capacity");
}

TEST(clear_empties_but_keeps_the_watermark) {
    unsigned char store[8], out[8];
    ringbuf rb;
    rb_init(&rb, store, sizeof store);
    rb_write(&rb, "abcdef", 6);
    rb_clear(&rb);
    CHECK_EQ_INT(rb_len(&rb), 0, "clear empties the ring");
    CHECK_EQ_INT(rb_avail(&rb), 8, "clear frees all capacity");
    CHECK_EQ_INT(rb_high(&rb), 6, "clear keeps the high watermark");
    CHECK_EQ_INT(rb_read(&rb, out, 4), 0, "nothing to read after clear");
    CHECK_EQ_INT(rb_write(&rb, "pq", 2), 2, "ring usable after clear");
    CHECK_EQ_INT(rb_read(&rb, out, 2), 2, "reads work after clear");
    CHECK(memcmp(out, "pq", 2) == 0, "post-clear data intact");
    CHECK_EQ_INT(rb_high(&rb), 6, "small post-clear loads keep the peak");
}

TEST(two_rings_do_not_share_state) {
    unsigned char sa[4], sb[4], out[4];
    ringbuf a, b;
    rb_init(&a, sa, sizeof sa);
    rb_init(&b, sb, sizeof sb);
    rb_write(&a, "AA", 2);
    rb_write(&b, "b", 1);
    CHECK_EQ_INT(rb_len(&a), 2, "ring a has its own count");
    CHECK_EQ_INT(rb_len(&b), 1, "ring b has its own count");
    rb_read(&a, out, 2);
    CHECK_EQ_INT(rb_len(&b), 1, "draining a leaves b alone");
    CHECK_EQ_INT(rb_high(&a), 2, "ring a watermark");
    CHECK_EQ_INT(rb_high(&b), 1, "ring b watermark");
}

TEST(sustained_churn_stays_in_order) {
    unsigned char store[8], out[8];
    ringbuf rb;
    rb_init(&rb, store, sizeof store);
    unsigned char next_in = 0, next_out = 0;
    for (int round = 0; round < 100; round++) {
        unsigned char chunk[5];
        for (int i = 0; i < 5; i++)
            chunk[i] = next_in++;
        CHECK_EQ_INT(rb_write(&rb, chunk, 5), 5, "chunk fits");
        CHECK_EQ_INT(rb_read(&rb, out, 5), 5, "chunk drains");
        for (int i = 0; i < 5; i++) {
            if (out[i] != next_out) {
                CHECK(0, "byte stream stays FIFO through many wraps");
                return;
            }
            next_out++;
        }
    }
    CHECK_EQ_INT(rb_len(&rb), 0, "balanced churn ends empty");
    CHECK_EQ_INT(rb_high(&rb), 5, "steady 5-byte bursts peak at 5");
}

int main(void) {
    RUN(init_and_argument_checks);
    RUN(write_then_read_back);
    RUN(fifo_order_across_wraparound);
    RUN(write_accepts_only_what_fits);
    RUN(full_then_empty_then_reusable);
    RUN(peek_does_not_consume);
    RUN(zero_length_ops_are_noops);
    RUN(binary_bytes_pass_through);
    RUN(high_watermark_tracks_peak_count);
    RUN(clear_empties_but_keeps_the_watermark);
    RUN(two_rings_do_not_share_state);
    RUN(sustained_churn_stays_in_order);
    return mt_summary();
}
