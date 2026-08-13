import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

public final class TestMain {
    private static final String TOKEN = "test-token-90";
    private static final String SECOND_TOKEN = "OpsToken quoted-token-90";
    private static final String SECOND_ID = "22222222-2222-2222-2222-222222222222";
    private static final String CREATE_BODY = "{\"key\":\"nightly-maintenance\",\"schedule\":{"
            + "\"hour\":2,\"minuteOfTheHour\":15,\"duration\":60,\"scheduleType\":\"ONCE\"}}";
    private static final String UPDATE_BODY = "{\"id\":\"" + ContractPinnedMock.CREATED_ID
            + "\",\"key\":\"nightly-maintenance\",\"schedule\":{"
            + "\"hour\":2,\"minuteOfTheHour\":15,\"duration\":90,\"scheduleType\":\"ONCE\"}}";
    private static final String SPECIAL_KEY = "daily \"quoted\" \\ \u2603\nkey";
    private static final String CREATE_OPTIONAL_BODY = "{\"key\":\"daily \\\"quoted\\\" "
            + "\\\\ \u2603\\nkey\",\"schedule\":{\"hour\":23,\"minuteOfTheHour\":59,"
            + "\"duration\":45,\"scheduleType\":\"DAILY\","
            + "\"startDate\":\"08/12/2026 \\\"start\\\"\\n\","
            + "\"timeZone\":\"America/Chicago\\\\Central\",\"expireRuns\":3}}";
    private static final String UPDATE_OPTIONAL_BODY = "{\"id\":\"" + SECOND_ID
            + "\",\"key\":\"daily \\\"quoted\\\" \\\\ \u2603\\nkey\",\"schedule\":{"
            + "\"hour\":0,\"minuteOfTheHour\":1,\"duration\":120,"
            + "\"scheduleType\":\"YEARLY\",\"expirationDate\":\"08/12/2027\"}}";
    private static final String SECOND_CREATE_RESPONSE = "{\"key\":\"server copy\","
            + "\"schedule\":{\"hour\":23,\"minuteOfTheHour\":59,\"duration\":45,"
            + "\"scheduleType\":\"DAILY\"},\"id\" : \"" + SECOND_ID + "\"}";
    private static final String SECOND_UPDATE_RESPONSE = "{\"id\":\"" + SECOND_ID
            + "\",\"updated\":true}";
    private static final String CREATE_ERROR_RESPONSE = "{\"error\":\"duplicate key\"}";

    public static void main(String[] args) throws Exception {
        Path root = Path.of(args.length == 0 ? "." : args[0]).toAbsolutePath().normalize();
        testPartialFailure(root);
        testOptionalValuesAndSuccess(root);
        testCreateHttpFailure(root);
        System.out.println("PASS: contract, wire shape, omission, and partial-failure report");
    }

    private static void testPartialFailure(Path root) throws Exception {
        try (ContractPinnedMock mock = new ContractPinnedMock(root.resolve("docs/contract.json"))) {
            mock.start();

            VcfOperationsClient.Schedule requested = new VcfOperationsClient.Schedule(
                    2, 15, 60, "ONCE", null, null, null, null);
            VcfOperationsClient.Schedule replacement = new VcfOperationsClient.Schedule(
                    2, 15, 90, "ONCE", null, null, null, null);
            VcfOperationsClient client = new VcfOperationsClient(mock.applianceUri(), TOKEN);

            VcfOperationsClient.ChangeReport report = client.applyMaintenanceScheduleChange(
                    "nightly-maintenance", requested, replacement);

            assertStep(report.create(), "createMaintenanceSchedules", 201, true,
                    ContractPinnedMock.CREATED_ID);
            assertStep(report.update(), "updateMaintenanceSchedules", 404, false,
                    ContractPinnedMock.CREATED_ID);
            check(report.create().responseBody().contains(ContractPinnedMock.CREATED_ID),
                    "create response body was not preserved");
            check(report.update().responseBody().isEmpty(),
                    "empty update error body was not preserved exactly");

            List<ContractPinnedMock.RequestEntry> requests = mock.requestLog();
            check(requests.size() == 2, "expected exactly two requests, got " + requests.size());
            assertRequest(requests.get(0), "POST", TOKEN, CREATE_BODY);
            assertRequest(requests.get(1), "PUT", TOKEN, UPDATE_BODY);
            assertAllOptionalFieldsAbsent(requests.get(0));
            assertAllOptionalFieldsAbsent(requests.get(1));
        }
    }

    private static void testOptionalValuesAndSuccess(Path root) throws Exception {
        try (ContractPinnedMock mock = new ContractPinnedMock(
                root.resolve("docs/contract.json"),
                201,
                SECOND_CREATE_RESPONSE,
                200,
                SECOND_UPDATE_RESPONSE)) {
            mock.start();

            VcfOperationsClient.Schedule requested = new VcfOperationsClient.Schedule(
                    23, 59, 45, "DAILY", "08/12/2026 \"start\"\n", "",
                    "America/Chicago\\Central", 3);
            VcfOperationsClient.Schedule replacement = new VcfOperationsClient.Schedule(
                    0, 1, 120, "YEARLY", "", "08/12/2027", "", null);
            VcfOperationsClient client = new VcfOperationsClient(mock.applianceUri(), SECOND_TOKEN);

            VcfOperationsClient.ChangeReport report = client.applyMaintenanceScheduleChange(
                    SPECIAL_KEY, requested, replacement);

            assertStep(report.create(), "createMaintenanceSchedules", 201, true, SECOND_ID);
            assertStep(report.update(), "updateMaintenanceSchedules", 200, true, SECOND_ID);
            check(SECOND_CREATE_RESPONSE.equals(report.create().responseBody()),
                    "successful create response body was not preserved exactly");
            check(SECOND_UPDATE_RESPONSE.equals(report.update().responseBody()),
                    "successful update response body was not preserved exactly");

            List<ContractPinnedMock.RequestEntry> requests = mock.requestLog();
            check(requests.size() == 2,
                    "optional-value case expected exactly two requests, got " + requests.size());
            assertRequest(requests.get(0), "POST", SECOND_TOKEN, CREATE_OPTIONAL_BODY);
            assertRequest(requests.get(1), "PUT", SECOND_TOKEN, UPDATE_OPTIONAL_BODY);
            check(!requests.get(0).bodyUtf8().contains("\"expirationDate\""),
                    "create emitted an empty expirationDate");
            check(!requests.get(1).bodyUtf8().contains("\"startDate\""),
                    "update emitted an empty startDate");
            check(!requests.get(1).bodyUtf8().contains("\"timeZone\""),
                    "update emitted an empty timeZone");
        }
    }

    private static void testCreateHttpFailure(Path root) throws Exception {
        try (ContractPinnedMock mock = new ContractPinnedMock(
                root.resolve("docs/contract.json"),
                422,
                CREATE_ERROR_RESPONSE,
                200,
                SECOND_UPDATE_RESPONSE)) {
            mock.start();

            VcfOperationsClient.Schedule schedule = new VcfOperationsClient.Schedule(
                    1, 2, 30, "ONCE", null, null, null, null);
            VcfOperationsClient client = new VcfOperationsClient(mock.applianceUri(), TOKEN);
            VcfOperationsClient.ChangeReport report = client.applyMaintenanceScheduleChange(
                    "duplicate", schedule, schedule);

            assertStep(report.create(), "createMaintenanceSchedules", 422, false, null);
            check(CREATE_ERROR_RESPONSE.equals(report.create().responseBody()),
                    "create HTTP error response body was not preserved exactly");
            check(report.update() == null, "update was reported even though it was not attempted");
            List<ContractPinnedMock.RequestEntry> requests = mock.requestLog();
            check(requests.size() == 1,
                    "create failure must stop before update; got " + requests.size() + " requests");
            assertRequest(requests.get(0), "POST", TOKEN,
                    "{\"key\":\"duplicate\",\"schedule\":{\"hour\":1,"
                            + "\"minuteOfTheHour\":2,\"duration\":30,"
                            + "\"scheduleType\":\"ONCE\"}}");
            assertAllOptionalFieldsAbsent(requests.get(0));
        }
    }

    private static void assertStep(
            VcfOperationsClient.StepResult actual,
            String operationId,
            int status,
            boolean succeeded,
            String resourceId) {
        check(actual != null, operationId + " result is missing");
        check(operationId.equals(actual.operationId()), "wrong operationId for " + operationId);
        check(actual.statusCode() == status, "wrong status for " + operationId);
        check(actual.succeeded() == succeeded, "wrong success state for " + operationId);
        check(java.util.Objects.equals(resourceId, actual.resourceId()),
                "wrong resource id for " + operationId);
    }

    private static void assertRequest(
            ContractPinnedMock.RequestEntry request,
            String method,
            String token,
            String expectedBody) {
        check(method.equals(request.method()), "wrong method for " + method + " request");
        check(ContractPinnedMock.WIRE_PATH.equals(request.rawPath()),
                "wrong path for " + method + " request: " + request.rawPath());
        check(request.rawQuery() == null, method + " request unexpectedly had a query string");
        check(token.equals(request.firstHeader("Authorization")),
                "wrong Authorization header for " + method);
        check("application/json".equals(request.firstHeader("Content-Type")),
                "wrong Content-Type for " + method);
        check("application/json".equals(request.firstHeader("Accept")),
                "wrong Accept for " + method);
        check(parseJson(expectedBody).equals(parseJson(request.bodyUtf8())),
                method + " body mismatch\nexpected: " + expectedBody + "\nactual:   " + request.bodyUtf8());
        check(request.bodyUtf8().getBytes(StandardCharsets.UTF_8).length
                        == request.body().length,
                method + " body was not UTF-8");
        check(!request.bodyUtf8().contains(":null"), method + " emitted a null placeholder");
        check(!request.bodyUtf8().contains(":\"\""), method + " emitted an empty-string placeholder");
        check(!request.bodyUtf8().contains(":[]"), method + " emitted an empty-array placeholder");
    }

    private static void assertAllOptionalFieldsAbsent(ContractPinnedMock.RequestEntry request) {
        for (String unset : List.of(
                "recurrence", "dayOfTheMonth", "daysOfTheMonth", "weeksOfTheMonth",
                "daysOfTheWeek", "month", "months", "startDate", "expirationDate",
                "timeZone", "expireRuns")) {
            check(!request.bodyUtf8().contains("\"" + unset + "\""),
                    request.method() + " emitted unset optional field " + unset);
        }
    }

    private static Object parseJson(String input) {
        return new JsonParser(input).parseDocument();
    }

    /** Small strict parser used only to compare request JSON without imposing member order. */
    private static final class JsonParser {
        private final String input;
        private int index;

        private JsonParser(String input) {
            this.input = input;
        }

        private Object parseDocument() {
            Object value = parseValue();
            skipWhitespace();
            check(index == input.length(), "trailing data in JSON body");
            return value;
        }

        private Object parseValue() {
            skipWhitespace();
            check(index < input.length(), "unexpected end of JSON body");
            return switch (input.charAt(index)) {
                case '{' -> parseObject();
                case '[' -> parseArray();
                case '"' -> parseString();
                case 't' -> parseKeyword("true", Boolean.TRUE);
                case 'f' -> parseKeyword("false", Boolean.FALSE);
                case 'n' -> parseKeyword("null", null);
                default -> parseNumber();
            };
        }

        private Map<String, Object> parseObject() {
            index++;
            Map<String, Object> result = new LinkedHashMap<>();
            skipWhitespace();
            if (consume('}')) {
                return result;
            }
            while (true) {
                skipWhitespace();
                check(index < input.length() && input.charAt(index) == '"',
                        "JSON object key is not a string");
                String key = parseString();
                skipWhitespace();
                check(consume(':'), "JSON object key has no value");
                check(!result.containsKey(key), "duplicate JSON object key: " + key);
                result.put(key, parseValue());
                skipWhitespace();
                if (consume('}')) {
                    return result;
                }
                check(consume(','), "JSON object members are not comma-separated");
            }
        }

        private List<Object> parseArray() {
            index++;
            List<Object> result = new ArrayList<>();
            skipWhitespace();
            if (consume(']')) {
                return result;
            }
            while (true) {
                result.add(parseValue());
                skipWhitespace();
                if (consume(']')) {
                    return result;
                }
                check(consume(','), "JSON array values are not comma-separated");
            }
        }

        private String parseString() {
            check(consume('"'), "JSON string has no opening quote");
            StringBuilder result = new StringBuilder();
            while (index < input.length()) {
                char character = input.charAt(index++);
                if (character == '"') {
                    return result.toString();
                }
                check(character >= 0x20, "unescaped control character in JSON string");
                if (character != '\\') {
                    result.append(character);
                    continue;
                }
                check(index < input.length(), "unfinished JSON escape");
                char escaped = input.charAt(index++);
                switch (escaped) {
                    case '"', '\\', '/' -> result.append(escaped);
                    case 'b' -> result.append('\b');
                    case 'f' -> result.append('\f');
                    case 'n' -> result.append('\n');
                    case 'r' -> result.append('\r');
                    case 't' -> result.append('\t');
                    case 'u' -> result.append(parseUnicodeEscape());
                    default -> throw new AssertionError("invalid JSON escape: \\" + escaped);
                }
            }
            throw new AssertionError("JSON string has no closing quote");
        }

        private char parseUnicodeEscape() {
            check(index + 4 <= input.length(), "short JSON unicode escape");
            try {
                char value = (char) Integer.parseInt(input.substring(index, index + 4), 16);
                index += 4;
                return value;
            } catch (NumberFormatException exception) {
                throw new AssertionError("invalid JSON unicode escape", exception);
            }
        }

        private Object parseKeyword(String keyword, Object value) {
            check(input.startsWith(keyword, index), "invalid JSON value");
            index += keyword.length();
            return value;
        }

        private java.math.BigDecimal parseNumber() {
            int start = index;
            if (consume('-')) {
                check(index < input.length(), "unfinished JSON number");
            }
            if (consume('0')) {
                check(index == input.length() || !Character.isDigit(input.charAt(index)),
                        "leading zero in JSON number");
            } else {
                check(index < input.length() && input.charAt(index) >= '1'
                                && input.charAt(index) <= '9',
                        "invalid JSON value");
                while (index < input.length() && Character.isDigit(input.charAt(index))) {
                    index++;
                }
            }
            if (consume('.')) {
                int fraction = index;
                while (index < input.length() && Character.isDigit(input.charAt(index))) {
                    index++;
                }
                check(index > fraction, "JSON fraction has no digits");
            }
            if (index < input.length()
                    && (input.charAt(index) == 'e' || input.charAt(index) == 'E')) {
                index++;
                if (index < input.length()
                        && (input.charAt(index) == '+' || input.charAt(index) == '-')) {
                    index++;
                }
                int exponent = index;
                while (index < input.length() && Character.isDigit(input.charAt(index))) {
                    index++;
                }
                check(index > exponent, "JSON exponent has no digits");
            }
            try {
                return new java.math.BigDecimal(input.substring(start, index));
            } catch (NumberFormatException exception) {
                throw new AssertionError("invalid JSON number", exception);
            }
        }

        private void skipWhitespace() {
            while (index < input.length()) {
                char character = input.charAt(index);
                if (character != ' ' && character != '\n'
                        && character != '\r' && character != '\t') {
                    return;
                }
                index++;
            }
        }

        private boolean consume(char character) {
            if (index < input.length() && input.charAt(index) == character) {
                index++;
                return true;
            }
            return false;
        }
    }

    private static void check(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }
}
