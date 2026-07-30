import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.Base64;
import java.util.List;
import java.util.concurrent.atomic.AtomicInteger;

public final class TestMain {
    private TestMain() {
    }

    public static void main(String[] args) throws Exception {
        if (args.length != 7) {
            throw new AssertionError("expected seven harness arguments");
        }
        String apiRoot = args[0];
        String sessionId = args[1];
        String source = args[2];
        String name = args[3];
        String expectedTask = args[4];
        String expectedVm = args[5];
        Path requestLog = Path.of(args[6]);

        AtomicInteger sleepCount = new AtomicInteger();
        VcenterCloneClient.Sleeper sleeper = duration -> {
            check(
                    duration.equals(Duration.ofMillis(7)),
                    "wrong poll interval passed to sleeper");
            sleepCount.incrementAndGet();
        };

        VcenterCloneClient client = new VcenterCloneClient(
                apiRoot,
                sessionId,
                Duration.ofSeconds(3),
                Duration.ofMillis(7),
                8,
                sleeper);
        check(
                !Files.exists(requestLog) || Files.size(requestLog) == 0,
                "constructor performed network traffic");

        expectIllegalArgument(
                () -> new VcenterCloneClient(
                        apiRoot + "?unexpected=true",
                        sessionId,
                        Duration.ofSeconds(1),
                        Duration.ofMillis(1),
                        1));
        expectIllegalArgument(
                () -> new VcenterCloneClient(
                        apiRoot.replace("/api", "/rest"),
                        sessionId,
                        Duration.ofSeconds(1),
                        Duration.ofMillis(1),
                        1));
        expectIllegalArgument(
                () -> new VcenterCloneClient(
                        apiRoot,
                        "bad session",
                        Duration.ofSeconds(1),
                        Duration.ofMillis(1),
                        1));
        expectIllegalArgument(
                () -> new VcenterCloneClient(
                        apiRoot,
                        sessionId,
                        Duration.ZERO,
                        Duration.ofMillis(1),
                        1));
        expectIllegalArgument(
                () -> new VcenterCloneClient(
                        apiRoot,
                        sessionId,
                        Duration.ofSeconds(1),
                        Duration.ofMillis(1),
                        0));

        VcenterCloneClient.CloneOutcome outcome =
                client.cloneAndWait(source, name);
        check(outcome.taskId().equals(expectedTask), "wrong task ID");
        check(
                outcome.virtualMachineId().equals(expectedVm),
                "wrong VM result");
        check(outcome.polls() == 4, "client did not poll through terminal state");
        check(sleepCount.get() == 3, "wrong sleeper invocation count");

        List<String> events = Files.readAllLines(
                requestLog,
                StandardCharsets.UTF_8);
        check(events.size() == 5, "unexpected request count");

        String expectedCloneTarget =
                "/api/vcenter/vm?action=clone&vmw-task=true";
        String expectedTaskTarget =
                "/api/cis/tasks/" + encodePathSegment(expectedTask);
        String expectedBody = "{"
                + jsonString("source") + ":" + jsonString(source) + ","
                + jsonString("name") + ":" + jsonString(name)
                + "}";
        String expectedBodyBase64 = Base64.getEncoder().encodeToString(
                expectedBody.getBytes(StandardCharsets.UTF_8));

        for (int index = 0; index < events.size(); index++) {
            String event = events.get(index);
            check(
                    integerField(event, "seq") == index + 1,
                    "request sequence mismatch");
            check(
                    stringField(event, "session").equals(sessionId),
                    "session header mismatch");
            check(
                    stringField(event, "accept").equals("application/json"),
                    "Accept header mismatch");

            if (index == 0) {
                check(
                        stringField(event, "method").equals("POST"),
                        "clone method mismatch");
                check(
                        stringField(event, "raw_target")
                                .equals(expectedCloneTarget),
                        "clone raw target mismatch");
                check(
                        stringField(event, "operation_id")
                                .equals("Vcenter.VM_clone$Task"),
                        "clone operationId mismatch");
                check(
                        stringField(event, "content_type")
                                .equals("application/json"),
                        "clone content type mismatch");
                check(
                        stringField(event, "body_b64")
                                .equals(expectedBodyBase64),
                        "clone body is not the minimal exact JSON");
            } else {
                check(
                        stringField(event, "method").equals("GET"),
                        "task method mismatch");
                check(
                        stringField(event, "raw_target")
                                .equals(expectedTaskTarget),
                        "task target or path encoding mismatch");
                check(
                        stringField(event, "operation_id")
                                .equals("Cis.Tasks_get"),
                        "task operationId mismatch");
                check(
                        nullField(event, "content_type"),
                        "task GET sent Content-Type");
                check(
                        integerField(event, "body_length") == 0,
                        "task GET sent a body");
                check(
                        stringField(event, "body_b64").isEmpty(),
                        "task GET body log is not empty");
                check(
                        integerField(event, "poll_ordinal") == index,
                        "task poll order mismatch");
            }
        }
        System.out.println("TEST_MAIN_OK");
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

    private static String stringField(String object, String key) {
        int start = fieldValueStart(object, key);
        if (start >= object.length() || object.charAt(start) != '"') {
            throw new AssertionError("field is not a JSON string: " + key);
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

    private static boolean nullField(String object, String key) {
        int start = fieldValueStart(object, key);
        return object.startsWith("null", start);
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
                        result.append(String.format("\\u%04X", (int) item));
                    } else {
                        result.append(item);
                    }
                }
            }
        }
        return result.append('"').toString();
    }

    private static String encodePathSegment(String value) {
        byte[] bytes = value.getBytes(StandardCharsets.UTF_8);
        char[] hex = "0123456789ABCDEF".toCharArray();
        StringBuilder result = new StringBuilder();
        for (byte item : bytes) {
            int current = item & 0xff;
            if ((current >= 'a' && current <= 'z')
                    || (current >= 'A' && current <= 'Z')
                    || (current >= '0' && current <= '9')
                    || current == '-'
                    || current == '.'
                    || current == '_'
                    || current == '~') {
                result.append((char) current);
            } else {
                result.append('%')
                        .append(hex[current >>> 4])
                        .append(hex[current & 0x0f]);
            }
        }
        return result.toString();
    }

    private static void check(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }

    @FunctionalInterface
    private interface ThrowingAction {
        void run() throws Exception;
    }
}
