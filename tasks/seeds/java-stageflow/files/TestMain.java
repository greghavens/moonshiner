import java.time.Duration;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.Objects;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CompletionException;
import java.util.concurrent.Executor;

/**
 * Acceptance tests for the loan pre-approval stage orchestrator.
 * Fully deterministic: same-thread executor + ManualScheduler virtual time.
 * Run: java TestMain.java
 */
public final class TestMain {
    private static int passed = 0;
    private static int failed = 0;

    interface Body { void run() throws Exception; }

    private static void test(String name, Body body) {
        try {
            body.run();
            passed++;
            System.out.println("PASS " + name);
        } catch (Throwable t) {
            failed++;
            System.out.println("FAIL " + name + ": " + t);
        }
    }

    private static void eq(String what, Object expected, Object actual) {
        if (!Objects.equals(expected, actual)) {
            throw new AssertionError(what + ": expected <" + expected + "> got <" + actual + ">");
        }
    }

    private static void yes(String what, boolean cond) {
        if (!cond) throw new AssertionError(what);
    }

    private static <X extends Throwable> X thrown(Class<X> type, Body body) {
        try {
            body.run();
        } catch (Throwable t) {
            if (type.isInstance(t)) return type.cast(t);
            throw new AssertionError("expected " + type.getSimpleName() + " but got " + t, t);
        }
        throw new AssertionError("expected " + type.getSimpleName() + " but nothing was thrown");
    }

    /** The failure inside a completed-exceptionally future, CompletionException stripped. */
    private static Throwable failureOf(CompletableFuture<?> cf) {
        yes("future must be done", cf.isDone());
        yes("future must have failed", cf.isCompletedExceptionally());
        Throwable t = cf.handle((v, ex) -> ex).join();
        return (t instanceof CompletionException && t.getCause() != null) ? t.getCause() : t;
    }

    static final class CountingExecutor implements Executor {
        int runs = 0;

        @Override
        public void execute(Runnable command) {
            runs++;
            command.run();
        }
    }

    private static Orchestrator sameThread(ManualScheduler scheduler) {
        return new Orchestrator(Runnable::run, scheduler);
    }

    public static void main(String[] args) {

        test("supply_runs_on_the_injected_executor", () -> {
            CountingExecutor executor = new CountingExecutor();
            Orchestrator orch = new Orchestrator(executor, new ManualScheduler());
            CompletableFuture<Integer> stage = orch.supply("parse-docs", () -> 42);
            yes("same-thread executor completes it synchronously", stage.isDone());
            eq("value", 42, stage.join());
            eq("executor used exactly once", 1, executor.runs);
        });

        test("supply_failure_becomes_a_stage_exception", () -> {
            Orchestrator orch = sameThread(new ManualScheduler());
            IllegalStateException boom = new IllegalStateException("bad pdf");
            CompletableFuture<Integer> stage = orch.supply("parse-docs", () -> { throw boom; });
            yes("completed exceptionally", stage.isCompletedExceptionally());
            StageException e = (StageException) failureOf(stage);
            eq("message", "stage 'parse-docs' failed", e.getMessage());
            eq("stage name", "parse-docs", e.stage());
            yes("original cause preserved", e.getCause() == boom);
        });

        test("constructor_and_supply_validation", () -> {
            ManualScheduler scheduler = new ManualScheduler();
            eq("null executor", "executor must not be null",
                    thrown(IllegalArgumentException.class,
                            () -> new Orchestrator(null, scheduler)).getMessage());
            eq("null scheduler", "scheduler must not be null",
                    thrown(IllegalArgumentException.class,
                            () -> new Orchestrator(Runnable::run, null)).getMessage());
            Orchestrator orch = sameThread(scheduler);
            eq("null stage", "stage must not be blank",
                    thrown(IllegalArgumentException.class,
                            () -> orch.supply(null, () -> 1)).getMessage());
            eq("blank stage", "stage must not be blank",
                    thrown(IllegalArgumentException.class,
                            () -> orch.supply("   ", () -> 1)).getMessage());
            eq("null body", "body must not be null",
                    thrown(IllegalArgumentException.class,
                            () -> orch.supply("x", null)).getMessage());
        });

        test("timeout_fires_exactly_at_the_virtual_deadline", () -> {
            ManualScheduler scheduler = new ManualScheduler();
            Orchestrator orch = sameThread(scheduler);
            CompletableFuture<String> bureau = new CompletableFuture<>();
            CompletableFuture<String> timed = orch.withTimeout("equifax", bureau, Duration.ofSeconds(30));
            yes("a fresh future is returned", timed != bureau);
            eq("one timer scheduled", 1, scheduler.scheduledCount());
            scheduler.advance(Duration.ofSeconds(29));
            yes("still waiting one second before the deadline", !timed.isDone());
            scheduler.advance(Duration.ofSeconds(1));
            yes("timed out at the deadline", timed.isCompletedExceptionally());
            StageTimeoutException e = (StageTimeoutException) failureOf(timed);
            eq("message", "stage 'equifax' timed out after PT30S", e.getMessage());
            eq("stage name", "equifax", e.stage());
            eq("timeout", Duration.ofSeconds(30), e.timeout());
            yes("the SOURCE future must never be completed by the timeout", !bureau.isDone());
        });

        test("completion_before_the_deadline_cancels_the_timer", () -> {
            ManualScheduler scheduler = new ManualScheduler();
            Orchestrator orch = sameThread(scheduler);
            CompletableFuture<String> bureau = new CompletableFuture<>();
            CompletableFuture<String> timed = orch.withTimeout("equifax", bureau, Duration.ofSeconds(30));
            bureau.complete("score-780");
            eq("value mirrored", "score-780", timed.join());
            eq("timer cancelled", 1, scheduler.cancelledCount());
            eq("nothing left pending", 0, scheduler.pendingCount());
            scheduler.advance(Duration.ofSeconds(120));
            eq("advancing past the dead timer changes nothing", "score-780", timed.join());
            eq("no timer ever fired", 0, scheduler.firedCount());
        });

        test("failed_source_mirrors_its_cause_and_cancels_the_timer", () -> {
            ManualScheduler scheduler = new ManualScheduler();
            Orchestrator orch = sameThread(scheduler);
            CompletableFuture<String> bureau = new CompletableFuture<>();
            CompletableFuture<String> timed = orch.withTimeout("equifax", bureau, Duration.ofSeconds(30));
            IllegalStateException boom = new IllegalStateException("bureau 503");
            bureau.completeExceptionally(boom);
            yes("failure mirrored as-is", failureOf(timed) == boom);
            eq("timer cancelled", 1, scheduler.cancelledCount());
        });

        test("already_completed_source_schedules_no_timer", () -> {
            ManualScheduler scheduler = new ManualScheduler();
            Orchestrator orch = sameThread(scheduler);
            CompletableFuture<String> done = CompletableFuture.completedFuture("cached");
            CompletableFuture<String> timed = orch.withTimeout("equifax", done, Duration.ofSeconds(30));
            eq("value", "cached", timed.join());
            yes("fresh future even when already done", timed != done);
            eq("no timer scheduled at all", 0, scheduler.scheduledCount());
        });

        test("with_timeout_validation", () -> {
            Orchestrator orch = sameThread(new ManualScheduler());
            CompletableFuture<String> cf = new CompletableFuture<>();
            eq("blank stage", "stage must not be blank",
                    thrown(IllegalArgumentException.class,
                            () -> orch.withTimeout("", cf, Duration.ofSeconds(1))).getMessage());
            eq("null future", "future must not be null",
                    thrown(IllegalArgumentException.class,
                            () -> orch.withTimeout("x", null, Duration.ofSeconds(1))).getMessage());
            eq("null timeout", "timeout must be positive",
                    thrown(IllegalArgumentException.class,
                            () -> orch.withTimeout("x", cf, null)).getMessage());
            eq("zero timeout", "timeout must be positive",
                    thrown(IllegalArgumentException.class,
                            () -> orch.withTimeout("x", cf, Duration.ZERO)).getMessage());
            eq("negative timeout", "timeout must be positive",
                    thrown(IllegalArgumentException.class,
                            () -> orch.withTimeout("x", cf, Duration.ofSeconds(-1))).getMessage());
        });

        test("all_preserves_input_order_regardless_of_completion_order", () -> {
            Orchestrator orch = sameThread(new ManualScheduler());
            CompletableFuture<String> a = new CompletableFuture<>();
            CompletableFuture<String> b = new CompletableFuture<>();
            CompletableFuture<String> c = new CompletableFuture<>();
            CompletableFuture<List<String>> all = orch.all(List.of(a, b, c));
            c.complete("C");
            a.complete("A");
            yes("still waiting for b", !all.isDone());
            b.complete("B");
            eq("input order, not completion order", List.of("A", "B", "C"), all.join());
            thrown(UnsupportedOperationException.class, () -> all.join().add("D"));
        });

        test("all_is_not_fail_fast_and_reports_the_lowest_index_failure", () -> {
            Orchestrator orch = sameThread(new ManualScheduler());
            CompletableFuture<String> a = new CompletableFuture<>();
            CompletableFuture<String> b = new CompletableFuture<>();
            CompletableFuture<String> c = new CompletableFuture<>();
            CompletableFuture<List<String>> all = orch.all(List.of(a, b, c));
            IllegalStateException bBoom = new IllegalStateException("b failed first in time");
            b.completeExceptionally(bBoom);
            yes("NOT fail-fast: a and c still outstanding", !all.isDone());
            c.complete("C");
            yes("still waiting for a", !all.isDone());
            IllegalStateException aBoom = new IllegalStateException("a failed last in time");
            a.completeExceptionally(aBoom);
            yes("done once every stage settled", all.isDone());
            yes("lowest-index failure wins, not first-in-time", failureOf(all) == aBoom);
        });

        test("all_empty_and_validation", () -> {
            Orchestrator orch = sameThread(new ManualScheduler());
            eq("empty input completes immediately", List.of(), orch.all(List.of()).join());
            eq("null list", "stages must not be null",
                    thrown(IllegalArgumentException.class, () -> orch.all(null)).getMessage());
            eq("null element", "stages must not contain null",
                    thrown(IllegalArgumentException.class,
                            () -> orch.all(Arrays.asList(new CompletableFuture<String>(), null))).getMessage());
        });

        test("any_first_success_in_completion_order_wins", () -> {
            Orchestrator orch = sameThread(new ManualScheduler());
            CompletableFuture<String> a = new CompletableFuture<>();
            CompletableFuture<String> b = new CompletableFuture<>();
            CompletableFuture<String> any = orch.any(List.of(a, b));
            yes("waits while nothing settled", !any.isDone());
            b.complete("tu-report");
            eq("second stage won the race", "tu-report", any.join());
            a.complete("eq-report");
            eq("late success is ignored", "tu-report", any.join());
        });

        test("any_skips_failures_while_a_success_is_still_possible", () -> {
            Orchestrator orch = sameThread(new ManualScheduler());
            CompletableFuture<String> a = new CompletableFuture<>();
            CompletableFuture<String> b = new CompletableFuture<>();
            CompletableFuture<String> any = orch.any(List.of(a, b));
            a.completeExceptionally(new IllegalStateException("bureau down"));
            yes("one failure does not settle the race", !any.isDone());
            b.complete("tu-report");
            eq("value", "tu-report", any.join());
        });

        test("any_when_all_fail_collects_causes_in_input_order", () -> {
            Orchestrator orch = sameThread(new ManualScheduler());
            CompletableFuture<String> a = new CompletableFuture<>();
            CompletableFuture<String> b = new CompletableFuture<>();
            CompletableFuture<String> any = orch.any(List.of(a, b));
            IllegalStateException bBoom = new IllegalStateException("transunion 503");
            IllegalStateException aBoom = new IllegalStateException("equifax 500");
            b.completeExceptionally(bBoom);
            a.completeExceptionally(aBoom);
            AllFailedException e = (AllFailedException) failureOf(any);
            eq("message", "all 2 stages failed", e.getMessage());
            eq("causes in INPUT order despite reverse completion",
                    List.of(aBoom, bBoom), e.causes());
            thrown(UnsupportedOperationException.class, () -> e.causes().add(aBoom));
        });

        test("any_validation", () -> {
            Orchestrator orch = sameThread(new ManualScheduler());
            eq("empty list", "stages must not be empty",
                    thrown(IllegalArgumentException.class, () -> orch.any(List.of())).getMessage());
            eq("null list", "stages must not be null",
                    thrown(IllegalArgumentException.class, () -> orch.any(null)).getMessage());
            eq("null element", "stages must not contain null",
                    thrown(IllegalArgumentException.class,
                            () -> orch.any(Arrays.asList(new CompletableFuture<String>(), null))).getMessage());
        });

        test("zip_combines_only_once_both_sides_arrive", () -> {
            Orchestrator orch = sameThread(new ManualScheduler());
            CompletableFuture<Integer> a = new CompletableFuture<>();
            CompletableFuture<Integer> b = new CompletableFuture<>();
            CompletableFuture<Integer> product = orch.zip(a, b, (x, y) -> x * y);
            a.complete(6);
            yes("half a zip is no zip", !product.isDone());
            b.complete(7);
            eq("combined", 42, product.join());
        });

        test("zip_fails_fast_without_waiting_for_the_other_side", () -> {
            Orchestrator orch = sameThread(new ManualScheduler());
            CompletableFuture<Integer> a = new CompletableFuture<>();
            CompletableFuture<Integer> b = new CompletableFuture<>();
            CompletableFuture<Integer> product = orch.zip(a, b, (x, y) -> x * y);
            IllegalStateException boom = new IllegalStateException("credit stage exploded");
            a.completeExceptionally(boom);
            yes("failed immediately while b is still pending", product.isCompletedExceptionally());
            yes("cause mirrored as-is", failureOf(product) == boom);
            b.complete(7);
            yes("late value cannot resurrect it", failureOf(product) == boom);
        });

        test("zip_validation_and_throwing_combiner", () -> {
            Orchestrator orch = sameThread(new ManualScheduler());
            CompletableFuture<Integer> a = new CompletableFuture<>();
            CompletableFuture<Integer> b = new CompletableFuture<>();
            eq("null future", "future must not be null",
                    thrown(IllegalArgumentException.class,
                            () -> orch.zip(null, b, (x, y) -> x)).getMessage());
            eq("null combine", "combine must not be null",
                    thrown(IllegalArgumentException.class,
                            () -> orch.zip(a, b, null)).getMessage());
            IllegalStateException boom = new IllegalStateException("mult overflow");
            CompletableFuture<Integer> product = orch.zip(a, b, (x, y) -> { throw boom; });
            a.complete(1);
            b.complete(2);
            yes("combiner exception fails the zip", failureOf(product) == boom);
        });

        test("loan_decision_tree_end_to_end", () -> {
            ManualScheduler scheduler = new ManualScheduler();
            Orchestrator orch = sameThread(scheduler);
            CompletableFuture<String> equifax = new CompletableFuture<>();
            CompletableFuture<String> transunion = new CompletableFuture<>();
            CompletableFuture<String> timedEq = orch.withTimeout("equifax", equifax, Duration.ofSeconds(30));
            CompletableFuture<String> timedTu = orch.withTimeout("transunion", transunion, Duration.ofSeconds(45));
            CompletableFuture<String> credit = orch.any(List.of(timedEq, timedTu));
            CompletableFuture<String> docs = orch.supply("parse-docs", () -> "docs-ok");
            CompletableFuture<String> decision = orch.zip(credit, docs, (c, d) -> c + "+" + d);

            scheduler.advance(Duration.ofSeconds(30));
            yes("equifax timed out", timedEq.isCompletedExceptionally());
            eq("its stage", "equifax", ((StageTimeoutException) failureOf(timedEq)).stage());
            yes("race still open on transunion", !decision.isDone());

            transunion.complete("tu-score-712");
            eq("decision assembled", "tu-score-712+docs-ok", decision.join());
            eq("transunion timer cancelled", 1, scheduler.cancelledCount());
            eq("exactly one timer ever fired", 1, scheduler.firedCount());
            scheduler.advance(Duration.ofSeconds(300));
            eq("nothing changes after the fact", "tu-score-712+docs-ok", decision.join());
        });

        test("racing_two_timeouts_fails_with_causes_in_input_order", () -> {
            ManualScheduler scheduler = new ManualScheduler();
            Orchestrator orch = sameThread(scheduler);
            CompletableFuture<String> timedEq = orch.withTimeout("equifax",
                    new CompletableFuture<>(), Duration.ofSeconds(30));
            CompletableFuture<String> timedTu = orch.withTimeout("transunion",
                    new CompletableFuture<>(), Duration.ofSeconds(45));
            CompletableFuture<String> credit = orch.any(List.of(timedEq, timedTu));
            scheduler.advance(Duration.ofSeconds(45));
            AllFailedException e = (AllFailedException) failureOf(credit);
            eq("message", "all 2 stages failed", e.getMessage());
            eq("both causes are stage timeouts", 2, e.causes().size());
            eq("first cause is equifax", "stage 'equifax' timed out after PT30S",
                    e.causes().get(0).getMessage());
            eq("second cause is transunion", "stage 'transunion' timed out after PT45S",
                    e.causes().get(1).getMessage());
        });

        System.out.println(passed + " passed, " + failed + " failed");
        if (failed > 0) System.exit(1);
    }
}
