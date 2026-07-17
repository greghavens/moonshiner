import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Map;
import java.util.NavigableMap;
import java.util.Objects;
import java.util.function.Predicate;
import java.util.function.ToLongFunction;
import java.util.stream.Collector;

/**
 * Acceptance tests for the espresso-fleet telemetry collectors.
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

    /** Drives supplier/accumulator/combiner/finisher by hand over pre-split parts. */
    private static <T, A, R> R collectSplit(Collector<T, A, R> collector, List<List<T>> parts) {
        A merged = collector.supplier().get();
        for (List<T> part : parts) {
            A acc = collector.supplier().get();
            for (T t : part) {
                collector.accumulator().accept(acc, t);
            }
            merged = collector.combiner().apply(merged, acc);
        }
        return collector.finisher().apply(merged);
    }

    record Shot(String machine, long millis, boolean decaf) {}

    private static List<Shot> fleet() {
        return List.of(
                new Shot("LM-01", 27500, false),
                new Shot("LM-01", 31200, true),
                new Shot("KV-02", 24800, false),
                new Shot("KV-02", 33000, true),
                new Shot("SL-03", 29000, false));
    }

    public static void main(String[] args) {
        Comparator<Shot> byMillis = Comparator.comparingLong(Shot::millis);

        test("topk_basic_greatest_first", () -> {
            List<Long> pulls = List.of(250L, 900L, 512L, 30L, 771L, 640L);
            List<Long> top = pulls.stream()
                    .collect(BrewCollectors.topK(3, Comparator.<Long>naturalOrder()));
            eq("top 3 descending", List.of(900L, 771L, 640L), top);
        });

        test("topk_ties_broken_by_encounter_order", () -> {
            Shot m1 = new Shot("m1", 700, false);
            Shot m2 = new Shot("m2", 700, false);
            Shot m3 = new Shot("m3", 900, false);
            Shot m4 = new Shot("m4", 700, false);
            List<Shot> forward = List.of(m1, m2, m3, m4);
            eq("forward encounter order wins ties",
                    List.of(m3, m1, m2),
                    forward.stream().collect(BrewCollectors.topK(3, byMillis)));
            List<Shot> reversed = List.of(m4, m3, m2, m1);
            eq("reversed input flips the tie winners",
                    List.of(m3, m4, m2),
                    reversed.stream().collect(BrewCollectors.topK(3, byMillis)));
        });

        test("topk_k_edge_cases", () -> {
            List<Long> pulls = List.of(5L, 9L, 1L);
            eq("k=0 collects nothing", List.of(),
                    pulls.stream().collect(BrewCollectors.topK(0, Comparator.<Long>naturalOrder())));
            eq("k larger than input returns everything sorted",
                    List.of(9L, 5L, 1L),
                    pulls.stream().collect(BrewCollectors.topK(10, Comparator.<Long>naturalOrder())));
        });

        test("topk_result_is_immutable", () -> {
            List<Long> top = List.of(5L, 9L).stream()
                    .collect(BrewCollectors.topK(1, Comparator.<Long>naturalOrder()));
            thrown(UnsupportedOperationException.class, () -> top.add(1L));
        });

        test("topk_factory_validation", () -> {
            thrown(IllegalArgumentException.class, () -> BrewCollectors.topK(-1, Comparator.<Long>naturalOrder()));
            thrown(IllegalArgumentException.class, () -> BrewCollectors.topK(3, null));
        });

        test("topk_characteristics_claim_nothing", () -> {
            Collector<Long, ?, List<Long>> c = BrewCollectors.topK(2, Comparator.<Long>naturalOrder());
            yes("an order-sensitive, finisher-bearing collector must claim NO characteristics, got "
                    + c.characteristics(), c.characteristics().isEmpty());
        });

        test("topk_manual_split_combine_matches_sequential", () -> {
            Shot m1 = new Shot("m1", 700, false);
            Shot m2 = new Shot("m2", 700, false);
            Shot m3 = new Shot("m3", 900, false);
            Shot m4 = new Shot("m4", 700, false);
            List<List<Shot>> parts = List.of(List.of(m1), List.of(), List.of(m2, m3, m4));
            List<Shot> viaSplit = collectSplit(BrewCollectors.topK(3, byMillis), parts);
            eq("combiner keeps left-part elements ahead on ties", List.of(m3, m1, m2), viaSplit);
            List<Shot> sequential = List.of(m1, m2, m3, m4).stream()
                    .collect(BrewCollectors.topK(3, byMillis));
            eq("split-combine equals sequential", sequential, viaSplit);
        });

        test("partition_stats_basic", () -> {
            Map<Boolean, BrewStats> byDecaf = fleet().stream()
                    .collect(BrewCollectors.partitionStats(Shot::decaf, Shot::millis));
            eq("decaf side", new BrewStats(2, 64200, 31200, 33000), byDecaf.get(true));
            eq("regular side", new BrewStats(3, 81300, 24800, 29000), byDecaf.get(false));
            eq("exactly the two keys", 2, byDecaf.size());
        });

        test("partition_stats_empty_side_is_all_zero", () -> {
            Map<Boolean, BrewStats> all = List.of(400L, 500L).stream()
                    .collect(BrewCollectors.partitionStats(v -> true, Long::longValue));
            eq("matching side", new BrewStats(2, 900, 400, 500), all.get(true));
            eq("empty side", new BrewStats(0, 0, 0, 0), all.get(false));
            yes("FALSE key present even when empty", all.containsKey(false));
        });

        test("partition_stats_result_is_unmodifiable", () -> {
            Map<Boolean, BrewStats> m = fleet().stream()
                    .collect(BrewCollectors.partitionStats(Shot::decaf, Shot::millis));
            thrown(UnsupportedOperationException.class,
                    () -> m.put(true, new BrewStats(0, 0, 0, 0)));
        });

        test("partition_stats_characteristics_exactly_unordered", () -> {
            Collector<Shot, ?, Map<Boolean, BrewStats>> c =
                    BrewCollectors.partitionStats(Shot::decaf, Shot::millis);
            eq("claims UNORDERED and nothing else",
                    java.util.Set.of(Collector.Characteristics.UNORDERED), c.characteristics());
        });

        test("partition_stats_unordered_claim_is_honest", () -> {
            List<Shot> forward = fleet();
            List<Shot> backward = new ArrayList<>(forward);
            java.util.Collections.reverse(backward);
            eq("reversed input, identical result",
                    forward.stream().collect(BrewCollectors.partitionStats(Shot::decaf, Shot::millis)),
                    backward.stream().collect(BrewCollectors.partitionStats(Shot::decaf, Shot::millis)));
        });

        test("partition_stats_parallel_matches_sequential", () -> {
            List<Shot> shots = new ArrayList<>();
            for (int i = 0; i < 64; i++) {
                shots.add(new Shot("M-" + (i % 7), 20000 + (i * 37L) % 15000, i % 3 == 0));
            }
            Map<Boolean, BrewStats> sequential = shots.stream()
                    .collect(BrewCollectors.partitionStats(Shot::decaf, Shot::millis));
            Map<Boolean, BrewStats> parallel = shots.parallelStream()
                    .collect(BrewCollectors.partitionStats(Shot::decaf, Shot::millis));
            eq("parallel equals sequential", sequential, parallel);
        });

        test("partition_stats_combining_an_empty_part_does_not_poison_min_max", () -> {
            Predicate<Long> slow = v -> v >= 300;
            ToLongFunction<Long> identity = Long::longValue;
            List<List<Long>> parts = List.of(List.of(), List.of(200L, 350L, 275L));
            Map<Boolean, BrewStats> viaSplit =
                    collectSplit(BrewCollectors.partitionStats(slow, identity), parts);
            eq("matching side after empty-left combine", new BrewStats(1, 350, 350, 350), viaSplit.get(true));
            eq("other side after empty-left combine", new BrewStats(2, 475, 200, 275), viaSplit.get(false));
        });

        test("histogram_buckets_by_floored_division", () -> {
            List<Long> deltas = List.of(-250L, -1L, 0L, 99L, 100L, 754L, -300L);
            NavigableMap<Long, Long> h = deltas.stream()
                    .collect(BrewCollectors.histogram(Long::longValue, 100));
            eq("bucket map", Map.of(-300L, 2L, -100L, 1L, 0L, 2L, 100L, 1L, 700L, 1L), h);
            eq("firstKey ascending", -300L, h.firstKey());
            eq("lastKey ascending", 700L, h.lastKey());
        });

        test("histogram_result_is_unmodifiable", () -> {
            NavigableMap<Long, Long> h = List.of(10L).stream()
                    .collect(BrewCollectors.histogram(Long::longValue, 100));
            thrown(UnsupportedOperationException.class, () -> h.put(500L, 1L));
        });

        test("histogram_characteristics_exactly_unordered", () -> {
            Collector<Long, ?, NavigableMap<Long, Long>> c =
                    BrewCollectors.histogram(Long::longValue, 100);
            eq("claims UNORDERED and nothing else",
                    java.util.Set.of(Collector.Characteristics.UNORDERED), c.characteristics());
        });

        test("histogram_split_combine_and_parallel_match_sequential", () -> {
            List<Long> millis = List.of(24800L, 27500L, 29000L, 31200L, 33000L, 26100L,
                    24999L, 25000L, 30000L, 34999L, 20000L, 22222L);
            NavigableMap<Long, Long> sequential = millis.stream()
                    .collect(BrewCollectors.histogram(Long::longValue, 5000));
            eq("expected buckets", Map.of(20000L, 4L, 25000L, 4L, 30000L, 4L), sequential);
            List<List<Long>> parts = List.of(millis.subList(0, 4), millis.subList(4, 4),
                    millis.subList(4, 12));
            eq("split-combine equals sequential", sequential,
                    collectSplit(BrewCollectors.histogram(Long::longValue, 5000), parts));
            eq("parallel equals sequential", sequential,
                    millis.parallelStream().collect(BrewCollectors.histogram(Long::longValue, 5000)));
        });

        test("histogram_factory_validation", () -> {
            thrown(IllegalArgumentException.class, () -> BrewCollectors.histogram(Long::longValue, 0));
            thrown(IllegalArgumentException.class, () -> BrewCollectors.histogram(Long::longValue, -5));
            thrown(IllegalArgumentException.class, () -> BrewCollectors.histogram(null, 100));
        });

        test("partition_stats_factory_validation", () -> {
            thrown(IllegalArgumentException.class,
                    () -> BrewCollectors.partitionStats(null, Shot::millis));
            thrown(IllegalArgumentException.class,
                    () -> BrewCollectors.partitionStats(Shot::decaf, null));
        });

        test("brew_stats_is_a_value_type", () -> {
            eq("component access", 4L, new BrewStats(4, 100, 10, 40).count());
            eq("value equality", new BrewStats(2, 300, 100, 200), new BrewStats(2, 300, 100, 200));
        });

        test("collectors_compose_in_a_real_pipeline", () -> {
            List<Shot> slowest = fleet().stream()
                    .collect(BrewCollectors.topK(2, byMillis));
            eq("two slowest pulls", List.of(
                    new Shot("KV-02", 33000, true),
                    new Shot("LM-01", 31200, true)), slowest);
        });

        System.out.println(passed + " passed, " + failed + " failed");
        if (failed > 0) System.exit(1);
    }
}
