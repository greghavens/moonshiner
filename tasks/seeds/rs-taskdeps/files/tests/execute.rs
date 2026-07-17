//! Execution contract: invocation order, failure propagation with root-cause
//! attribution, and the run report shape.

use rs_taskdeps::{execute, PlanError, TaskSpec, TaskStatus};

fn t(id: &str, needs: &[&str], produces: &[&str]) -> TaskSpec {
    TaskSpec::new(id, needs, produces)
}

fn s(id: &str, st: TaskStatus) -> (String, TaskStatus) {
    (id.to_string(), st)
}

#[test]
fn all_green_pipeline_runs_everything_in_plan_order() {
    let tasks = [
        t("package", &["binary"], &["tarball"]),
        t("compile", &["source"], &["binary"]),
        t("checkout", &[], &["source"]),
        t("lint", &["source"], &[]),
    ];
    let mut log: Vec<String> = Vec::new();
    let report = execute(&tasks, &[], &mut |id| {
        log.push(id.to_string());
        true
    })
    .unwrap();
    assert_eq!(log, vec!["checkout", "compile", "lint", "package"]);
    assert_eq!(report.invoked, log);
    assert_eq!(
        report.statuses,
        vec![
            s("checkout", TaskStatus::Succeeded),
            s("compile", TaskStatus::Succeeded),
            s("lint", TaskStatus::Succeeded),
            s("package", TaskStatus::Succeeded),
        ]
    );
}

#[test]
fn a_failed_task_is_reported_failed_and_still_counts_as_invoked() {
    let tasks = [t("flaky", &[], &["out"])];
    let report = execute(&tasks, &[], &mut |_| false).unwrap();
    assert_eq!(report.invoked, vec!["flaky".to_string()]);
    assert_eq!(report.statuses, vec![s("flaky", TaskStatus::Failed)]);
}

#[test]
fn dependents_of_a_failed_task_are_skipped_not_invoked() {
    let tasks = [t("consume", &["data"], &[]), t("produce", &[], &["data"])];
    let mut log: Vec<String> = Vec::new();
    let report = execute(&tasks, &[], &mut |id| {
        log.push(id.to_string());
        id != "produce"
    })
    .unwrap();
    assert_eq!(log, vec!["produce"]);
    assert_eq!(
        report.statuses,
        vec![
            s(
                "consume",
                TaskStatus::Skipped {
                    because: "produce".to_string()
                }
            ),
            s("produce", TaskStatus::Failed),
        ]
    );
}

#[test]
fn skips_propagate_transitively_with_the_original_root_cause() {
    let tasks = [
        t("a", &[], &["x"]),
        t("b", &["x"], &["y"]),
        t("c", &["y"], &["z"]),
    ];
    let report = execute(&tasks, &[], &mut |id| id != "a").unwrap();
    assert_eq!(report.invoked, vec!["a".to_string()]);
    assert_eq!(
        report.statuses,
        vec![
            s("a", TaskStatus::Failed),
            s(
                "b",
                TaskStatus::Skipped {
                    because: "a".to_string()
                }
            ),
            s(
                "c",
                TaskStatus::Skipped {
                    because: "a".to_string()
                }
            ),
        ]
    );
}

#[test]
fn root_cause_ties_break_lexicographically() {
    let tasks = [
        t("zeta", &[], &["z-art"]),
        t("alpha", &[], &["a-art"]),
        t("join", &["a-art", "z-art"], &[]),
    ];
    // Both producers fail; "join" blames the lexicographically smallest root.
    let report = execute(&tasks, &[], &mut |_| false).unwrap();
    assert_eq!(
        report.statuses,
        vec![
            s("alpha", TaskStatus::Failed),
            s(
                "join",
                TaskStatus::Skipped {
                    because: "alpha".to_string()
                }
            ),
            s("zeta", TaskStatus::Failed),
        ]
    );
}

#[test]
fn unrelated_branches_keep_running_after_a_failure() {
    let tasks = [
        t("bad", &[], &["poison"]),
        t("good", &[], &["fruit"]),
        t("eat", &["fruit"], &[]),
        t("suffer", &["poison"], &[]),
    ];
    let mut log: Vec<String> = Vec::new();
    let report = execute(&tasks, &[], &mut |id| {
        log.push(id.to_string());
        id != "bad"
    })
    .unwrap();
    assert_eq!(log, vec!["bad", "good", "eat"]);
    assert_eq!(
        report.statuses,
        vec![
            s("bad", TaskStatus::Failed),
            s("eat", TaskStatus::Succeeded),
            s("good", TaskStatus::Succeeded),
            s(
                "suffer",
                TaskStatus::Skipped {
                    because: "bad".to_string()
                }
            ),
        ]
    );
}

#[test]
fn given_artifacts_satisfy_needs_during_execution() {
    let tasks = [t("train", &["dataset"], &["model"])];
    let report = execute(&tasks, &["dataset"], &mut |_| true).unwrap();
    assert_eq!(report.invoked, vec!["train".to_string()]);
    assert_eq!(report.statuses, vec![s("train", TaskStatus::Succeeded)]);
}

#[test]
fn plan_errors_abort_before_any_task_runs() {
    let tasks = [t("a", &["y"], &["x"]), t("b", &["x"], &["y"])];
    let mut log: Vec<String> = Vec::new();
    let err = execute(&tasks, &[], &mut |id| {
        log.push(id.to_string());
        true
    })
    .unwrap_err();
    assert_eq!(
        err,
        PlanError::Cycle {
            tasks: vec!["a".to_string(), "b".to_string()],
        }
    );
    assert!(log.is_empty(), "no task may be invoked when planning fails");
}
