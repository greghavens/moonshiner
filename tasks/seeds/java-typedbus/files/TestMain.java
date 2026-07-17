import java.util.ArrayList;
import java.util.List;
import java.util.Objects;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;
import java.util.function.Consumer;

/**
 * Acceptance tests for the smart-home hub event dispatcher.
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

    interface HubEvent {}
    record MotionDetected(String room) implements HubEvent {}
    record DoorOpened(String door) implements HubEvent {}
    record BatteryLow(String device, int percent) implements HubEvent {}

    public static void main(String[] args) {

        test("exact_type_delivery_and_invoked_count", () -> {
            HubBus bus = new HubBus();
            List<String> rooms = new ArrayList<>();
            bus.subscribe(MotionDetected.class, 0, e -> rooms.add(e.room()));
            eq("one handler invoked", 1, bus.publish(new MotionDetected("kitchen")));
            eq("payload delivered typed", List.of("kitchen"), rooms);
            eq("unrelated event invokes nobody", 0, bus.publish(new DoorOpened("front")));
            eq("rooms unchanged", List.of("kitchen"), rooms);
        });

        test("supertype_and_object_tokens_receive_polymorphically", () -> {
            HubBus bus = new HubBus();
            List<String> log = new ArrayList<>();
            bus.subscribe(HubEvent.class, 0, e -> log.add("hub:" + e.getClass().getSimpleName()));
            bus.subscribe(Object.class, 0, e -> log.add("any:" + e.getClass().getSimpleName()));
            eq("both see a motion event", 2, bus.publish(new MotionDetected("den")));
            eq("only Object sees a bare string", 1, bus.publish("heartbeat"));
            eq("log", List.of("hub:MotionDetected", "any:MotionDetected", "any:String"), log);
        });

        test("higher_priority_first_ties_by_registration_order", () -> {
            HubBus bus = new HubBus();
            List<String> log = new ArrayList<>();
            bus.subscribe(MotionDetected.class, 5, e -> log.add("A"));
            bus.subscribe(MotionDetected.class, 10, e -> log.add("B"));
            bus.subscribe(MotionDetected.class, 5, e -> log.add("C"));
            bus.subscribe(MotionDetected.class, 1, e -> log.add("D"));
            eq("all four invoked", 4, bus.publish(new MotionDetected("hall")));
            eq("order", List.of("B", "A", "C", "D"), log);
        });

        test("priority_interleaves_across_different_type_tokens", () -> {
            HubBus bus = new HubBus();
            List<String> log = new ArrayList<>();
            bus.subscribe(MotionDetected.class, 1, e -> log.add("exact-low"));
            bus.subscribe(HubEvent.class, 9, e -> log.add("super-high"));
            bus.subscribe(Object.class, 5, e -> log.add("any-mid"));
            eq("three invoked", 3, bus.publish(new MotionDetected("porch")));
            eq("order ignores token specificity, honors priority",
                    List.of("super-high", "any-mid", "exact-low"), log);
        });

        test("cancel_is_idempotent_and_stops_delivery", () -> {
            HubBus bus = new HubBus();
            AtomicInteger hits = new AtomicInteger();
            Subscription sub = bus.subscribe(DoorOpened.class, 0, e -> hits.incrementAndGet());
            yes("active before cancel", sub.active());
            eq("count before cancel", 1, bus.listenerCount(DoorOpened.class));
            yes("first cancel returns true", sub.cancel());
            yes("second cancel returns false", !sub.cancel());
            yes("inactive after cancel", !sub.active());
            eq("count after cancel", 0, bus.listenerCount(DoorOpened.class));
            eq("publish reaches nobody", 0, bus.publish(new DoorOpened("garage")));
            eq("handler never ran", 0, hits.get());
        });

        test("once_listener_fires_exactly_once", () -> {
            HubBus bus = new HubBus();
            AtomicInteger hits = new AtomicInteger();
            Subscription sub = bus.subscribeOnce(BatteryLow.class, 0, e -> hits.incrementAndGet());
            eq("first publish invokes it", 1, bus.publish(new BatteryLow("sensor-7", 9)));
            eq("second publish finds nobody", 0, bus.publish(new BatteryLow("sensor-7", 8)));
            eq("exactly one invocation", 1, hits.get());
            yes("consumed subscription is inactive", !sub.active());
        });

        test("once_is_deregistered_before_its_handler_runs", () -> {
            HubBus bus = new HubBus();
            AtomicInteger hits = new AtomicInteger();
            AtomicReference<Subscription> self = new AtomicReference<>();
            AtomicReference<Boolean> activeInside = new AtomicReference<>();
            AtomicInteger recursiveInvoked = new AtomicInteger(-1);
            Subscription sub = bus.subscribeOnce(MotionDetected.class, 0, e -> {
                hits.incrementAndGet();
                activeInside.set(self.get().active());
                recursiveInvoked.set(bus.publish(new MotionDetected("recursive")));
            });
            self.set(sub);
            eq("outer publish invoked one handler", 1, bus.publish(new MotionDetected("attic")));
            eq("already inactive inside its own handler", Boolean.FALSE, activeInside.get());
            eq("recursive publish re-invoked nobody", 0, recursiveInvoked.get());
            eq("handler body ran exactly once", 1, hits.get());
        });

        test("listener_subscribed_during_dispatch_misses_current_event", () -> {
            HubBus bus = new HubBus();
            List<String> log = new ArrayList<>();
            bus.subscribe(MotionDetected.class, 10, e -> {
                log.add("first:" + e.room());
                if (e.room().equals("lobby")) {
                    bus.subscribe(MotionDetected.class, 99, ev -> log.add("late:" + ev.room()));
                }
            });
            eq("current event reaches only the original listener", 1,
                    bus.publish(new MotionDetected("lobby")));
            eq("next event reaches both, new priority honored", 2,
                    bus.publish(new MotionDetected("stairs")));
            eq("log", List.of("first:lobby", "late:stairs", "first:stairs"), log);
        });

        test("listener_cancelled_during_dispatch_is_skipped_and_not_counted", () -> {
            HubBus bus = new HubBus();
            List<String> log = new ArrayList<>();
            AtomicReference<Subscription> victim = new AtomicReference<>();
            bus.subscribe(MotionDetected.class, 10, e -> {
                log.add("canceller");
                victim.get().cancel();
            });
            bus.subscribe(MotionDetected.class, 5, e -> log.add("survivor"));
            victim.set(bus.subscribe(MotionDetected.class, 1, e -> log.add("victim")));
            eq("victim skipped, two invoked", 2, bus.publish(new MotionDetected("hall")));
            eq("log", List.of("canceller", "survivor"), log);
            yes("victim inactive afterwards", !victim.get().active());
        });

        test("nested_publish_runs_depth_first", () -> {
            HubBus bus = new HubBus();
            List<String> log = new ArrayList<>();
            AtomicInteger innerCount = new AtomicInteger(-1);
            bus.subscribe(MotionDetected.class, 10, e -> {
                log.add("motion-1");
                innerCount.set(bus.publish(new DoorOpened("patio")));
            });
            bus.subscribe(MotionDetected.class, 1, e -> log.add("motion-2"));
            bus.subscribe(DoorOpened.class, 0, e -> log.add("door-1"));
            eq("outer publish counts only motion listeners", 2,
                    bus.publish(new MotionDetected("deck")));
            eq("inner publish counted separately", 1, innerCount.get());
            eq("depth-first interleaving", List.of("motion-1", "door-1", "motion-2"), log);
        });

        test("handler_exception_propagates_and_bus_survives", () -> {
            HubBus bus = new HubBus();
            List<String> log = new ArrayList<>();
            IllegalStateException boom = new IllegalStateException("relay stuck");
            Subscription thrower = bus.subscribe(DoorOpened.class, 10, e -> { throw boom; });
            bus.subscribe(DoorOpened.class, 1, e -> log.add("after"));
            IllegalStateException seen = thrown(IllegalStateException.class,
                    () -> bus.publish(new DoorOpened("side")));
            yes("the handler's own exception surfaces", seen == boom);
            eq("later listener was not reached", List.of(), log);
            yes("thrower can be cancelled afterwards", thrower.cancel());
            eq("bus still dispatches afterwards", 1, bus.publish(new DoorOpened("side")));
            eq("log after recovery", List.of("after"), log);
        });

        test("throwing_once_listener_stays_consumed", () -> {
            HubBus bus = new HubBus();
            AtomicInteger hits = new AtomicInteger();
            bus.subscribeOnce(BatteryLow.class, 0, e -> {
                hits.incrementAndGet();
                throw new IllegalStateException("dead battery handler");
            });
            thrown(IllegalStateException.class, () -> bus.publish(new BatteryLow("cam", 3)));
            eq("nobody left on the topic", 0, bus.publish(new BatteryLow("cam", 2)));
            eq("ran once in total", 1, hits.get());
        });

        test("listener_count_is_scoped_to_the_exact_token", () -> {
            HubBus bus = new HubBus();
            bus.subscribe(MotionDetected.class, 0, e -> {});
            Subscription second = bus.subscribe(MotionDetected.class, 0, e -> {});
            bus.subscribe(HubEvent.class, 0, e -> {});
            eq("motion token", 2, bus.listenerCount(MotionDetected.class));
            eq("hub token", 1, bus.listenerCount(HubEvent.class));
            eq("door token", 0, bus.listenerCount(DoorOpened.class));
            second.cancel();
            eq("motion token after cancel", 1, bus.listenerCount(MotionDetected.class));
        });

        test("once_cancelled_before_publish_never_fires", () -> {
            HubBus bus = new HubBus();
            AtomicInteger hits = new AtomicInteger();
            Subscription sub = bus.subscribeOnce(MotionDetected.class, 0, e -> hits.incrementAndGet());
            yes("cancel wins", sub.cancel());
            eq("no delivery", 0, bus.publish(new MotionDetected("shed")));
            eq("handler never ran", 0, hits.get());
            yes("cancel after cancel is false", !sub.cancel());
        });

        test("null_arguments_are_rejected", () -> {
            HubBus bus = new HubBus();
            eq("null type message", "type must not be null",
                    thrown(IllegalArgumentException.class,
                            () -> bus.subscribe(null, 0, e -> {})).getMessage());
            eq("null handler message", "handler must not be null",
                    thrown(IllegalArgumentException.class,
                            () -> bus.subscribe(MotionDetected.class, 0, null)).getMessage());
            eq("null once handler message", "handler must not be null",
                    thrown(IllegalArgumentException.class,
                            () -> bus.subscribeOnce(MotionDetected.class, 0, null)).getMessage());
            eq("null event message", "event must not be null",
                    thrown(IllegalArgumentException.class, () -> bus.publish(null)).getMessage());
            eq("null count token message", "type must not be null",
                    thrown(IllegalArgumentException.class,
                            () -> bus.listenerCount(null)).getMessage());
        });

        test("contravariant_handlers_compile_and_receive", () -> {
            HubBus bus = new HubBus();
            List<String> log = new ArrayList<>();
            Consumer<Object> anyHandler = o -> log.add("obj:" + o.getClass().getSimpleName());
            Consumer<HubEvent> hubHandler = e -> log.add("hub:" + e.getClass().getSimpleName());
            bus.subscribe(MotionDetected.class, 2, anyHandler);
            bus.subscribe(MotionDetected.class, 1, hubHandler);
            eq("both contravariant handlers invoked", 2, bus.publish(new MotionDetected("attic")));
            eq("log", List.of("obj:MotionDetected", "hub:MotionDetected"), log);
        });

        System.out.println(passed + " passed, " + failed + " failed");
        if (failed > 0) System.exit(1);
    }
}
