//! Planning contract: deterministic topological batches, dry-run rendering,
//! and the exact validation errors in their exact precedence order.

use rs_taskdeps::{plan, render_plan, PlanError, TaskSpec};

fn t(id: &str, needs: &[&str], produces: &[&str]) -> TaskSpec {
    TaskSpec::new(id, needs, produces)
}

fn b(batches: &[&[&str]]) -> Vec<Vec<String>> {
    batches
        .iter()
        .map(|batch| batch.iter().map(|s| s.to_string()).collect())
        .collect()
}

#[test]
fn single_task_with_no_needs_is_batch_one() {
    let tasks = [t("build", &[], &["binary"])];
    assert_eq!(plan(&tasks, &[]), Ok(b(&[&["build"]])));
}

#[test]
fn independent_tasks_share_a_batch_in_lexicographic_order() {
    let tasks = [
        t("web", &[], &["w"]),
        t("api", &[], &["a"]),
        t("docs", &[], &["d"]),
    ];
    assert_eq!(plan(&tasks, &[]), Ok(b(&[&["api", "docs", "web"]])));
}

#[test]
fn linear_chain_yields_one_task_per_batch() {
    let tasks = [
        t("package", &["binary"], &["tarball"]),
        t("compile", &["source"], &["binary"]),
        t("checkout", &[], &["source"]),
    ];
    assert_eq!(
        plan(&tasks, &[]),
        Ok(b(&[&["checkout"], &["compile"], &["package"]]))
    );
}

#[test]
fn diamond_fans_out_and_rejoins() {
    let tasks = [
        t("report", &["parsed", "statted"], &["report.html"]),
        t("stats", &["raw"], &["statted"]),
        t("parse", &["raw"], &["parsed"]),
        t("fetch", &[], &["raw"]),
    ];
    assert_eq!(
        plan(&tasks, &[]),
        Ok(b(&[&["fetch"], &["parse", "stats"], &["report"]]))
    );
}

#[test]
fn task_waits_for_its_slowest_dependency() {
    // "c" needs an artifact from batch 1 and one from batch 2 -> batch 3.
    let tasks = [
        t("a", &[], &["x"]),
        t("b", &["x"], &["y"]),
        t("c", &["x", "y"], &["z"]),
    ];
    assert_eq!(plan(&tasks, &[]), Ok(b(&[&["a"], &["b"], &["c"]])));
}

#[test]
fn given_artifacts_need_no_producer() {
    let tasks = [
        t("deploy", &["credentials", "binary"], &[]),
        t("build", &[], &["binary"]),
    ];
    assert_eq!(
        plan(&tasks, &["credentials"]),
        Ok(b(&[&["build"], &["deploy"]]))
    );
}

#[test]
fn producing_multiple_artifacts_satisfies_multiple_dependents() {
    let tasks = [
        t("split", &[], &["train", "test"]),
        t("fit", &["train"], &["model"]),
        t("eval", &["model", "test"], &["score"]),
    ];
    assert_eq!(plan(&tasks, &[]), Ok(b(&[&["split"], &["fit"], &["eval"]])));
}

#[test]
fn empty_input_plans_to_no_batches() {
    assert_eq!(plan(&[], &[]), Ok(vec![]));
}

#[test]
fn duplicate_task_id_is_rejected() {
    let tasks = [t("lint", &[], &["a"]), t("lint", &[], &["b"])];
    assert_eq!(
        plan(&tasks, &[]),
        Err(PlanError::DuplicateTask {
            id: "lint".to_string()
        })
    );
}

#[test]
fn duplicate_task_wins_over_duplicate_producer() {
    // Same id AND same artifact from both declarations: the id check fires first.
    let tasks = [t("dup", &[], &["a"]), t("dup", &[], &["a"])];
    assert_eq!(
        plan(&tasks, &[]),
        Err(PlanError::DuplicateTask {
            id: "dup".to_string()
        })
    );
}

#[test]
fn two_producers_of_one_artifact_are_reported_sorted() {
    // Declared out of order to prove the producer list is sorted, not positional.
    let tasks = [t("zeta", &[], &["binary"]), t("alpha", &[], &["binary"])];
    assert_eq!(
        plan(&tasks, &[]),
        Err(PlanError::DuplicateProducer {
            artifact: "binary".to_string(),
            producers: vec!["alpha".to_string(), "zeta".to_string()],
        })
    );
}

#[test]
fn a_task_conflicting_with_a_given_artifact_blames_the_given_pseudo_producer() {
    let tasks = [t("render", &[], &["config"])];
    assert_eq!(
        plan(&tasks, &["config"]),
        Err(PlanError::DuplicateProducer {
            artifact: "config".to_string(),
            producers: vec!["(given)".to_string(), "render".to_string()],
        })
    );
}

#[test]
fn duplicate_producer_reports_the_lexicographically_smallest_artifact() {
    // Both "alpha" and "beta" are doubly produced; "alpha" is the one reported.
    let tasks = [
        t("t1", &[], &["beta", "alpha"]),
        t("t2", &[], &["alpha"]),
        t("t3", &[], &["beta"]),
    ];
    assert_eq!(
        plan(&tasks, &[]),
        Err(PlanError::DuplicateProducer {
            artifact: "alpha".to_string(),
            producers: vec!["t1".to_string(), "t2".to_string()],
        })
    );
}

#[test]
fn unknown_artifact_names_task_and_artifact() {
    let tasks = [t("deploy", &["signed-image"], &[])];
    assert_eq!(
        plan(&tasks, &[]),
        Err(PlanError::UnknownArtifact {
            task: "deploy".to_string(),
            artifact: "signed-image".to_string(),
        })
    );
}

#[test]
fn unknown_artifact_scans_tasks_by_id_and_needs_in_declaration_order() {
    // "alpha" sorts before "zeta", and within alpha the first unknown need in
    // declaration order is "ghost2" (its first need, "real", is produced).
    let tasks = [
        t("zeta", &["ghost1"], &[]),
        t("alpha", &["real", "ghost2", "ghost3"], &[]),
        t("mid", &[], &["real"]),
    ];
    assert_eq!(
        plan(&tasks, &[]),
        Err(PlanError::UnknownArtifact {
            task: "alpha".to_string(),
            artifact: "ghost2".to_string(),
        })
    );
}

#[test]
fn unknown_artifact_is_reported_before_cycles() {
    let tasks = [
        t("a", &["ghost"], &["x"]),
        t("b", &["c-art"], &["b-art"]),
        t("c", &["b-art"], &["c-art"]),
    ];
    assert_eq!(
        plan(&tasks, &[]),
        Err(PlanError::UnknownArtifact {
            task: "a".to_string(),
            artifact: "ghost".to_string(),
        })
    );
}

#[test]
fn a_cycle_is_reported_with_all_stuck_tasks_sorted() {
    let tasks = [t("b", &["x"], &["y"]), t("a", &["y"], &["x"])];
    assert_eq!(
        plan(&tasks, &[]),
        Err(PlanError::Cycle {
            tasks: vec!["a".to_string(), "b".to_string()],
        })
    );
}

#[test]
fn cycle_report_includes_tasks_stuck_behind_the_cycle() {
    // "island" schedules fine; "egg"/"hen" deadlock and "victim" is wedged
    // behind them, so all three land in the report — sorted.
    let tasks = [
        t("island", &[], &["rock"]),
        t("hen", &["egg-art"], &["chicken-art"]),
        t("egg", &["chicken-art"], &["egg-art"]),
        t("victim", &["chicken-art"], &[]),
    ];
    assert_eq!(
        plan(&tasks, &[]),
        Err(PlanError::Cycle {
            tasks: vec!["egg".to_string(), "hen".to_string(), "victim".to_string()],
        })
    );
}

#[test]
fn error_messages_are_operator_readable() {
    assert_eq!(
        PlanError::DuplicateTask {
            id: "lint".to_string()
        }
        .to_string(),
        "duplicate task id 'lint'"
    );
    assert_eq!(
        PlanError::DuplicateProducer {
            artifact: "binary".to_string(),
            producers: vec!["a".to_string(), "b".to_string()],
        }
        .to_string(),
        "artifact 'binary' has multiple producers: a, b"
    );
    assert_eq!(
        PlanError::UnknownArtifact {
            task: "deploy".to_string(),
            artifact: "image".to_string(),
        }
        .to_string(),
        "task 'deploy' needs unknown artifact 'image'"
    );
    assert_eq!(
        PlanError::Cycle {
            tasks: vec!["a".to_string(), "b".to_string()],
        }
        .to_string(),
        "dependency cycle among tasks: a, b"
    );
}

#[test]
fn render_plan_is_one_line_per_batch() {
    let tasks = [
        t("report", &["parsed", "statted"], &["report.html"]),
        t("stats", &["raw"], &["statted"]),
        t("parse", &["raw"], &["parsed"]),
        t("fetch", &[], &["raw"]),
    ];
    let batches = plan(&tasks, &[]).unwrap();
    assert_eq!(
        render_plan(&batches),
        "batch 1: fetch\nbatch 2: parse, stats\nbatch 3: report"
    );
}

#[test]
fn render_plan_of_nothing_is_empty() {
    assert_eq!(render_plan(&[]), "");
}
