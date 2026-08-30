import java.io.IOException;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.Base64;
import java.util.List;

public final class TestMain {
    private TestMain() {
    }

    public static void main(String[] args) throws Exception {
        if (args.length != 9) {
            throw new AssertionError("expected nine harness arguments");
        }
        String mode = args[0];
        URI baseUri = URI.create(args[1]);
        String sessionId = args[2];
        String clientToken = args[3];
        String libraryId = args[4];
        String name = args[5];
        String backingValue = args[6];
        Path requestLog = Path.of(args[7]);
        String serverError = args[8];

        VcenterLibraryClient client = new VcenterLibraryClient(
                baseUri,
                sessionId,
                Duration.ofSeconds(3));
        check(
                !Files.exists(requestLog) || Files.size(requestLog) == 0,
                "constructor performed network traffic");

        if (mode.equals("drop_after_commit")) {
            verifyLocalValidation(
                    client,
                    baseUri,
                    sessionId,
                    clientToken,
                    requestLog);
        }

        boolean other = mode.equals("other_empty");
        VcenterLibraryClient.StorageBacking backing =
                new VcenterLibraryClient.StorageBacking(
                        other
                                ? VcenterLibraryClient.BackingType.OTHER
                                : VcenterLibraryClient.BackingType.DATASTORE,
                        other ? null : backingValue,
                        other ? backingValue : null);
        VcenterLibraryClient.LibrarySpec spec =
                new VcenterLibraryClient.LibrarySpec(
                        name,
                        List.of(backing),
                        other ? "" : null);

        int expectedRequests;
        int expectedEffects;
        switch (mode) {
            case "drop_after_commit" -> {
                VcenterLibraryClient.CreateResult result =
                        client.createLocalLibrary(clientToken, spec);
                assertResult(result, libraryId, clientToken, 2);
                expectedRequests = 2;
                expectedEffects = 1;
            }
            case "other_empty" -> {
                VcenterLibraryClient.CreateResult result =
                        client.createLocalLibrary(clientToken, spec);
                assertResult(result, libraryId, clientToken, 1);
                expectedRequests = 1;
                expectedEffects = 1;
            }
            case "http_503" -> {
                try {
                    client.createLocalLibrary(clientToken, spec);
                    throw new AssertionError(
                            "HTTP response was incorrectly accepted");
                } catch (VcenterLibraryClient.VcenterApiException expected) {
                    check(expected.statusCode() == 503, "wrong HTTP status");
                    check(expected.attempts() == 1, "HTTP response was retried");
                    check(
                            expected.operationId().equals(
                                    VcenterLibraryClient.OPERATION_ID),
                            "wrong API exception operationId");
                    String expectedBody = "{"
                            + jsonString("message") + ":"
                            + jsonString(serverError)
                            + "}";
                    check(
                            java.util.Arrays.equals(
                                    expected.responseBody(),
                                    expectedBody.getBytes(
                                            StandardCharsets.UTF_8)),
                            "API exception did not preserve response bytes");
                    assertSanitized(expected, sessionId, clientToken, serverError);
                }
                expectedRequests = 1;
                expectedEffects = 0;
            }
            case "malformed_201" -> {
                try {
                    client.createLocalLibrary(clientToken, spec);
                    throw new AssertionError(
                            "malformed success response was accepted");
                } catch (VcenterLibraryClient.ProtocolException expected) {
                    check(expected.attempts() == 1, "protocol error was retried");
                    check(
                            expected.operationId().equals(
                                    VcenterLibraryClient.OPERATION_ID),
                            "wrong protocol exception operationId");
                    assertSanitized(expected, sessionId, clientToken, "{}");
                }
                expectedRequests = 1;
                expectedEffects = 1;
            }
            case "drop_every" -> {
                try {
                    client.createLocalLibrary(clientToken, spec);
                    throw new AssertionError(
                            "retry exhaustion was incorrectly accepted");
                } catch (
                        VcenterLibraryClient.RetryExhaustedException expected) {
                    check(expected.attempts() == 2, "wrong retry limit");
                    check(
                            expected.operationId().equals(
                                    VcenterLibraryClient.OPERATION_ID),
                            "wrong retry exception operationId");
                    assertSanitized(
                            expected,
                            sessionId,
                            clientToken,
                            serverError);
                }
                expectedRequests = 2;
                expectedEffects = 1;
            }
            default -> throw new AssertionError("unknown harness mode");
        }

        String expectedBody = "{"
                + jsonString("name") + ":" + jsonString(name) + ","
                + jsonString("storage_backings") + ":[{"
                + jsonString("type") + ":"
                + jsonString(other ? "OTHER" : "DATASTORE") + ","
                + jsonString(other ? "storage_uri" : "datastore_id")
                + ":" + jsonString(backingValue)
                + "}]"
                + (other
                        ? "," + jsonString("description") + ":"
                                + jsonString("")
                        : "")
                + "}";
        verifyWire(
                requestLog,
                expectedRequests,
                expectedEffects,
                sessionId,
                clientToken,
                expectedBody);
        System.out.println("TEST_MAIN_OK");
    }

    private static void verifyLocalValidation(
            VcenterLibraryClient client,
            URI baseUri,
            String sessionId,
            String clientToken,
            Path requestLog) throws Exception {
        expectIllegalArgument(
                () -> new VcenterLibraryClient(
                        baseUri.resolve("/api"),
                        sessionId,
                        Duration.ofSeconds(1)));
        expectIllegalArgument(
                () -> new VcenterLibraryClient(
                        URI.create(baseUri + "?unexpected=true"),
                        sessionId,
                        Duration.ofSeconds(1)));
        expectIllegalArgument(
                () -> new VcenterLibraryClient(
                        URI.create(
                                baseUri.getScheme()
                                + "://embedded@"
                                + baseUri.getRawAuthority()),
                        sessionId,
                        Duration.ofSeconds(1)));
        expectIllegalArgument(
                () -> new VcenterLibraryClient(
                        baseUri,
                        "unsafe session",
                        Duration.ofSeconds(1)));
        expectIllegalArgument(
                () -> new VcenterLibraryClient(
                        baseUri,
                        sessionId,
                        Duration.ZERO));

        String datastore = "datastore-validation";
        VcenterLibraryClient.StorageBacking validBacking =
                new VcenterLibraryClient.StorageBacking(
                        VcenterLibraryClient.BackingType.DATASTORE,
                        datastore,
                        null);
        expectIllegalArgument(
                () -> client.createLocalLibrary(
                        "A0000000-0000-4000-8000-000000000000",
                        new VcenterLibraryClient.LibrarySpec(
                                "library",
                                List.of(validBacking),
                                null)));
        expectIllegalArgument(
                () -> client.createLocalLibrary(clientToken, null));
        expectIllegalArgument(
                () -> client.createLocalLibrary(
                        clientToken,
                        new VcenterLibraryClient.LibrarySpec(
                                " ",
                                List.of(validBacking),
                                null)));
        expectIllegalArgument(
                () -> client.createLocalLibrary(
                        clientToken,
                        new VcenterLibraryClient.LibrarySpec(
                                "library",
                                List.of(),
                                null)));
        expectIllegalArgument(
                () -> client.createLocalLibrary(
                        clientToken,
                        new VcenterLibraryClient.LibrarySpec(
                                "library",
                                List.of(validBacking, validBacking),
                                null)));
        expectIllegalArgument(
                () -> client.createLocalLibrary(
                        clientToken,
                        new VcenterLibraryClient.LibrarySpec(
                                "library",
                                List.of(
                                        new VcenterLibraryClient.StorageBacking(
                                                VcenterLibraryClient
                                                        .BackingType.DATASTORE,
                                                datastore,
                                                "nfs://unexpected")),
                                null)));
        expectIllegalArgument(
                () -> client.createLocalLibrary(
                        clientToken,
                        new VcenterLibraryClient.LibrarySpec(
                                "library",
                                List.of(
                                        new VcenterLibraryClient.StorageBacking(
                                                VcenterLibraryClient
                                                        .BackingType.OTHER,
                                                null,
                                                "relative/path")),
                                null)));
        check(
                !Files.exists(requestLog) || Files.size(requestLog) == 0,
                "invalid local input performed network traffic");
    }

    private static void verifyWire(
            Path requestLog,
            int expectedRequests,
            int expectedEffects,
            String sessionId,
            String clientToken,
            String expectedBody) throws Exception {
        List<String> events = Files.readAllLines(
                requestLog,
                StandardCharsets.UTF_8);
        check(events.size() == expectedRequests, "unexpected request count");
        byte[] bodyBytes = expectedBody.getBytes(StandardCharsets.UTF_8);
        String bodyBase64 = Base64.getEncoder().encodeToString(bodyBytes);

        for (int index = 0; index < events.size(); index++) {
            String event = events.get(index);
            check(integerField(event, "seq") == index + 1, "sequence mismatch");
            check(
                    stringField(event, "operation_id").equals(
                            VcenterLibraryClient.OPERATION_ID),
                    "wrong operationId");
            check(stringField(event, "method").equals("POST"), "wrong method");
            check(
                    stringField(event, "raw_target").equals(
                            "/api/content/local-library"),
                    "raw target or query omission mismatch");
            check(
                    stringField(event, "session").equals(sessionId)
                            && integerField(event, "session_count") == 1,
                    "session header mismatch");
            check(
                    stringField(event, "client_token").equals(clientToken)
                            && integerField(
                                    event,
                                    "client_token_count") == 1,
                    "Client-Token header mismatch");
            check(
                    stringField(event, "accept").equals("application/json")
                            && integerField(event, "accept_count") == 1,
                    "Accept header mismatch");
            check(
                    stringField(event, "content_type").equals(
                            "application/json")
                            && integerField(
                                    event,
                                    "content_type_count") == 1,
                    "Content-Type header mismatch");
            check(
                    integerStringField(event, "content_length")
                            == bodyBytes.length
                            && integerField(
                                    event,
                                    "content_length_count") == 1
                            && integerField(
                                    event,
                                    "transfer_encoding_count") == 0,
                    "request framing mismatch");
            check(
                    integerField(event, "authorization_count") == 0,
                    "Authorization header must be absent");
            check(
                    integerField(event, "body_length") == bodyBytes.length
                            && stringField(event, "body_b64").equals(
                                    bodyBase64),
                    "body bytes, order, escaping, or omission mismatch");
            check(
                    booleanField(event, "new_effect") == (index == 0
                            && expectedEffects == 1),
                    "deduplication effect marker mismatch");
            check(
                    integerField(event, "effect_count") == expectedEffects,
                    "unexpected number of library effects");
        }
        if (expectedRequests == 2) {
            check(
                    events.get(0).replaceFirst("\"seq\":1", "\"seq\":2")
                            .replaceFirst(
                                    "\"new_effect\":true",
                                    "\"new_effect\":false")
                            .equals(events.get(1)),
                    "retry was not byte-identical at the request surface");
        }
    }

    private static void assertResult(
            VcenterLibraryClient.CreateResult result,
            String libraryId,
            String clientToken,
            int attempts) {
        check(
                result.operationId().equals(
                        VcenterLibraryClient.OPERATION_ID),
                "wrong result operationId");
        check(result.libraryId().equals(libraryId), "wrong library identifier");
        check(result.clientToken().equals(clientToken), "wrong result token");
        check(result.attempts() == attempts, "wrong result attempt count");
    }

    private static void assertSanitized(
            Exception exception,
            String sessionId,
            String clientToken,
            String privateText) {
        String rendered = exception.toString();
        check(!rendered.contains(sessionId), "exception exposed session ID");
        check(!rendered.contains(clientToken), "exception exposed client token");
        check(!rendered.contains(privateText), "exception exposed private text");
        check(exception.getCause() == null, "exception exposed transport cause");
    }

    private static void expectIllegalArgument(ThrowingAction action)
            throws Exception {
        try {
            action.run();
            throw new AssertionError("expected IllegalArgumentException");
        } catch (IllegalArgumentException expected) {
            // Expected.
        }
    }

    @FunctionalInterface
    private interface ThrowingAction {
        void run() throws Exception;
    }

    private static void check(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }

    private static String stringField(String object, String key) {
        int start = fieldValueStart(object, key);
        if (start >= object.length() || object.charAt(start) != '"') {
            throw new AssertionError("log field is not a string: " + key);
        }
        StringBuilder result = new StringBuilder();
        for (int index = start + 1; index < object.length(); index++) {
            char value = object.charAt(index);
            if (value == '"') {
                return result.toString();
            }
            if (value != '\\') {
                result.append(value);
                continue;
            }
            char escaped = object.charAt(++index);
            switch (escaped) {
                case '"', '\\', '/' -> result.append(escaped);
                case 'b' -> result.append('\b');
                case 'f' -> result.append('\f');
                case 'n' -> result.append('\n');
                case 'r' -> result.append('\r');
                case 't' -> result.append('\t');
                case 'u' -> {
                    int code = Integer.parseInt(
                            object.substring(index + 1, index + 5),
                            16);
                    result.append((char) code);
                    index += 4;
                }
                default -> throw new AssertionError("bad JSON escape in log");
            }
        }
        throw new AssertionError("unterminated JSON string in log");
    }

    private static int integerField(String object, String key) {
        int start = fieldValueStart(object, key);
        int end = start;
        while (end < object.length()
                && (object.charAt(end) == '-'
                    || Character.isDigit(object.charAt(end)))) {
            end++;
        }
        return Integer.parseInt(object.substring(start, end));
    }

    private static int integerStringField(String object, String key) {
        return Integer.parseInt(stringField(object, key));
    }

    private static boolean booleanField(String object, String key) {
        int start = fieldValueStart(object, key);
        if (object.startsWith("true", start)) {
            return true;
        }
        if (object.startsWith("false", start)) {
            return false;
        }
        throw new AssertionError("log field is not Boolean: " + key);
    }

    private static int fieldValueStart(String object, String key) {
        String marker = jsonString(key) + ":";
        int markerIndex = object.indexOf(marker);
        if (markerIndex < 0) {
            throw new AssertionError("missing log field: " + key);
        }
        return markerIndex + marker.length();
    }

    private static String jsonString(String value) {
        StringBuilder result = new StringBuilder("\"");
        for (int index = 0; index < value.length(); index++) {
            char item = value.charAt(index);
            switch (item) {
                case '"' -> result.append("\\\"");
                case '\\' -> result.append("\\\\");
                case '\b' -> result.append("\\b");
                case '\f' -> result.append("\\f");
                case '\n' -> result.append("\\n");
                case '\r' -> result.append("\\r");
                case '\t' -> result.append("\\t");
                default -> {
                    if (item < 0x20) {
                        result.append(String.format(
                                "\\u%04X",
                                (int) item));
                    } else {
                        result.append(item);
                    }
                }
            }
        }
        return result.append('"').toString();
    }
}
