/* Acceptance suite for the deterministic deck toolkit.
 * Deck orders are pinned exactly — the club replays deals from seeds. */
#include "mintest.h"
#include "cardshuf.h"

#define NEW_ORDER \
    "2C 3C 4C 5C 6C 7C 8C 9C TC JC QC KC AC " \
    "2D 3D 4D 5D 6D 7D 8D 9D TD JD QD KD AD " \
    "2H 3H 4H 5H 6H 7H 8H 9H TH JH QH KH AH " \
    "2S 3S 4S 5S 6S 7S 8S 9S TS JS QS KS AS"

/* render the top k cards (or the whole deck) as "XX YY ZZ" */
static const char *deck_str(const deck *d, int k) {
    static char buf[256];
    char nm[3];
    int i, pos = 0;
    if (k < 0 || k > d->n) k = d->n;
    buf[0] = '\0';
    for (i = 0; i < k; i++) {
        if (card_name(d->cards[i], nm) != 0) return "(bad card)";
        if (i) buf[pos++] = ' ';
        buf[pos++] = nm[0];
        buf[pos++] = nm[1];
    }
    buf[pos] = '\0';
    return buf;
}

static int is_permutation(const deck *d) {
    int seen[52] = {0}, i;
    if (d->n != 52) return 0;
    for (i = 0; i < 52; i++) {
        if (d->cards[i] < 0 || d->cards[i] > 51 || seen[d->cards[i]]) return 0;
        seen[d->cards[i]] = 1;
    }
    return 1;
}

TEST(names_and_new_deck) {
    char nm[3] = "..";
    deck d;
    CHECK_EQ_INT(card_name(0, nm), 0, "card 0 names fine");
    CHECK_EQ_STR(nm, "2C", "card 0 is the two of clubs");
    card_name(12, nm);
    CHECK_EQ_STR(nm, "AC", "card 12 is the ace of clubs");
    card_name(13, nm);
    CHECK_EQ_STR(nm, "2D", "card 13 is the two of diamonds");
    card_name(21, nm);
    CHECK_EQ_STR(nm, "TD", "card 21 is the ten of diamonds");
    card_name(51, nm);
    CHECK_EQ_STR(nm, "AS", "card 51 is the ace of spades");
    strcpy(nm, "zz");
    CHECK_EQ_INT(card_name(52, nm), -1, "card 52 is rejected");
    CHECK_EQ_STR(nm, "zz", "rejected name leaves the buffer alone");
    CHECK_EQ_INT(card_name(-1, nm), -1, "negative card is rejected");
    deck_new(&d);
    CHECK_EQ_INT(d.n, 52, "new deck holds 52 cards");
    CHECK_EQ_STR(deck_str(&d, -1), NEW_ORDER, "new deck order is canonical");
}

TEST(house_prng) {
    uint32_t s = 1;
    CHECK_EQ_INT(shuf_next(&s), 16838, "seed 1 draw 1");
    CHECK_EQ_INT(shuf_next(&s), 5758, "seed 1 draw 2");
    CHECK_EQ_INT(shuf_next(&s), 10113, "seed 1 draw 3");
    CHECK_EQ_INT(shuf_next(&s), 17515, "seed 1 draw 4");
    CHECK_EQ_INT(shuf_next(&s), 31051, "seed 1 draw 5");
    CHECK_EQ_INT(shuf_next(&s), 5627, "seed 1 draw 6");
    s = 42;
    CHECK_EQ_INT(shuf_next(&s), 19081, "seed 42 draw 1");
    CHECK_EQ_INT(shuf_next(&s), 17033, "seed 42 draw 2");
    CHECK_EQ_INT(shuf_next(&s), 15269, "seed 42 draw 3");
}

TEST(seeded_shuffle) {
    deck d, e;
    deck_new(&d);
    deck_shuffle(&d, 1);
    CHECK_EQ_INT(d.n, 52, "shuffle keeps 52 cards");
    CHECK(is_permutation(&d), "seed-1 shuffle is a permutation");
    CHECK_EQ_STR(deck_str(&d, -1),
        "8C AD 2C 4D 4S 4H AC 9C 2H 6H 6D 8H 7S AH QS 4C TD JH 5H 3D KS QD "
        "7H 8S TS KH 5C 3H 3S QH 7D JS KD KC TC 9H 7C AS JC 6C 5D 8D 3C 9D "
        "2S QC TH 6S JD 2D 9S 5S",
        "seed-1 shuffle order is pinned");
    deck_new(&e);
    deck_shuffle(&e, 1);
    {
        int i, same = 1;
        for (i = 0; i < 52; i++)
            if (d.cards[i] != e.cards[i]) same = 0;
        CHECK(same, "same seed reproduces the same deck");
    }
    deck_new(&d);
    deck_shuffle(&d, 42);
    CHECK(is_permutation(&d), "seed-42 shuffle is a permutation");
    CHECK_EQ_STR(deck_str(&d, 8), "6C 5S KD 4C 9S 7S 5C AH",
        "seed-42 top eight cards are pinned");
}

TEST(cutting) {
    deck d;
    deck_new(&d);
    CHECK_EQ_INT(deck_cut(&d, 26), 0, "cut at 26 accepted");
    CHECK_EQ_STR(deck_str(&d, 4), "2H 3H 4H 5H", "half cut brings hearts up");
    deck_new(&d);
    CHECK_EQ_INT(deck_cut(&d, 1), 0, "cut of one card accepted");
    CHECK_EQ_STR(deck_str(&d, 3), "3C 4C 5C", "single-card cut shifts the top");
    {
        char nm[3];
        card_name(d.cards[d.n - 1], nm);
        CHECK_EQ_STR(nm, "2C", "cut card lands on the bottom");
    }
    deck_new(&d);
    CHECK_EQ_INT(deck_cut(&d, 0), 0, "cut of zero is legal");
    CHECK_EQ_STR(deck_str(&d, -1), NEW_ORDER, "cut of zero changes nothing");
    CHECK_EQ_INT(deck_cut(&d, 52), 0, "cut of the whole deck is legal");
    CHECK_EQ_STR(deck_str(&d, -1), NEW_ORDER, "full cut changes nothing");
    CHECK_EQ_INT(deck_cut(&d, -1), -1, "negative cut rejected");
    CHECK_EQ_INT(deck_cut(&d, 53), -1, "oversized cut rejected");
    CHECK_EQ_STR(deck_str(&d, -1), NEW_ORDER, "rejected cuts change nothing");
}

TEST(riffling) {
    deck d;
    deck_new(&d);
    CHECK_EQ_INT(deck_riffle(&d), 0, "riffle accepted");
    CHECK_EQ_STR(deck_str(&d, 12), "2C 2H 3C 3H 4C 4H 5C 5H 6C 6H 7C 7H",
        "one riffle interleaves the halves");
    CHECK_EQ_INT(deck_riffle(&d), 0, "second riffle accepted");
    CHECK_EQ_INT(deck_riffle(&d), 0, "third riffle accepted");
    CHECK_EQ_STR(deck_str(&d, 12), "2C 8H 2D 8S 2H 9C 2S 9D 3C 9H 3D 9S",
        "three riffles land the pinned order");
    CHECK(is_permutation(&d), "riffling never loses a card");
    /* odd-length deck: the extra top-half card falls out last */
    d.n = 5;
    d.cards[0] = 0; d.cards[1] = 1; d.cards[2] = 2; d.cards[3] = 3; d.cards[4] = 4;
    CHECK_EQ_INT(deck_riffle(&d), 0, "odd riffle accepted");
    CHECK_EQ_STR(deck_str(&d, -1), "2C 5C 3C 6C 4C", "odd riffle order is pinned");
    d.n = 1;
    d.cards[0] = 7;
    CHECK_EQ_INT(deck_riffle(&d), 0, "one-card riffle is a no-op");
    CHECK_EQ_INT(d.cards[0], 7, "one-card deck unchanged");
}

TEST(round_robin_deal) {
    deck d;
    int hands[8][13];
    deck_new(&d);
    deck_shuffle(&d, 1);
    CHECK_EQ_INT(deck_deal(&d, 4, 5, 0, hands), 0, "4 players x 5 cards deals");
    {
        deck h;
        int p;
        const char *want[4] = {
            "8C 4S 2H 7S TD",
            "AD 4H 6H AH JH",
            "2C AC 6D QS 5H",
            "4D 9C 8H 4C 3D",
        };
        for (p = 0; p < 4; p++) {
            int r;
            h.n = 5;
            for (r = 0; r < 5; r++) h.cards[r] = hands[p][r];
            CHECK_EQ_STR(deck_str(&h, -1), want[p], "hand matches the pinned deal");
        }
    }
    CHECK_EQ_INT(d.n, 32, "twenty cards left the deck");
    CHECK_EQ_STR(deck_str(&d, 3), "KS QD 7H", "remaining deck shifts up");
}

TEST(burn_deal) {
    deck d;
    int hands[8][13];
    deck_new(&d);
    deck_shuffle(&d, 1);
    CHECK_EQ_INT(deck_deal(&d, 4, 5, 1, hands), 0, "burn deal accepted");
    {
        deck h;
        int p;
        const char *want[4] = {
            "AD AC 8H TD QD",
            "2C 9C 7S JH 7H",
            "4D 2H AH 5H 8S",
            "4S 6H QS 3D TS",
        };
        for (p = 0; p < 4; p++) {
            int r;
            h.n = 5;
            for (r = 0; r < 5; r++) h.cards[r] = hands[p][r];
            CHECK_EQ_STR(deck_str(&h, -1), want[p], "burn-deal hand is pinned");
        }
    }
    CHECK_EQ_INT(d.n, 27, "five burns plus twenty deals leave 27");
    {
        char nm[3];
        card_name(d.cards[0], nm);
        CHECK_EQ_STR(nm, "KH", "top of the remainder after burns");
    }
}

TEST(deal_validation) {
    deck d;
    int hands[8][13];
    deck_new(&d);
    CHECK_EQ_INT(deck_deal(&d, 0, 5, 0, hands), -1, "zero players rejected");
    CHECK_EQ_INT(deck_deal(&d, 9, 5, 0, hands), -1, "nine players rejected");
    CHECK_EQ_INT(deck_deal(&d, 4, 0, 0, hands), -1, "zero cards rejected");
    CHECK_EQ_INT(deck_deal(&d, 4, 14, 0, hands), -1, "fourteen cards rejected");
    CHECK_EQ_INT(deck_deal(&d, 8, 7, 0, hands), -1, "56 cards from 52 rejected");
    CHECK_EQ_INT(deck_deal(&d, 4, 13, 1, hands), -1,
                 "52 deals plus 13 burns rejected");
    CHECK_EQ_STR(deck_str(&d, -1), NEW_ORDER, "rejected deals change nothing");
    CHECK_EQ_INT(deck_deal(&d, 4, 13, 0, hands), 0, "dealing out the deck works");
    CHECK_EQ_INT(d.n, 0, "deck is empty after a full deal");
}

TEST(operation_sequence) {
    /* the disputed friday-night deal: shuffle seed 7, cut 10, riffle,
     * then heads-up 3 cards each with burns */
    deck d;
    int hands[8][13];
    deck_new(&d);
    deck_shuffle(&d, 7);
    CHECK_EQ_INT(deck_cut(&d, 10), 0, "cut 10 accepted");
    CHECK_EQ_INT(deck_riffle(&d), 0, "riffle accepted");
    CHECK_EQ_INT(deck_deal(&d, 2, 3, 1, hands), 0, "heads-up burn deal accepted");
    {
        deck h;
        int r;
        h.n = 3;
        for (r = 0; r < 3; r++) h.cards[r] = hands[0][r];
        CHECK_EQ_STR(deck_str(&h, -1), "2H 9H 6H", "player 0 replayed hand");
        for (r = 0; r < 3; r++) h.cards[r] = hands[1][r];
        CHECK_EQ_STR(deck_str(&h, -1), "4D 6D 7H", "player 1 replayed hand");
    }
    CHECK_EQ_INT(d.n, 43, "sequence leaves 43 cards");
    {
        char nm[3];
        card_name(d.cards[0], nm);
        CHECK_EQ_STR(nm, "AD", "top of the deck after the sequence");
    }
}

int main(void) {
    RUN(names_and_new_deck);
    RUN(house_prng);
    RUN(seeded_shuffle);
    RUN(cutting);
    RUN(riffling);
    RUN(round_robin_deal);
    RUN(burn_deal);
    RUN(deal_validation);
    RUN(operation_sequence);
    return mt_summary();
}
