import java.lang.reflect.RecordComponent;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Arrays;
import java.util.List;
import java.util.Objects;

public final class TestMain {
    private static int passed;
    private static int failed;

    interface Body { void run() throws Exception; }

    private static void test(String name, Body body) {
        try {
            body.run();
            passed++;
            System.out.println("PASS " + name);
        } catch (Throwable error) {
            failed++;
            System.out.println("FAIL " + name + ": " + error);
        }
    }

    private static void eq(String what, Object expected, Object actual) {
        if (!Objects.equals(expected, actual)) {
            throw new AssertionError(what + ": expected <" + expected + "> got <" + actual + ">");
        }
    }

    private static BookingEntity booked(String id) {
        return new BookingEntity(id, "Avery Ng", 3, 12000L, 34000L, "CONFIRMED");
    }

    private static BookingService service(BookingStore store, BookingPricing pricing) {
        return new BookingService(store, new BookingMapper(), pricing);
    }

    private static BookingPricing pricing(String id, long current) {
        BookingPricing pricing = new BookingPricing();
        pricing.setCurrentNightly(id, current);
        return pricing;
    }

    public static void main(String[] args) {
        test("aggregate_preserves_locked_terms_and_discounted_total", () -> {
            Booking booking = Booking.restore("BK-17", "Avery Ng", 3, 12000L, 34000L, "CONFIRMED");
            booking.extendStay(2, 19000L);
            eq("nights", 5, booking.nights());
            eq("locked nightly", 12000L, booking.nightlyCents());
            eq("existing total plus extension", 58000L, booking.totalCents());
        });

        test("rest_flow_persists_terms_and_keeps_json_stable", () -> {
            BookingStore store = new BookingStore();
            store.seed(booked("BK-17"));
            BookingPricing prices = pricing("BK-17", 17000L);
            String json = new BookingRestController(service(store, prices)).extend("BK-17", 2);
            eq("json",
                    "{\"id\":\"BK-17\",\"guestName\":\"Avery Ng\",\"nights\":5,"
                    + "\"nightlyCents\":12000,\"totalCents\":58000,\"status\":\"CONFIRMED\"}",
                    json);
            eq("entity", new BookingEntity("BK-17", "Avery Ng", 5, 12000L, 58000L,
                    "CONFIRMED"), store.load("BK-17"));
            eq("one save", 1, store.saves());
            eq("pricing seam retained", 1, prices.calls());
        });

        test("rest_json_escapes_string_fields_without_changing_field_order", () -> {
            String id = "BK-\"\\07";
            BookingStore store = new BookingStore();
            store.seed(new BookingEntity(id, "A\"Ng\\Ops\nDesk", 3, 12000L, 34000L,
                    "CONFIRMED"));
            String json = new BookingRestController(service(store, pricing(id, 17000L)))
                    .extend(id, 1);
            eq("escaped json",
                    "{\"id\":\"BK-\\\"\\\\07\",\"guestName\":\"A\\\"Ng\\\\Ops\\nDesk\","
                    + "\"nights\":4,\"nightlyCents\":12000,\"totalCents\":46000,"
                    + "\"status\":\"CONFIRMED\"}",
                    json);
        });

        test("cli_serialization_and_application_path_remain_stable", () -> {
            BookingStore store = new BookingStore();
            store.seed(booked("BK-CLI"));
            BookingCli cli = new BookingCli(service(store, pricing("BK-CLI", 15500L)));
            eq("cli", "extended BK-CLI: 5 nights at 12000 cents, total 58000, status CONFIRMED",
                    cli.run("extend BK-CLI 2"));
            eq("usage", "error: usage: extend <booking-id> <additional-nights>", cli.run("show"));
        });

        test("mapper_round_trip_preserves_every_entity_field", () -> {
            BookingMapper mapper = new BookingMapper();
            BookingEntity entity = new BookingEntity("BK-8", "Sam O'Neil", 4, 9900L, 37600L,
                    "CONFIRMED");
            eq("round trip", entity, mapper.toEntity(mapper.fromEntity(entity)));
            eq("dto", new BookingDto("BK-8", "Sam O'Neil", 4, 9900L, 37600L, "CONFIRMED"),
                    mapper.toDto(mapper.fromEntity(entity)));
        });

        test("entity_and_dto_record_component_schemas_are_pinned", () -> {
            List<String> expected = List.of("id:String", "guestName:String", "nights:int",
                    "nightlyCents:long", "totalCents:long", "status:String");
            List<String> entityComponents = Arrays.stream(BookingEntity.class.getRecordComponents())
                    .map(c -> c.getName() + ":" + c.getType().getSimpleName()).toList();
            List<String> dtoComponents = Arrays.stream(BookingDto.class.getRecordComponents())
                    .map(c -> c.getName() + ":" + c.getType().getSimpleName()).toList();
            eq("entity reflection", expected, entityComponents);
            eq("dto reflection", expected, dtoComponents);
            eq("protected schema fixture", String.join("\n", expected) + "\n",
                    Files.readString(Path.of("contracts/booking_entity.schema")));
        });

        test("invalid_extension_does_not_persist", () -> {
            BookingStore store = new BookingStore();
            store.seed(booked("BK-2"));
            try {
                service(store, pricing("BK-2", 17000L)).extend("BK-2", 0);
                throw new AssertionError("expected IllegalArgumentException");
            } catch (IllegalArgumentException error) {
                eq("message", "additional nights must be positive", error.getMessage());
            }
            eq("unchanged", booked("BK-2"), store.load("BK-2"));
            eq("no save", 0, store.saves());
        });

        test("negative_extension_is_rejected_without_mutation", () -> {
            Booking booking = Booking.restore("BK-NEG", "Nora", 2, 7000L, 13000L,
                    "CONFIRMED");
            try {
                booking.extendStay(-3, 19000L);
                throw new AssertionError("expected IllegalArgumentException");
            } catch (IllegalArgumentException error) {
                eq("message", "additional nights must be positive", error.getMessage());
            }
            eq("nights unchanged", 2, booking.nights());
            eq("nightly unchanged", 7000L, booking.nightlyCents());
            eq("total unchanged", 13000L, booking.totalCents());
        });

        test("non_confirmed_booking_cannot_be_extended", () -> {
            Booking booking = Booking.restore("BK-X", "Rae", 2, 8000L, 16000L, "CANCELLED");
            try {
                booking.extendStay(1, 9000L);
                throw new AssertionError("expected IllegalStateException");
            } catch (IllegalStateException error) {
                eq("message", "only confirmed bookings can be extended", error.getMessage());
            }
            eq("nights unchanged", 2, booking.nights());
            eq("total unchanged", 16000L, booking.totalCents());
        });

        test("extension_charge_overflow_is_atomic", () -> {
            Booking booking = Booking.restore("BK-MAX", "Max", 7, 10L,
                    Long.MAX_VALUE - 5L, "CONFIRMED");
            try {
                booking.extendStay(1, 10L);
                throw new AssertionError("expected ArithmeticException");
            } catch (ArithmeticException expected) {
                // checked arithmetic is part of the aggregate contract
            }
            eq("nights unchanged", 7, booking.nights());
            eq("nightly unchanged", 10L, booking.nightlyCents());
            eq("total unchanged", Long.MAX_VALUE - 5L, booking.totalCents());
        });

        test("locked_rate_multiplication_overflow_is_atomic", () -> {
            long locked = Long.MAX_VALUE / 2L + 1L;
            Booking booking = Booking.restore("BK-MULT", "Mina", 1, locked, 25L,
                    "CONFIRMED");
            try {
                booking.extendStay(2, 1L);
                throw new AssertionError("expected ArithmeticException");
            } catch (ArithmeticException expected) {
                // checked multiplication must finish before any field changes
            }
            eq("nights unchanged", 1, booking.nights());
            eq("nightly unchanged", locked, booking.nightlyCents());
            eq("total unchanged", 25L, booking.totalCents());
        });

        test("night_count_overflow_is_atomic", () -> {
            Booking booking = Booking.restore("BK-N", "Nia", Integer.MAX_VALUE, 1L, 1L,
                    "CONFIRMED");
            try {
                booking.extendStay(1, 1L);
                throw new AssertionError("expected ArithmeticException");
            } catch (ArithmeticException expected) {
                // expected
            }
            eq("nights unchanged", Integer.MAX_VALUE, booking.nights());
            eq("total unchanged", 1L, booking.totalCents());
        });

        System.out.println("checks: " + passed + " passed, " + failed + " failed");
        System.exit(failed == 0 ? 0 : 1);
    }
}
