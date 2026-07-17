import java.math.BigDecimal;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Objects;

/**
 * Acceptance contract for the webhook-gateway JSON layer:
 * Json.parse / Json.write, the JsonValue model, and JsonParseException
 * positions. Run: java TestMain.java
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

    /** Asserts parse fails with exactly this position and message tail. */
    private static void rejects(String json, int line, int col, String what) {
        try {
            Json.parse(json);
        } catch (JsonParseException e) {
            eq("line for <" + json + ">", line, e.line());
            eq("col for <" + json + ">", col, e.col());
            eq("message for <" + json + ">", "line " + line + " col " + col + ": " + what,
                    e.getMessage());
            return;
        }
        throw new AssertionError("expected JsonParseException for <" + json + ">");
    }

    private static IllegalStateException wrongKind(String what, Runnable r) {
        try {
            r.run();
        } catch (IllegalStateException e) {
            return e;
        }
        throw new AssertionError(what + ": expected IllegalStateException");
    }

    public static void main(String[] args) {

        test("scalars_parse_with_correct_kinds", () -> {
            eq("true", JsonValue.Kind.BOOL, Json.parse("true").kind());
            yes("true value", Json.parse("true").asBool());
            yes("false value", !Json.parse("false").asBool());
            eq("null", JsonValue.Kind.NULL, Json.parse("null").kind());
            eq("string", "hi", Json.parse("\"hi\"").asString());
            eq("number kind", JsonValue.Kind.NUMBER, Json.parse("42").kind());
            yes("42", Json.parse("42").asNumber().compareTo(new BigDecimal("42")) == 0);
            yes("-3.25", Json.parse("-3.25").asNumber().compareTo(new BigDecimal("-3.25")) == 0);
        });

        test("nested_webhook_payload_parses", () -> {
            String doc = "{\n"
                    + "  \"id\": \"wh_20260712_0041\",\n"
                    + "  \"type\": \"shipment.scanned\",\n"
                    + "  \"attempt\": 2,\n"
                    + "  \"payload\": {\n"
                    + "    \"carrier\": \"pallet-express\",\n"
                    + "    \"scans\": [\n"
                    + "      {\"hub\": \"OAK-3\", \"temp_c\": -1.5},\n"
                    + "      {\"hub\": \"SLC-1\", \"temp_c\": 2.25}\n"
                    + "    ],\n"
                    + "    \"sealed\": false,\n"
                    + "    \"note\": null\n"
                    + "  }\n"
                    + "}";
            JsonValue root = Json.parse(doc);
            eq("root kind", JsonValue.Kind.OBJECT, root.kind());
            eq("field order", "[id, type, attempt, payload]", root.fields().keySet().toString());
            eq("id", "wh_20260712_0041", root.get("id").asString());
            JsonValue scans = root.get("payload").get("scans");
            eq("scans kind", JsonValue.Kind.ARRAY, scans.kind());
            eq("scan count", 2, scans.items().size());
            eq("second hub", "SLC-1", scans.items().get(1).get("hub").asString());
            eq("first temp lexeme", "-1.5", scans.items().get(0).get("temp_c").numberLexeme());
            eq("sealed", JsonValue.Kind.BOOL, root.get("payload").get("sealed").kind());
            eq("note", JsonValue.Kind.NULL, root.get("payload").get("note").kind());
            eq("absent key", null, root.get("nope"));
        });

        test("whitespace_between_tokens_is_fine", () -> {
            JsonValue v = Json.parse(" \t\r\n [ 1 , \"a\" , { \"k\" : null } ] \n ");
            eq("kind", JsonValue.Kind.ARRAY, v.kind());
            eq("size", 3, v.items().size());
        });

        test("string_escapes_decode", () -> {
            eq("simple escapes", "\" \\ / \b \f \n \r \t",
                    Json.parse("\"\\\" \\\\ \\/ \\b \\f \\n \\r \\t\"").asString());
            eq("unicode escape", "café", Json.parse("\"caf\\u00e9\"").asString());
            eq("uppercase hex", "café", Json.parse("\"caf\\u00E9\"").asString());
            eq("surrogate pair", "😀", Json.parse("\"\\ud83d\\ude00\"").asString());
            eq("raw non-ascii passes through", "smörgås", Json.parse("\"smörgås\"").asString());
        });

        test("numbers_keep_their_source_lexeme", () -> {
            eq("decimal", "1.50", Json.parse("1.50").numberLexeme());
            yes("value", Json.parse("1.50").asNumber().compareTo(new BigDecimal("1.5")) == 0);
            eq("exponent", "1e3", Json.parse("1e3").numberLexeme());
            yes("exp value", Json.parse("1e3").asNumber().compareTo(new BigDecimal("1000")) == 0);
            eq("negative zero", "-0", Json.parse("-0").numberLexeme());
            eq("full form", "-0.5e-2", Json.parse("-0.5e-2").numberLexeme());
        });

        test("deep_nesting_parses", () -> {
            StringBuilder deep = new StringBuilder();
            deep.append("[".repeat(64)).append("7").append("]".repeat(64));
            JsonValue v = Json.parse(deep.toString());
            for (int i = 0; i < 64; i++) {
                eq("depth " + i + " size", 1, v.items().size());
                v = v.items().get(0);
            }
            eq("core", "7", v.numberLexeme());
        });

        test("empty_object_and_array", () -> {
            eq("object", 0, Json.parse("{}").fields().size());
            eq("array", 0, Json.parse("[]").items().size());
            eq("with space", 0, Json.parse("{ }").fields().size());
        });

        test("duplicate_object_keys_are_rejected_with_position", () -> {
            rejects("{\"id\":1,\"id\":2}", 1, 9, "duplicate key 'id'");
        });

        test("empty_input_is_rejected", () -> {
            rejects("", 1, 1, "unexpected end of input");
            rejects("   ", 1, 4, "unexpected end of input");
        });

        test("values_cannot_start_with_stray_characters", () -> {
            rejects("?", 1, 1, "unexpected character '?'");
            rejects("+5", 1, 1, "unexpected character '+'");
            rejects(".5", 1, 1, "unexpected character '.'");
            rejects("[1,]", 1, 4, "unexpected character ']'");
        });

        test("malformed_numbers_are_rejected", () -> {
            rejects("01", 1, 1, "bad number '01'");
            rejects("5.", 1, 1, "bad number '5.'");
            rejects("1e", 1, 1, "bad number '1e'");
            rejects("1.2.3", 1, 1, "bad number '1.2.3'");
            rejects("-", 1, 1, "bad number '-'");
            rejects("[4, 007]", 1, 5, "bad number '007'");
        });

        test("bad_escapes_are_rejected_at_the_backslash", () -> {
            rejects("\"a\\qb\"", 1, 3, "bad escape '\\q'");
            rejects("\"\\u12G4\"", 1, 2, "bad unicode escape");
            rejects("\"\\u12\"", 1, 2, "bad unicode escape");
        });

        test("raw_control_characters_in_strings_are_rejected", () -> {
            rejects("\"a\nb\"", 1, 3, "raw control character in string");
            rejects("\"x" + (char) 1 + "\"", 1, 3, "raw control character in string");
        });

        test("unterminated_strings_are_rejected", () -> {
            rejects("\"abc", 1, 5, "unterminated string");
        });

        test("junk_literals_are_rejected", () -> {
            rejects("tru", 1, 1, "bad literal 'tru'");
            rejects("True", 1, 1, "bad literal 'True'");
            rejects("nulle", 1, 1, "bad literal 'nulle'");
            rejects("[truth]", 1, 2, "bad literal 'truth'");
        });

        test("structural_errors_carry_positions", () -> {
            rejects("{\"a\" 1}", 1, 6, "expected ':'");
            rejects("{\"a\":1 \"b\":2}", 1, 8, "expected ',' or '}'");
            rejects("[1 2]", 1, 4, "expected ',' or ']'");
            rejects("{\"a\":1,}", 1, 8, "expected string key");
            rejects("{1:2}", 1, 2, "expected string key");
        });

        test("trailing_data_is_rejected", () -> {
            rejects("{} x", 1, 4, "trailing data");
            rejects("1 1", 1, 3, "trailing data");
        });

        test("positions_track_lines_in_pretty_documents", () -> {
            rejects("{\n  \"a\": 1,\n  \"b\": 01\n}", 3, 8, "bad number '01'");
            rejects("[1,\n2,", 2, 3, "unexpected end of input");
        });

        test("writer_emits_compact_canonical_form", () -> {
            LinkedHashMap<String, JsonValue> amount = new LinkedHashMap<>();
            amount.put("currency", JsonValue.of("USD"));
            amount.put("value", JsonValue.number("124.50"));
            LinkedHashMap<String, JsonValue> event = new LinkedHashMap<>();
            event.put("event", JsonValue.of("charge.settled"));
            event.put("attempt", JsonValue.of(3L));
            event.put("live", JsonValue.of(true));
            event.put("tags", JsonValue.array(List.of(JsonValue.of("pci"), JsonValue.nul())));
            event.put("amount", JsonValue.object(amount));
            eq("canonical form",
                    "{\"event\":\"charge.settled\",\"attempt\":3,\"live\":true,"
                            + "\"tags\":[\"pci\",null],"
                            + "\"amount\":{\"currency\":\"USD\",\"value\":124.50}}",
                    Json.write(JsonValue.object(event)));
        });

        test("writer_escapes_exactly_what_json_requires", () -> {
            String raw = "say \"hi\" back\\slash \n bell" + (char) 7 + " café a/b";
            eq("escaped",
                    "\"say \\\"hi\\\" back\\\\slash \\n bell\\u0007 café a/b\"",
                    Json.write(JsonValue.of(raw)));
        });

        test("write_after_parse_preserves_order_and_lexemes", () -> {
            eq("normalized",
                    "{\"b\":1.50,\"a\":[1e3,-0]}",
                    Json.write(Json.parse("{ \"b\" : 1.50 , \"a\" : [ 1e3, -0 ] }")));
        });

        test("round_trip_is_a_fixed_point", () -> {
            String doc = "{\"u\":\"\\ud83d\\ude00\",\"n\":[0.5e-2, 12, -0.001],"
                    + "\"deep\":{\"x\":[[]],\"y\":{}},\"s\":\"tab\\tquote\\\"\"}";
            String once = Json.write(Json.parse(doc));
            String twice = Json.write(Json.parse(once));
            eq("fixed point", once, twice);
            yes("compact", !once.contains(" "));
        });

        test("wrong_kind_accessors_explain_themselves", () -> {
            eq("string on number", "not a STRING: NUMBER",
                    wrongKind("asString", () -> Json.parse("42").asString()).getMessage());
            eq("items on object", "not an ARRAY: OBJECT",
                    wrongKind("items", () -> Json.parse("{}").items()).getMessage());
            eq("get on array", "not an OBJECT: ARRAY",
                    wrongKind("get", () -> Json.parse("[]").get("k")).getMessage());
            eq("bool on null", "not a BOOL: NULL",
                    wrongKind("asBool", () -> Json.parse("null").asBool()).getMessage());
            eq("number on string", "not a NUMBER: STRING",
                    wrongKind("asNumber", () -> Json.parse("\"1\"").asNumber()).getMessage());
        });

        test("number_factory_validates_lexemes", () -> {
            eq("kept verbatim", "10.250", Json.write(JsonValue.number("10.250")));
            try {
                JsonValue.number("01");
                throw new AssertionError("expected IllegalArgumentException");
            } catch (IllegalArgumentException e) {
                eq("message", "bad number lexeme '01'", e.getMessage());
            }
        });

        System.out.println(passed + " passed, " + failed + " failed");
        if (failed > 0) System.exit(1);
    }
}
