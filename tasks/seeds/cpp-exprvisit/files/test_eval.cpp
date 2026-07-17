/* Acceptance tests for the kiosk rule evaluator (eval.hpp/eval.cpp).
 * Build and run with `make test`.
 *
 * Contract pinned here:
 *   - typed values (bool / long / std::string) with strict, no-coercion
 *     operators; every misuse yields an EvalError alternative, never a
 *     crash and never a silently coerced value;
 *   - evaluation order is left-to-right and the FIRST error wins;
 *   - "and"/"or" short-circuit: a skipped right-hand side is never
 *     evaluated, so its errors do not surface;
 *   - the same expression tree can be evaluated under different
 *     environments and always answers from the env it was given.
 */
#include "mintest.h"

#include "eval.hpp"

#include <string>
#include <type_traits>
#include <variant>

/* Render an Outcome compactly for byte-exact comparisons. */
static std::string show(const Outcome &o) {
    if (std::holds_alternative<EvalError>(o)) {
        const EvalError &e = std::get<EvalError>(o);
        return "err:" + e.code + "(" + e.detail + ")";
    }
    const Value &v = std::get<Value>(o);
    return std::visit(
            [](const auto &x) -> std::string {
                using T = std::decay_t<decltype(x)>;
                if constexpr (std::is_same_v<T, bool>)
                    return x ? "bool:true" : "bool:false";
                else if constexpr (std::is_same_v<T, long>)
                    return "long:" + std::to_string(x);
                else
                    return "str:" + x;
            },
            v);
}

#define CHECK_EVAL(expr, env_, want) do {                                   \
        const std::string mt_line = show(eval((expr), (env_)));             \
        CHECK_EQ_STR(mt_line.c_str(), (want), "evaluates to " want);        \
    } while (0)

static Env kiosk() {
    return {
        {"qty", Value(12L)},   {"unit", Value(-7L)},
        {"zero", Value(0L)},   {"vip", Value(true)},
        {"name", Value(std::string("kiosk-3"))},
        {"tier", Value(std::string("gold"))},
    };
}

TEST(literals_evaluate_to_themselves) {
    Env e = kiosk();
    CHECK_EVAL(lit(7L), e, "long:7");
    CHECK_EVAL(lit(true), e, "bool:true");
    CHECK_EVAL(lit(false), e, "bool:false");
    CHECK_EVAL(lit(std::string("ok")), e, "str:ok");
}

TEST(refs_read_the_environment) {
    Env e = kiosk();
    CHECK_EVAL(ref("qty"), e, "long:12");
    CHECK_EVAL(ref("vip"), e, "bool:true");
    CHECK_EVAL(ref("tier"), e, "str:gold");
    CHECK_EVAL(ref("missing"), e, "err:undef(missing)");
}

TEST(arithmetic_on_longs) {
    Env e = kiosk();
    CHECK_EVAL(bin("+", lit(2L), lit(3L)), e, "long:5");
    CHECK_EVAL(bin("-", lit(2L), lit(9L)), e, "long:-7");
    CHECK_EVAL(bin("*", ref("qty"), ref("unit")), e, "long:-84");
    CHECK_EVAL(bin("*", bin("+", ref("qty"), ref("unit")), lit(2L)), e,
               "long:10");
}

TEST(division_truncates_toward_zero_and_flags_zero) {
    Env e = kiosk();
    CHECK_EVAL(bin("/", lit(7L), lit(2L)), e, "long:3");
    CHECK_EVAL(bin("/", lit(-7L), lit(2L)), e, "long:-3");
    CHECK_EVAL(bin("/", lit(7L), lit(-2L)), e, "long:-3");
    CHECK_EVAL(bin("/", lit(1L), lit(0L)), e, "err:div0()");
    CHECK_EVAL(bin("/", ref("qty"), ref("zero")), e, "err:div0()");
}

TEST(plus_concatenates_strings_but_never_mixes) {
    Env e = kiosk();
    CHECK_EVAL(bin("+", ref("name"), lit(std::string("-live"))), e,
               "str:kiosk-3-live");
    CHECK_EVAL(bin("+", ref("name"), lit(3L)), e, "err:type(+)");
    CHECK_EVAL(bin("+", lit(3L), ref("tier")), e, "err:type(+)");
    CHECK_EVAL(bin("+", lit(true), lit(true)), e, "err:type(+)");
}

TEST(arithmetic_rejects_non_longs) {
    Env e = kiosk();
    CHECK_EVAL(bin("-", lit(true), lit(1L)), e, "err:type(-)");
    CHECK_EVAL(bin("*", ref("tier"), lit(2L)), e, "err:type(*)");
    CHECK_EVAL(bin("/", lit(4L), lit(std::string("2"))), e, "err:type(/)");
}

TEST(equality_within_one_type_only) {
    Env e = kiosk();
    CHECK_EVAL(bin("==", ref("qty"), lit(12L)), e, "bool:true");
    CHECK_EVAL(bin("!=", lit(12L), ref("unit")), e, "bool:true");
    CHECK_EVAL(bin("==", ref("tier"), lit(std::string("gold"))), e,
               "bool:true");
    CHECK_EVAL(bin("==", ref("vip"), lit(true)), e, "bool:true");
    CHECK_EVAL(bin("!=", ref("vip"), lit(true)), e, "bool:false");
    CHECK_EVAL(bin("==", lit(1L), lit(std::string("1"))), e, "err:type(==)");
    CHECK_EVAL(bin("!=", lit(true), lit(0L)), e, "err:type(!=)");
}

TEST(less_than_orders_longs_and_strings) {
    Env e = kiosk();
    CHECK_EVAL(bin("<", lit(3L), lit(5L)), e, "bool:true");
    CHECK_EVAL(bin("<", lit(5L), lit(3L)), e, "bool:false");
    CHECK_EVAL(bin("<", lit(std::string("apple")), lit(std::string("pear"))),
               e, "bool:true");
    CHECK_EVAL(bin("<", lit(true), lit(false)), e, "err:type(<)");
    CHECK_EVAL(bin("<", ref("qty"), ref("tier")), e, "err:type(<)");
}

TEST(logic_wants_bools_on_both_sides_it_evaluates) {
    Env e = kiosk();
    CHECK_EVAL(bin("and", ref("vip"), lit(true)), e, "bool:true");
    CHECK_EVAL(bin("and", ref("vip"), lit(false)), e, "bool:false");
    CHECK_EVAL(bin("or", lit(false), lit(false)), e, "bool:false");
    CHECK_EVAL(bin("or", lit(false), ref("vip")), e, "bool:true");
    CHECK_EVAL(bin("and", lit(1L), lit(true)), e, "err:type(and)");
    CHECK_EVAL(bin("or", ref("name"), lit(true)), e, "err:type(or)");
    CHECK_EVAL(bin("and", lit(true), lit(3L)), e, "err:type(and)");
}

TEST(logic_short_circuits_past_broken_right_sides) {
    Env e = kiosk();
    CHECK_EVAL(bin("or", lit(true), ref("missing")), e, "bool:true");
    CHECK_EVAL(bin("or", lit(true), bin("/", lit(1L), lit(0L))), e,
               "bool:true");
    CHECK_EVAL(bin("and", lit(false), ref("missing")), e, "bool:false");
    CHECK_EVAL(bin("and", lit(false), bin("+", lit(true), lit(1L))), e,
               "bool:false");
    /* ...but an evaluated right side still reports its error */
    CHECK_EVAL(bin("and", lit(true), ref("missing")), e,
               "err:undef(missing)");
    CHECK_EVAL(bin("or", lit(false), bin("/", lit(1L), lit(0L))), e,
               "err:div0()");
}

TEST(unary_minus_and_not) {
    Env e = kiosk();
    CHECK_EVAL(un('-', lit(5L)), e, "long:-5");
    CHECK_EVAL(un('-', ref("unit")), e, "long:7");
    CHECK_EVAL(un('!', lit(false)), e, "bool:true");
    CHECK_EVAL(un('!', ref("vip")), e, "bool:false");
    CHECK_EVAL(un('-', ref("tier")), e, "err:type(-)");
    CHECK_EVAL(un('!', lit(0L)), e, "err:type(!)");
    CHECK_EVAL(un('-', ref("nope")), e, "err:undef(nope)");
}

TEST(first_error_wins_left_to_right) {
    Env e = kiosk();
    CHECK_EVAL(bin("+", ref("missing"), bin("/", lit(1L), lit(0L))), e,
               "err:undef(missing)");
    CHECK_EVAL(bin("*", bin("/", lit(1L), lit(0L)), ref("missing")), e,
               "err:div0()");
    CHECK_EVAL(bin("+", lit(1L), ref("nope")), e, "err:undef(nope)");
    CHECK_EVAL(un('!', bin("/", lit(1L), lit(0L))), e, "err:div0()");
}

TEST(unknown_operators_are_type_errors) {
    Env e = kiosk();
    CHECK_EVAL(bin("%", lit(1L), lit(2L)), e, "err:type(%)");
    CHECK_EVAL(un('~', lit(1L)), e, "err:type(~)");
}

TEST(a_realistic_rule_reads_like_a_rule) {
    Env e = kiosk();
    /* (vip and qty * unit < 0) or tier == "gold" */
    ExprPtr rule = bin(
            "or",
            bin("and", ref("vip"),
                bin("<", bin("*", ref("qty"), ref("unit")), lit(0L))),
            bin("==", ref("tier"), lit(std::string("gold"))));
    CHECK_EVAL(rule, e, "bool:true");
    Env e2 = kiosk();
    e2["vip"] = Value(false);
    e2["tier"] = Value(std::string("bronze"));
    CHECK_EVAL(rule, e2, "bool:false");
}

TEST(one_tree_many_environments) {
    ExprPtr expr = bin("*", ref("qty"), lit(3L));
    Env a = kiosk();
    Env b = kiosk();
    b["qty"] = Value(2L);
    CHECK_EVAL(expr, a, "long:36");
    CHECK_EVAL(expr, b, "long:6");
    CHECK_EVAL(expr, a, "long:36");
    Env c;
    CHECK_EVAL(expr, c, "err:undef(qty)");
}

int main(void) {
    RUN(literals_evaluate_to_themselves);
    RUN(refs_read_the_environment);
    RUN(arithmetic_on_longs);
    RUN(division_truncates_toward_zero_and_flags_zero);
    RUN(plus_concatenates_strings_but_never_mixes);
    RUN(arithmetic_rejects_non_longs);
    RUN(equality_within_one_type_only);
    RUN(less_than_orders_longs_and_strings);
    RUN(logic_wants_bools_on_both_sides_it_evaluates);
    RUN(logic_short_circuits_past_broken_right_sides);
    RUN(unary_minus_and_not);
    RUN(first_error_wins_left_to_right);
    RUN(unknown_operators_are_type_errors);
    RUN(a_realistic_rule_reads_like_a_rule);
    RUN(one_tree_many_environments);
    return mt_summary();
}
