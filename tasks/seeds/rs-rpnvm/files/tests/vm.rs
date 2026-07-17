// Acceptance tests for the alert-formula stack machine.
// Errors are asserted structurally: variant + token index + payload. Token
// indices are 0-based positions in the whitespace-split token list.

use rs_rpnvm::{Vm, VmError};

#[test]
fn basic_arithmetic() {
    let mut vm = Vm::new(16);
    assert_eq!(vm.eval("3 4 + 2 *"), Ok(14));
    assert_eq!(vm.eval("8 3 -"), Ok(5));
    assert_eq!(vm.eval("2 3 4 * +"), Ok(14));
    assert_eq!(vm.eval("10 2 /"), Ok(5));
    assert_eq!(vm.eval("9 2 %"), Ok(1));
}

#[test]
fn division_and_remainder_truncate_toward_zero() {
    let mut vm = Vm::new(16);
    assert_eq!(vm.eval("7 2 /"), Ok(3));
    assert_eq!(vm.eval("-7 2 /"), Ok(-3));
    assert_eq!(vm.eval("7 -2 /"), Ok(-3));
    assert_eq!(vm.eval("-7 -2 /"), Ok(3));
    assert_eq!(vm.eval("-7 2 %"), Ok(-1), "remainder takes the dividend's sign");
    assert_eq!(vm.eval("7 -2 %"), Ok(1));
    assert_eq!(vm.eval("-7 -2 %"), Ok(-1));
}

#[test]
fn stack_manipulation_ops() {
    let mut vm = Vm::new(3);
    assert_eq!(vm.eval("5 neg"), Ok(-5));
    assert_eq!(vm.eval("4 dup *"), Ok(16));
    assert_eq!(vm.eval("2 3 swap -"), Ok(1), "swap exchanges the top two");
    assert_eq!(vm.eval("7 8 drop"), Ok(7));
    assert_eq!(vm.eval("1 2 3 drop drop"), Ok(1));
}

#[test]
fn variables_read_through_the_stack() {
    let mut vm = Vm::new(8);
    vm.set_var("rate", 250);
    assert_eq!(vm.eval("rate 4 *"), Ok(1000));
    assert_eq!(vm.get_var("rate"), Some(250));
    assert_eq!(vm.get_var("missing"), None);

    vm.set_var("a", 1);
    vm.set_var("b", 2);
    vm.set_var("c", 3);
    vm.set_var("under_score2", 4);
    assert_eq!(vm.eval("a b + c + under_score2 +"), Ok(10));
}

#[test]
fn store_pops_and_persists_across_eval_calls() {
    let mut vm = Vm::new(8);
    assert_eq!(vm.eval("5 !x x x +"), Ok(10));
    assert_eq!(vm.get_var("x"), Some(5));
    assert_eq!(vm.eval("x 1 +"), Ok(6));

    // a store that leaves the stack empty is still a BadResult — but the
    // variable write sticks
    let mut vm2 = Vm::new(8);
    assert_eq!(vm2.eval("3 !acc"), Err(VmError::BadResult { depth: 0 }));
    assert_eq!(vm2.get_var("acc"), Some(3));
    assert_eq!(vm2.eval("acc 2 *"), Ok(6));
}

#[test]
fn store_overwrites_existing_binding() {
    let mut vm = Vm::new(8);
    vm.set_var("x", 1);
    assert_eq!(vm.eval("9 !x x"), Ok(9));
    assert_eq!(vm.get_var("x"), Some(9));
}

#[test]
fn each_eval_starts_with_a_fresh_stack() {
    let mut vm = Vm::new(8);
    assert_eq!(vm.eval("1 2"), Err(VmError::BadResult { depth: 2 }));
    // leftovers from the failed program must not leak into the next eval
    assert_eq!(vm.eval("5"), Ok(5));
}

#[test]
fn undefined_variable_reports_name_and_position() {
    let mut vm = Vm::new(8);
    vm.set_var("a", 1);
    assert_eq!(
        vm.eval("a b +"),
        Err(VmError::UndefinedVar {
            index: 1,
            name: "b".to_string()
        })
    );
}

#[test]
fn unknown_tokens_are_rejected_with_position() {
    let mut vm = Vm::new(8);
    assert_eq!(
        vm.eval("3 4 &"),
        Err(VmError::UnknownToken {
            index: 2,
            token: "&".to_string()
        })
    );
    assert_eq!(
        vm.eval("ADD"),
        Err(VmError::UnknownToken {
            index: 0,
            token: "ADD".to_string()
        }),
        "identifiers are lowercase; uppercase is not an op alias"
    );
    assert_eq!(
        vm.eval("--5"),
        Err(VmError::UnknownToken {
            index: 0,
            token: "--5".to_string()
        })
    );
    assert_eq!(
        vm.eval("!9"),
        Err(VmError::UnknownToken {
            index: 0,
            token: "!9".to_string()
        }),
        "a store target must be a valid identifier"
    );
}

#[test]
fn malformed_and_overflowing_literals() {
    let mut vm = Vm::new(8);
    assert_eq!(
        vm.eval("12x"),
        Err(VmError::BadLiteral {
            index: 0,
            token: "12x".to_string()
        })
    );
    assert_eq!(
        vm.eval("9223372036854775808"),
        Err(VmError::BadLiteral {
            index: 0,
            token: "9223372036854775808".to_string()
        }),
        "i64::MAX + 1 does not fit"
    );
    assert_eq!(
        vm.eval("-9223372036854775809"),
        Err(VmError::BadLiteral {
            index: 0,
            token: "-9223372036854775809".to_string()
        })
    );
    // ... but i64::MIN itself is a legal literal
    assert_eq!(vm.eval("-9223372036854775808"), Ok(i64::MIN));
}

#[test]
fn underflow_names_the_starving_token() {
    let mut vm = Vm::new(8);
    let cases: &[(&str, usize, &str)] = &[
        ("+", 0, "+"),
        ("1 +", 1, "+"),
        ("neg", 0, "neg"),
        ("1 swap", 1, "swap"),
        ("dup", 0, "dup"),
        ("drop", 0, "drop"),
        ("!x", 0, "!x"),
    ];
    for &(program, index, token) in cases {
        assert_eq!(
            vm.eval(program),
            Err(VmError::StackUnderflow {
                index,
                token: token.to_string()
            }),
            "program {program:?}"
        );
    }
}

#[test]
fn depth_guard_refuses_the_push_that_would_overflow() {
    let mut vm = Vm::new(3);
    assert_eq!(
        vm.eval("1 2 3 4"),
        Err(VmError::StackOverflow { index: 3, depth: 3 })
    );
    assert_eq!(
        vm.eval("1 2 3 dup"),
        Err(VmError::StackOverflow { index: 3, depth: 3 }),
        "dup pushes too"
    );
    // binary ops never grow the stack, so depth 2 is enough here
    let mut tight = Vm::new(2);
    assert_eq!(tight.eval("1 2 + 3 *"), Ok(9));

    let mut zero = Vm::new(0);
    assert_eq!(
        zero.eval("1"),
        Err(VmError::StackOverflow { index: 0, depth: 0 })
    );
}

#[test]
fn checked_arithmetic_reports_the_operator_token() {
    let mut vm = Vm::new(8);
    assert_eq!(vm.eval("0 1 -"), Ok(-1));
    assert_eq!(
        vm.eval(&format!("{} 1 +", i64::MAX)),
        Err(VmError::ArithmeticOverflow { index: 2 })
    );
    assert_eq!(
        vm.eval(&format!("{} 1 -", i64::MIN)),
        Err(VmError::ArithmeticOverflow { index: 2 })
    );
    assert_eq!(
        vm.eval(&format!("{} 2 *", i64::MAX)),
        Err(VmError::ArithmeticOverflow { index: 2 })
    );
    assert_eq!(
        vm.eval(&format!("{} neg", i64::MIN)),
        Err(VmError::ArithmeticOverflow { index: 1 })
    );
    assert_eq!(
        vm.eval(&format!("{} -1 /", i64::MIN)),
        Err(VmError::ArithmeticOverflow { index: 2 }),
        "i64::MIN / -1 overflows"
    );
    assert_eq!(
        vm.eval(&format!("{} -1 %", i64::MIN)),
        Err(VmError::ArithmeticOverflow { index: 2 })
    );
}

#[test]
fn division_by_zero_is_its_own_error() {
    let mut vm = Vm::new(8);
    assert_eq!(vm.eval("5 0 /"), Err(VmError::DivideByZero { index: 2 }));
    assert_eq!(vm.eval("5 0 %"), Err(VmError::DivideByZero { index: 2 }));
}

#[test]
fn programs_must_leave_exactly_one_value() {
    let mut vm = Vm::new(8);
    assert_eq!(vm.eval("1 2"), Err(VmError::BadResult { depth: 2 }));
    assert_eq!(vm.eval(""), Err(VmError::BadResult { depth: 0 }));
    assert_eq!(vm.eval("   \t  "), Err(VmError::BadResult { depth: 0 }));
}

#[test]
fn side_effects_before_an_error_persist() {
    let mut vm = Vm::new(8);
    assert_eq!(
        vm.eval("1 !a @@"),
        Err(VmError::UnknownToken {
            index: 2,
            token: "@@".to_string()
        })
    );
    assert_eq!(vm.get_var("a"), Some(1), "the store ran before the bad token");
}

#[test]
fn whitespace_is_insignificant_but_indices_count_tokens() {
    let mut vm = Vm::new(8);
    assert_eq!(vm.eval("  3\t4  +  "), Ok(7));
    assert_eq!(
        vm.eval("3 \t 0   /"),
        Err(VmError::DivideByZero { index: 2 })
    );
}

#[test]
fn error_display_messages_are_stable() {
    assert_eq!(
        VmError::UnknownToken {
            index: 2,
            token: "&".to_string()
        }
        .to_string(),
        "token 2: unknown token \"&\""
    );
    assert_eq!(
        VmError::StackUnderflow {
            index: 1,
            token: "+".to_string()
        }
        .to_string(),
        "token 1: stack underflow at \"+\""
    );
    assert_eq!(
        VmError::DivideByZero { index: 4 }.to_string(),
        "token 4: division by zero"
    );
    assert_eq!(
        VmError::BadResult { depth: 2 }.to_string(),
        "program left 2 values on the stack (expected exactly 1)"
    );
    fn assert_error<E: std::error::Error>(_: &E) {}
    assert_error(&VmError::DivideByZero { index: 0 });
}
