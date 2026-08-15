import java.math.BigDecimal;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Base64;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

public final class TestMain {
    private record Wire(
            String method,
            String path,
            String query,
            String authorization,
            String contentType,
            String accept,
            String body) {}

    public static void main(String[] args) throws Exception {
        if (args.length != 3) {
            throw new AssertionError("expected base URL, request-log path, and scenario");
        }

        switch (args[2]) {
            case "final-failure" -> finalFailure(args[0], Path.of(args[1]));
            case "full-success" -> fullSuccess(args[0], Path.of(args[1]));
            case "project-failure" -> projectFailure(args[0], Path.of(args[1]));
            case "zone-failure" -> zoneFailure(args[0], Path.of(args[1]));
            case "null-collections" -> nullCollections(args[0], Path.of(args[1]));
            case "empty-collections" -> emptyCollections(args[0], Path.of(args[1]));
            default -> throw new AssertionError("unknown scenario: " + args[2]);
        }
        System.out.println("PASS " + args[2]);
    }

    private static void finalFailure(String baseUrl, Path logPath) throws Exception {
        VcfAutomationClient client =
                new VcfAutomationClient(baseUrl, "seed-token", "2021-07-15");
        VcfAutomationClient.ProjectChange change = new VcfAutomationClient.ProjectChange(
                "proj alpha/7",
                "Platform Staging",
                null,
                null,
                List.of(new VcfAutomationClient.ZoneAssignment(
                        "zone-a", 1, null, null, null, null)),
                List.of(new VcfAutomationClient.Tag("environment", null)));

        VcfAutomationClient.ChangeReport report = client.applyProjectChange(change);
        check(!report.successful(), "report must reflect the final failure");
        check(report.steps().size() == 3, "all three attempted steps must be reported");

        VcfAutomationClient.StepResult first = report.steps().get(0);
        assertStep(first, "updateProject", 200, VcfAutomationClient.State.SUCCEEDED);
        assertNoDetails(first);

        VcfAutomationClient.StepResult second = report.steps().get(1);
        assertStep(
                second,
                "updateProjectZoneAssignments",
                202,
                VcfAutomationClient.State.ACCEPTED);
        check("zone-request-42".equals(second.requestId()), "request ID was not captured");
        check("INPROGRESS".equals(second.remoteStatus()), "remote status was not captured");
        check(second.messageId() == null && second.message() == null, "accepted step has error detail");

        VcfAutomationClient.StepResult third = report.steps().get(2);
        assertStep(
                third,
                "updateProjectResourceMetadata",
                400,
                VcfAutomationClient.State.FAILED);
        check("metadata.update.failed".equals(third.messageId()), "message ID was not captured");
        check(
                "resource metadata backend rejected the update".equals(third.message()),
                "error message was not captured");
        check(third.requestId() == null && third.remoteStatus() == null, "failed step has tracker detail");

        List<Wire> wire = readWire(logPath);
        check(wire.size() == 3, "unexpected request count: " + wire.size());
        assertCommon(wire.get(0), "PATCH", "/iaas/api/projects/proj%20alpha%2F7");
        assertQuery(wire.get(0), "apiVersion=2021-07-15");
        assertJson(wire.get(0).body(), "{\"name\":\"Platform Staging\"}", "wrong project body");

        assertCommon(wire.get(1), "PUT", "/iaas/api/projects/proj%20alpha%2F7/zones");
        assertQuery(wire.get(1), "apiVersion=2021-07-15");
        assertJson(
                wire.get(1).body(),
                "{\"zoneAssignmentSpecifications\":[{\"zoneId\":\"zone-a\",\"priority\":1}]}",
                "wrong zones body");

        assertCommon(
                wire.get(2),
                "PATCH",
                "/iaas/api/projects/proj%20alpha%2F7/resource-metadata");
        assertQuery(wire.get(2), "apiVersion=2021-07-15");
        assertJson(
                wire.get(2).body(),
                "{\"tags\":[{\"key\":\"environment\"}]}",
                "wrong metadata body");

        String allBodies = wire.stream().map(Wire::body).reduce("", String::concat);
        check(!allBodies.contains("description"), "unset description was serialized");
        check(!allBodies.contains("maxNumberInstances"), "unset zone limit was serialized");
        check(!allBodies.contains("\"value\""), "unset tag value was serialized");
        check(!wire.get(0).query().contains("validatePrincipals"), "unset query field was sent");
    }

    private static void fullSuccess(String baseUrl, Path logPath) throws Exception {
        VcfAutomationClient client =
                new VcfAutomationClient(baseUrl, "seed-token", "2021-07-15");
        VcfAutomationClient.ProjectChange change = new VcfAutomationClient.ProjectChange(
                "proj?#% ü",
                "Core \"Δ\"\nLab\\",
                "line1\nline2\t\b\f\r\u0001",
                true,
                List.of(
                        new VcfAutomationClient.ZoneAssignment(
                                "zone/\"α", null, 17L, 2048L, 8L, 99L),
                        new VcfAutomationClient.ZoneAssignment(
                                "zone-b", 0, null, null, null, null)),
                List.of(
                        new VcfAutomationClient.Tag("team\"\\\n", "R&D/東京"),
                        new VcfAutomationClient.Tag("empty", "")));

        VcfAutomationClient.ChangeReport report = client.applyProjectChange(change);
        check(report.successful(), "three successful/accepted steps must be successful");
        check(report.steps().size() == 3, "successful run lost a step");
        assertStep(
                report.steps().get(0),
                "updateProject",
                200,
                VcfAutomationClient.State.SUCCEEDED);
        assertStep(
                report.steps().get(1),
                "updateProjectZoneAssignments",
                202,
                VcfAutomationClient.State.ACCEPTED);
        assertStep(
                report.steps().get(2),
                "updateProjectResourceMetadata",
                200,
                VcfAutomationClient.State.SUCCEEDED);
        check(
                "zone-\"quoted\nid".equals(report.steps().get(1).requestId()),
                "escaped tracker ID was not decoded");
        check(
                "INPROGRESS/✓".equals(report.steps().get(1).remoteStatus()),
                "tracker status was not decoded");

        List<Wire> wire = readWire(logPath);
        check(wire.size() == 3, "unexpected successful request count: " + wire.size());
        String projectPath = "/iaas/api/projects/proj%3F%23%25%20%C3%BC";
        assertCommon(wire.get(0), "PATCH", projectPath);
        assertQuery(
                wire.get(0),
                "apiVersion=2021-07-15",
                "validatePrincipals=true");
        assertJson(
                wire.get(0).body(),
                "{\"name\":\"Core \\\"Δ\\\"\\nLab\\\\\",\"description\":\"line1\\nline2\\t\\b\\f\\r\\u0001\"}",
                "strings were not JSON escaped");

        assertCommon(wire.get(1), "PUT", projectPath + "/zones");
        assertQuery(wire.get(1), "apiVersion=2021-07-15");
        assertJson(
                wire.get(1).body(),
                "{\"zoneAssignmentSpecifications\":[{\"zoneId\":\"zone/\\\"α\",\"maxNumberInstances\":17,\"memoryLimitMB\":2048,\"cpuLimit\":8,\"storageLimitGB\":99},{\"zoneId\":\"zone-b\",\"priority\":0}]}",
                "zone fields were not encoded accurately");

        assertCommon(wire.get(2), "PATCH", projectPath + "/resource-metadata");
        assertQuery(wire.get(2), "apiVersion=2021-07-15");
        assertJson(
                wire.get(2).body(),
                "{\"tags\":[{\"key\":\"team\\\"\\\\\\n\",\"value\":\"R&D/東京\"},{\"key\":\"empty\",\"value\":\"\"}]}",
                "tag strings or explicit empty value were encoded incorrectly");
    }

    private static void projectFailure(String baseUrl, Path logPath) throws Exception {
        VcfAutomationClient client = new VcfAutomationClient(baseUrl, "seed-token", null);
        VcfAutomationClient.ProjectChange change = new VcfAutomationClient.ProjectChange(
                "blocked",
                "Blocked",
                "",
                false,
                List.of(),
                List.of());

        VcfAutomationClient.ChangeReport report = client.applyProjectChange(change);
        check(!report.successful(), "failed first step cannot be successful");
        check(report.steps().size() == 1, "client did not stop after project failure");
        VcfAutomationClient.StepResult failed = report.steps().get(0);
        assertStep(failed, "updateProject", 403, VcfAutomationClient.State.FAILED);
        check("project.denied/π".equals(failed.messageId()), "project message ID was lost");
        check(
                "denied \"by policy\"\nπ".equals(failed.message()),
                "escaped project error was not decoded");

        List<Wire> wire = readWire(logPath);
        check(wire.size() == 1, "later request followed project failure");
        assertCommon(wire.get(0), "PATCH", "/iaas/api/projects/blocked");
        assertQuery(wire.get(0), "validatePrincipals=false");
        assertJson(
                wire.get(0).body(),
                "{\"name\":\"Blocked\",\"description\":\"\"}",
                "explicit empty description was not preserved");
    }

    private static void zoneFailure(String baseUrl, Path logPath) throws Exception {
        VcfAutomationClient client = new VcfAutomationClient(baseUrl, "seed-token", null);
        VcfAutomationClient.ProjectChange change = new VcfAutomationClient.ProjectChange(
                "zone-blocked",
                "Zone Blocked",
                null,
                true,
                null,
                List.of(new VcfAutomationClient.Tag("unused", "unused")));

        VcfAutomationClient.ChangeReport report = client.applyProjectChange(change);
        check(!report.successful(), "zone failure cannot be successful");
        check(report.steps().size() == 2, "client did not stop after zone failure");
        assertStep(
                report.steps().get(0),
                "updateProject",
                200,
                VcfAutomationClient.State.SUCCEEDED);
        assertStep(
                report.steps().get(1),
                "updateProjectZoneAssignments",
                404,
                VcfAutomationClient.State.FAILED);
        check(
                "zones.not.found".equals(report.steps().get(1).messageId()),
                "zone message ID was lost");
        check("no eligible zones".equals(report.steps().get(1).message()), "zone message was lost");

        List<Wire> wire = readWire(logPath);
        check(wire.size() == 2, "metadata request followed zone failure");
        String projectPath = "/iaas/api/projects/zone-blocked";
        assertCommon(wire.get(0), "PATCH", projectPath);
        assertQuery(wire.get(0), "validatePrincipals=true");
        assertCommon(wire.get(1), "PUT", projectPath + "/zones");
        assertQuery(wire.get(1));
        assertJson(wire.get(1).body(), "{}", "null zone collection was not omitted");
    }

    private static void nullCollections(String baseUrl, Path logPath) throws Exception {
        VcfAutomationClient client = new VcfAutomationClient(baseUrl, "seed-token", null);
        VcfAutomationClient.ProjectChange change = new VcfAutomationClient.ProjectChange(
                "dot.segment", "Minimal", null, null, null, null);

        VcfAutomationClient.ChangeReport report = client.applyProjectChange(change);
        check(report.successful(), "null optional collections should still allow all operations");
        check(report.steps().size() == 3, "minimal run lost an operation");

        List<Wire> wire = readWire(logPath);
        check(wire.size() == 3, "minimal run sent the wrong request count");
        String projectPath = "/iaas/api/projects/dot.segment";
        assertCommon(wire.get(0), "PATCH", projectPath);
        assertCommon(wire.get(1), "PUT", projectPath + "/zones");
        assertCommon(wire.get(2), "PATCH", projectPath + "/resource-metadata");
        wire.forEach(TestMain::assertQuery);
        assertJson(wire.get(0).body(), "{\"name\":\"Minimal\"}", "wrong minimal project body");
        assertJson(wire.get(1).body(), "{}", "null zones should produce an empty object");
        assertJson(wire.get(2).body(), "{}", "null tags should produce an empty object");
    }

    private static void emptyCollections(String baseUrl, Path logPath) throws Exception {
        VcfAutomationClient client = new VcfAutomationClient(baseUrl, "seed-token", null);
        VcfAutomationClient.ProjectChange change = new VcfAutomationClient.ProjectChange(
                "empty-lists", "Clear Assignments", null, null, List.of(), List.of());

        VcfAutomationClient.ChangeReport report = client.applyProjectChange(change);
        check(report.successful(), "explicit empty collections should complete all operations");

        List<Wire> wire = readWire(logPath);
        check(wire.size() == 3, "empty-collection run sent the wrong request count");
        assertJson(
                wire.get(1).body(),
                "{\"zoneAssignmentSpecifications\":[]}",
                "an explicit empty zone list was treated as unset");
        assertJson(
                wire.get(2).body(),
                "{\"tags\":[]}",
                "an explicit empty tag list was treated as unset");
    }

    private static void assertStep(
            VcfAutomationClient.StepResult step,
            String operation,
            int statusCode,
            VcfAutomationClient.State state) {
        check(step.operation().equals(operation), "wrong operation: " + step.operation());
        check(step.statusCode() == statusCode, "wrong status for " + operation);
        check(step.state() == state, "wrong state for " + operation);
    }

    private static void assertNoDetails(VcfAutomationClient.StepResult step) {
        check(
                step.requestId() == null
                        && step.remoteStatus() == null
                        && step.messageId() == null
                        && step.message() == null,
                "successful step has stale detail");
    }

    private static void assertCommon(Wire wire, String method, String path) {
        check(wire.method().equals(method), "wrong method for " + path);
        check(
                URLDecoder.decode(wire.path(), StandardCharsets.UTF_8)
                        .equals(URLDecoder.decode(path, StandardCharsets.UTF_8)),
                "wrong path: " + wire.path());
        check(wire.authorization().equals("Bearer seed-token"), "wrong authorization header");
        check(wire.contentType().equals("application/json"), "wrong content type");
        check(wire.accept().equals("application/json"), "wrong accept header");
    }

    private static void assertQuery(Wire wire, String... expectedParameters) {
        List<String> actual = wire.query().isEmpty()
                ? List.of()
                : List.of(wire.query().split("&", -1));
        Set<String> unique = new HashSet<>(actual);
        check(
                actual.size() == expectedParameters.length
                        && unique.size() == actual.size()
                        && unique.equals(Set.of(expectedParameters)),
                "wrong query: " + wire.query());
    }

    private static void assertJson(String actual, String expected, String message) {
        Object actualValue = JsonParser.parse(actual);
        Object expectedValue = JsonParser.parse(expected);
        check(expectedValue.equals(actualValue), message + ": " + actual);
    }

    private static List<Wire> readWire(Path path) throws Exception {
        List<Wire> result = new ArrayList<>();
        for (String line : Files.readAllLines(path, StandardCharsets.UTF_8)) {
            String[] fields = line.split("\\t", -1);
            check(fields.length == 8, "malformed request log");
            String body = new String(Base64.getDecoder().decode(fields[7]), StandardCharsets.UTF_8);
            result.add(new Wire(fields[1], fields[2], fields[3], fields[4], fields[5], fields[6], body));
        }
        return result;
    }

    private static final class JsonParser {
        private final String text;
        private int offset;

        private JsonParser(String text) {
            this.text = text;
        }

        static Object parse(String text) {
            JsonParser parser = new JsonParser(text);
            Object value = parser.value();
            parser.whitespace();
            if (parser.offset != text.length()) {
                throw parser.invalid("trailing content");
            }
            return value;
        }

        private Object value() {
            whitespace();
            if (offset >= text.length()) {
                throw invalid("missing value");
            }
            return switch (text.charAt(offset)) {
                case '{' -> object();
                case '[' -> array();
                case '"' -> string();
                case 't' -> literal("true", Boolean.TRUE);
                case 'f' -> literal("false", Boolean.FALSE);
                case 'n' -> literal("null", null);
                default -> number();
            };
        }

        private Map<String, Object> object() {
            offset++;
            Map<String, Object> result = new LinkedHashMap<>();
            whitespace();
            if (take('}')) {
                return result;
            }
            while (true) {
                whitespace();
                if (offset >= text.length() || text.charAt(offset) != '"') {
                    throw invalid("object key must be a string");
                }
                String key = string();
                whitespace();
                require(':');
                if (result.containsKey(key)) {
                    throw invalid("duplicate object key");
                }
                result.put(key, value());
                whitespace();
                if (take('}')) {
                    return result;
                }
                require(',');
            }
        }

        private List<Object> array() {
            offset++;
            List<Object> result = new ArrayList<>();
            whitespace();
            if (take(']')) {
                return result;
            }
            while (true) {
                result.add(value());
                whitespace();
                if (take(']')) {
                    return result;
                }
                require(',');
            }
        }

        private String string() {
            require('"');
            StringBuilder result = new StringBuilder();
            while (offset < text.length()) {
                char current = text.charAt(offset++);
                if (current == '"') {
                    return result.toString();
                }
                if (current < 0x20) {
                    throw invalid("unescaped control character");
                }
                if (current != '\\') {
                    result.append(current);
                    continue;
                }
                if (offset >= text.length()) {
                    throw invalid("unfinished string escape");
                }
                char escaped = text.charAt(offset++);
                switch (escaped) {
                    case '"', '\\', '/' -> result.append(escaped);
                    case 'b' -> result.append('\b');
                    case 'f' -> result.append('\f');
                    case 'n' -> result.append('\n');
                    case 'r' -> result.append('\r');
                    case 't' -> result.append('\t');
                    case 'u' -> {
                        if (offset + 4 > text.length()) {
                            throw invalid("short Unicode escape");
                        }
                        try {
                            result.append((char) Integer.parseInt(text, offset, offset + 4, 16));
                        } catch (NumberFormatException malformed) {
                            throw invalid("malformed Unicode escape");
                        }
                        offset += 4;
                    }
                    default -> throw invalid("unsupported string escape");
                }
            }
            throw invalid("unterminated string");
        }

        private BigDecimal number() {
            int start = offset;
            if (take('-') && offset >= text.length()) {
                throw invalid("unfinished number");
            }
            if (take('0')) {
                if (offset < text.length() && Character.isDigit(text.charAt(offset))) {
                    throw invalid("leading zero in number");
                }
            } else {
                digits();
            }
            if (take('.')) {
                digits();
            }
            if (take('e') || take('E')) {
                if (!take('+')) {
                    take('-');
                }
                digits();
            }
            try {
                return new BigDecimal(text.substring(start, offset));
            } catch (NumberFormatException malformed) {
                throw invalid("malformed number");
            }
        }

        private void digits() {
            int start = offset;
            while (offset < text.length() && Character.isDigit(text.charAt(offset))) {
                offset++;
            }
            if (start == offset) {
                throw invalid("expected digit");
            }
        }

        private Object literal(String spelling, Object value) {
            if (!text.startsWith(spelling, offset)) {
                throw invalid("invalid literal");
            }
            offset += spelling.length();
            return value;
        }

        private void whitespace() {
            while (offset < text.length()
                    && (text.charAt(offset) == ' '
                            || text.charAt(offset) == '\n'
                            || text.charAt(offset) == '\r'
                            || text.charAt(offset) == '\t')) {
                offset++;
            }
        }

        private boolean take(char expected) {
            if (offset < text.length() && text.charAt(offset) == expected) {
                offset++;
                return true;
            }
            return false;
        }

        private void require(char expected) {
            if (!take(expected)) {
                throw invalid("expected '" + expected + "'");
            }
        }

        private AssertionError invalid(String message) {
            return new AssertionError("invalid JSON at " + offset + ": " + message + " in " + text);
        }
    }

    private static void check(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }
}
