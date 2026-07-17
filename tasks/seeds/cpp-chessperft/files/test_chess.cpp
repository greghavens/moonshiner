/* Acceptance suite for the chess move generator.
 * Every node count below was cross-checked between two independent
 * implementations and, where available, the standard published perft
 * tables; they are exact and not negotiable. */
#include <algorithm>
#include <string>
#include <vector>

#include "mintest.h"
#include "chess.h"

static bool has_move(const Chess *c, const char *uci) {
    std::vector<std::string> ms = chess_moves(c);
    return std::find(ms.begin(), ms.end(), std::string(uci)) != ms.end();
}

static void check_counts(const char *fen, const long long *want, int depths,
                         const char *label) {
    Chess c;
    CHECK(chess_load(&c, fen), "fixture FEN loads");
    for (int d = 1; d <= depths; d++)
        CHECK_EQ_INT(chess_perft(&c, d), want[d - 1], label);
}

TEST(fen_validation) {
    Chess c;
    CHECK(chess_load(&c,
        "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"),
        "the start position loads");
    CHECK(!chess_load(&c, "banana"), "garbage rejected");
    CHECK(!chess_load(&c, ""), "empty string rejected");
    CHECK(!chess_load(&c, "8/8/8/8/8/8/8 w - - 0 1"),
          "seven ranks rejected");
    CHECK(!chess_load(&c, "8/8/x7/8/8/8/8/8 w - - 0 1"),
          "unknown piece letter rejected");
    CHECK(!chess_load(&c, "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR z KQkq - 0 1"),
          "bad side-to-move rejected");
    CHECK(!chess_load(&c, "rnbqkbnr/ppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"),
          "a rank that does not sum to eight files rejected");
}

TEST(startpos_move_list) {
    Chess c;
    CHECK(chess_load(&c,
        "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"),
        "start position loads");
    std::vector<std::string> ms = chess_moves(&c);
    CHECK_EQ_INT((int)ms.size(), 20, "twenty legal moves at the start");
    CHECK_EQ_STR(ms.front().c_str(), "a2a3", "list is sorted: first move");
    CHECK_EQ_STR(ms.back().c_str(), "h2h4", "list is sorted: last move");
    CHECK(has_move(&c, "e2e4"), "the king's pawn is in the list");
    CHECK(has_move(&c, "g1f3"), "knight development is in the list");
    CHECK(!has_move(&c, "e1g1"), "no castling through your own pieces");
}

TEST(apply_and_reply_counts) {
    Chess c;
    CHECK(chess_load(&c,
        "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"),
        "start position loads");
    CHECK(!chess_apply(&c, "e2e5"), "an illegal move is refused");
    CHECK(!chess_apply(&c, "junk"), "nonsense is refused");
    CHECK_EQ_INT(chess_perft(&c, 1), 20, "the refusal changed nothing");
    CHECK(chess_apply(&c, "e2e4"), "1. e4 applies");
    CHECK_EQ_INT(chess_perft(&c, 1), 20, "black has twenty replies");
    CHECK(chess_apply(&c, "e7e5"), "1... e5 applies");
    CHECK_EQ_INT(chess_perft(&c, 1), 29, "white has twenty-nine moves now");
}

TEST(perft_startpos) {
    static const long long want[] = {20, 400, 8902, 197281};
    Chess c;
    CHECK(chess_load(&c,
        "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"),
        "start position loads");
    CHECK_EQ_INT(chess_perft(&c, 0), 1, "depth 0 is one node");
    CHECK_EQ_INT(chess_perft(&c, -3), 0, "negative depth is zero nodes");
    for (int d = 1; d <= 4; d++)
        CHECK_EQ_INT(chess_perft(&c, d), want[d - 1],
                     "start position node count");
}

TEST(perft_castling_playground) {
    /* The classic castle/EP/promotion stress position. */
    static const long long want[] = {48, 2039, 97862};
    check_counts(
        "r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq - 0 1",
        want, 3, "castling-playground node count");
    Chess c;
    CHECK(chess_load(&c,
        "r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq - 0 1"),
        "position loads");
    CHECK(has_move(&c, "e1g1"), "white can castle short");
    CHECK(has_move(&c, "e1c1"), "white can castle long");
}

TEST(perft_pin_endgame) {
    static const long long want[] = {14, 191, 2812, 43238};
    check_counts("8/2p5/3p4/KP5r/1R3p1k/8/4P1P1/8 w - - 0 1",
                 want, 4, "pinned-endgame node count");
}

TEST(perft_promotion_storm) {
    static const long long want[] = {36, 1669, 54047, 2519192};
    check_counts("r3k2r/Pppp1ppp/1b3nbN/nP6/BBP1P3/q4N2/Pp1P2PK/R6b w kq - 0 1",
                 want, 4, "promotion-storm node count");
}

TEST(perft_underpromotion_check) {
    static const long long want[] = {44, 1486, 62379, 2103487};
    check_counts("rnbq1k1r/pp1Pbppp/2p5/8/2B5/8/PPP1NnPP/RNBQK2R w KQ - 1 8",
                 want, 4, "underpromotion-check node count");
}

TEST(perft_symmetrical_middle) {
    static const long long want[] = {46, 2079, 89890};
    check_counts(
        "r4rk1/1pp1qppp/p1np1n2/2b1p1B1/2B1P1b1/P1NP1N2/1PP1QPPP/R4RK1 w - - 0 10",
        want, 3, "symmetrical-middlegame node count");
}

TEST(en_passant_laws) {
    Chess c;
    /* Rank pin: taking en passant would strip both pawns off the fourth
     * rank and leave the king staring at the queen. */
    CHECK(chess_load(&c, "8/8/8/8/k2Pp2Q/8/8/4K3 b - d3 0 1"),
          "EP-pin position loads");
    CHECK(!has_move(&c, "e4d3"), "the en passant capture is illegal here");
    CHECK(has_move(&c, "e4e3"), "the plain push is fine");
    CHECK(!chess_apply(&c, "e4d3"), "applying it is refused too");

    /* Only the pawn that just moved may be taken. */
    CHECK(chess_load(&c,
        "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"),
        "start position loads");
    CHECK(chess_apply(&c, "e2e4"), "1. e4");
    CHECK(chess_apply(&c, "d7d5"), "1... d5");
    CHECK(chess_apply(&c, "e4e5"), "2. e5");
    CHECK(chess_apply(&c, "f7f5"), "2... f5");
    CHECK(has_move(&c, "e5f6"), "the fresh double-push can be taken");
    CHECK(!has_move(&c, "e5d6"), "the stale one cannot");
    CHECK(chess_apply(&c, "e5f6"), "capturing en passant applies");
}

TEST(castling_through_attack) {
    Chess c;
    CHECK(chess_load(&c, "4k3/8/8/8/8/8/5r2/R3K2R w KQ - 0 1"),
          "rook-on-f2 position loads");
    CHECK(!has_move(&c, "e1g1"), "cannot castle across the attacked f1");
    CHECK(has_move(&c, "e1c1"), "the long side is untouched");
}

TEST(promotion_choices) {
    Chess c;
    CHECK(chess_load(&c, "8/P6k/8/8/8/8/7K/8 w - - 0 1"),
          "promotion position loads");
    std::vector<std::string> ms = chess_moves(&c);
    CHECK_EQ_INT((int)ms.size(), 9, "five king moves plus four promotions");
    CHECK(has_move(&c, "a7a8q"), "queening is offered");
    CHECK(has_move(&c, "a7a8n"), "underpromotion to a knight is offered");
    CHECK(has_move(&c, "a7a8r"), "to a rook");
    CHECK(has_move(&c, "a7a8b"), "and to a bishop");
    CHECK(!has_move(&c, "a7a8"), "a bare pawn move to the last rank is not");
    CHECK(chess_apply(&c, "a7a8n"), "underpromotion applies");
}

int main(void) {
    RUN(fen_validation);
    RUN(startpos_move_list);
    RUN(apply_and_reply_counts);
    RUN(perft_startpos);
    RUN(perft_castling_playground);
    RUN(perft_pin_endgame);
    RUN(perft_promotion_storm);
    RUN(perft_underpromotion_check);
    RUN(perft_symmetrical_middle);
    RUN(en_passant_laws);
    RUN(castling_through_attack);
    RUN(promotion_choices);
    return mt_summary();
}
