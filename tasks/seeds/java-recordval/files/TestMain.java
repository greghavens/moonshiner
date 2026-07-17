import java.time.LocalDate;
import java.util.ArrayList;
import java.util.List;
import java.util.Objects;

/**
 * Acceptance tests for the drone flight-permit domain records.
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

    private static Geofence okZone() {
        return new Geofence(40.7128, -74.0060, 400, 90);
    }

    private static PermitRequest okPermit() {
        return new PermitRequest("DFP-00042", "NY1234", okZone(),
                LocalDate.of(2026, 5, 17), List.of("night-ops", "crowd"));
    }

    public static void main(String[] args) {

        test("geofence_valid_accessors", () -> {
            Geofence g = new Geofence(40.7128, -74.0060, 400, 90);
            eq("centerLat", 40.7128, g.centerLat());
            eq("centerLon", -74.0060, g.centerLon());
            eq("radiusM", 400, g.radiusM());
            eq("maxAltitudeM", 90, g.maxAltitudeM());
        });

        test("geofence_single_violation_message", () -> {
            ValidationErrors e = thrown(ValidationErrors.class,
                    () -> new Geofence(40.0, -74.0, 20, 90));
            eq("violations", List.of("radiusM must be between 50 and 5000"), e.violations());
            eq("message", "Geofence: radiusM must be between 50 and 5000", e.getMessage());
        });

        test("geofence_accumulates_all_violations_in_declaration_order", () -> {
            ValidationErrors e = thrown(ValidationErrors.class,
                    () -> new Geofence(-91.5, 200.25, 9000, 500));
            eq("violations", List.of(
                    "centerLat must be between -90.0 and 90.0",
                    "centerLon must be between -180.0 and 180.0",
                    "radiusM must be between 50 and 5000",
                    "maxAltitudeM must be between 10 and 120"), e.violations());
        });

        test("geofence_nan_coordinates_rejected", () -> {
            ValidationErrors e = thrown(ValidationErrors.class,
                    () -> new Geofence(Double.NaN, 0.0, 400, 90));
            eq("violations", List.of("centerLat must be between -90.0 and 90.0"), e.violations());
        });

        test("geofence_boundary_values_accepted", () -> {
            Geofence a = new Geofence(-90.0, 180.0, 50, 120);
            Geofence b = new Geofence(90.0, -180.0, 5000, 10);
            eq("a.radiusM", 50, a.radiusM());
            eq("b.maxAltitudeM", 10, b.maxAltitudeM());
        });

        test("geofence_wither_returns_new_validated_instance", () -> {
            Geofence g = okZone();
            Geofence w = g.withRadiusM(75);
            eq("new radius", 75, w.radiusM());
            eq("lat carried over", g.centerLat(), w.centerLat());
            eq("altitude carried over", g.maxAltitudeM(), w.maxAltitudeM());
            eq("original untouched", 400, g.radiusM());
        });

        test("geofence_wither_with_same_value_is_equal", () -> {
            Geofence g = okZone();
            Geofence w = g.withMaxAltitudeM(g.maxAltitudeM());
            eq("equals", g, w);
            eq("hashCode", g.hashCode(), w.hashCode());
        });

        test("geofence_wither_rejects_bad_value_with_accumulated_report", () -> {
            Geofence g = okZone();
            ValidationErrors e = thrown(ValidationErrors.class, () -> g.withRadiusM(10));
            eq("violations", List.of("radiusM must be between 50 and 5000"), e.violations());
            eq("original still intact", 400, g.radiusM());
        });

        test("permit_valid_accessors", () -> {
            PermitRequest p = okPermit();
            eq("permitId", "DFP-00042", p.permitId());
            eq("pilotLicense", "NY1234", p.pilotLicense());
            eq("zone", okZone(), p.zone());
            eq("flightDate", LocalDate.of(2026, 5, 17), p.flightDate());
            eq("waivers", List.of("night-ops", "crowd"), p.waivers());
        });

        test("permit_waivers_are_defensively_copied_and_unmodifiable", () -> {
            List<String> input = new ArrayList<>(List.of("night-ops", "crowd"));
            PermitRequest p = new PermitRequest("DFP-00042", "NY1234", okZone(),
                    LocalDate.of(2026, 5, 17), input);
            input.add("later-mutation");
            eq("record unaffected by caller mutation", List.of("night-ops", "crowd"), p.waivers());
            thrown(UnsupportedOperationException.class, () -> p.waivers().add("sneaky"));
        });

        test("permit_id_format_enforced", () -> {
            ValidationErrors shortDigits = thrown(ValidationErrors.class,
                    () -> new PermitRequest("DFP-1234", "NY1234", okZone(),
                            LocalDate.of(2026, 5, 17), List.of()));
            eq("short digits", List.of("permitId must match DFP-NNNNN"), shortDigits.violations());
            ValidationErrors lower = thrown(ValidationErrors.class,
                    () -> new PermitRequest("dfp-00042", "NY1234", okZone(),
                            LocalDate.of(2026, 5, 17), List.of()));
            eq("lowercase prefix", List.of("permitId must match DFP-NNNNN"), lower.violations());
        });

        test("permit_license_format_enforced", () -> {
            ValidationErrors e = thrown(ValidationErrors.class,
                    () -> new PermitRequest("DFP-00042", "ny12345", okZone(),
                            LocalDate.of(2026, 5, 17), List.of()));
            eq("violations", List.of("pilotLicense must match LLNNNN"), e.violations());
        });

        test("permit_all_nulls_reported_at_once_in_declaration_order", () -> {
            ValidationErrors e = thrown(ValidationErrors.class,
                    () -> new PermitRequest(null, null, null, null, null));
            eq("violations", List.of(
                    "permitId must not be null",
                    "pilotLicense must not be null",
                    "zone must not be null",
                    "flightDate must not be null",
                    "waivers must not be null"), e.violations());
            eq("message", "PermitRequest: permitId must not be null; "
                    + "pilotLicense must not be null; zone must not be null; "
                    + "flightDate must not be null; waivers must not be null", e.getMessage());
        });

        test("permit_mixed_violations_keep_field_declaration_order", () -> {
            ValidationErrors e = thrown(ValidationErrors.class,
                    () -> new PermitRequest("BAD-1", "NY1234", null,
                            LocalDate.of(2026, 5, 17), List.of("night-ops", "Crowd")));
            eq("violations", List.of(
                    "permitId must match DFP-NNNNN",
                    "zone must not be null",
                    "waivers[1] must be lowercase kebab-case"), e.violations());
        });

        test("permit_waiver_element_rules_reported_per_index", () -> {
            List<String> waivers = new ArrayList<>();
            waivers.add("night-ops");
            waivers.add(null);
            waivers.add("Night-Ops");
            waivers.add("-bad-");
            ValidationErrors e = thrown(ValidationErrors.class,
                    () -> new PermitRequest("DFP-00042", "NY1234", okZone(),
                            LocalDate.of(2026, 5, 17), waivers));
            eq("violations", List.of(
                    "waivers[1] must not be null",
                    "waivers[2] must be lowercase kebab-case",
                    "waivers[3] must be lowercase kebab-case"), e.violations());
        });

        test("permit_waiver_duplicates_reported_once", () -> {
            ValidationErrors e = thrown(ValidationErrors.class,
                    () -> new PermitRequest("DFP-00042", "NY1234", okZone(),
                            LocalDate.of(2026, 5, 17), List.of("night-ops", "crowd", "night-ops")));
            eq("violations", List.of("waivers must not contain duplicates"), e.violations());
        });

        test("permit_duplicate_message_comes_after_element_messages", () -> {
            ValidationErrors e = thrown(ValidationErrors.class,
                    () -> new PermitRequest("DFP-00042", "NY1234", okZone(),
                            LocalDate.of(2026, 5, 17), List.of("BAD", "BAD")));
            eq("violations", List.of(
                    "waivers[0] must be lowercase kebab-case",
                    "waivers[1] must be lowercase kebab-case",
                    "waivers must not contain duplicates"), e.violations());
        });

        test("permit_withers_produce_new_instances", () -> {
            PermitRequest p = okPermit();
            PermitRequest moved = p.withFlightDate(LocalDate.of(2026, 6, 1));
            eq("new date", LocalDate.of(2026, 6, 1), moved.flightDate());
            eq("original date untouched", LocalDate.of(2026, 5, 17), p.flightDate());
            PermitRequest relicensed = p.withPilotLicense("CA9876");
            eq("new license", "CA9876", relicensed.pilotLicense());
            PermitRequest rezoned = p.withZone(okZone().withRadiusM(200));
            eq("new zone radius", 200, rezoned.zone().radiusM());
            PermitRequest rewaived = p.withWaivers(List.of("bvlos"));
            eq("new waivers", List.of("bvlos"), rewaived.waivers());
        });

        test("permit_withers_revalidate", () -> {
            PermitRequest p = okPermit();
            ValidationErrors e = thrown(ValidationErrors.class,
                    () -> p.withWaivers(List.of("OK", "OK")));
            eq("violations", List.of(
                    "waivers[0] must be lowercase kebab-case",
                    "waivers[1] must be lowercase kebab-case",
                    "waivers must not contain duplicates"), e.violations());
            ValidationErrors z = thrown(ValidationErrors.class, () -> p.withZone(null));
            eq("zone violation", List.of("zone must not be null"), z.violations());
        });

        test("permit_wither_round_trip_is_equal", () -> {
            PermitRequest p = okPermit();
            PermitRequest same = p.withPilotLicense(p.pilotLicense())
                    .withFlightDate(p.flightDate())
                    .withWaivers(p.waivers());
            eq("equals", p, same);
            eq("hashCode", p.hashCode(), same.hashCode());
        });

        test("validation_errors_type_and_defensive_copy", () -> {
            List<String> source = new ArrayList<>(List.of("a happened", "b happened"));
            ValidationErrors e = new ValidationErrors("Widget", source);
            yes("is IllegalArgumentException", e instanceof IllegalArgumentException);
            eq("message", "Widget: a happened; b happened", e.getMessage());
            source.add("c happened");
            eq("defensive copy of violations", List.of("a happened", "b happened"), e.violations());
            thrown(UnsupportedOperationException.class, () -> e.violations().add("nope"));
        });

        test("permit_record_value_equality", () -> {
            PermitRequest a = okPermit();
            PermitRequest b = new PermitRequest("DFP-00042", "NY1234",
                    new Geofence(40.7128, -74.0060, 400, 90),
                    LocalDate.of(2026, 5, 17), List.of("night-ops", "crowd"));
            eq("equals", a, b);
            eq("hashCode", a.hashCode(), b.hashCode());
        });

        System.out.println(passed + " passed, " + failed + " failed");
        if (failed > 0) System.exit(1);
    }
}
