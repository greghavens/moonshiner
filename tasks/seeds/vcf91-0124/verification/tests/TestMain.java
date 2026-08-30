import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.ArrayList;
import java.util.Base64;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

public final class TestMain {
    private static final Pattern STRING_FIELD =
            Pattern.compile("\"([A-Za-z][A-Za-z0-9]*)\":\"([^\"]*)\"");
    private static final Pattern NUMBER_FIELD =
            Pattern.compile("\"([A-Za-z][A-Za-z0-9]*)\":([0-9]+)");
    private static final char[] HEX =
            "0123456789ABCDEF".toCharArray();

    private TestMain() {
    }

    public static void main(String[] args) throws Exception {
        if (args.length != 11) {
            throw new AssertionError("expected eleven fixture arguments");
        }
        URI apiRoot = URI.create(args[0]);
        String session = args[1];
        Path logPath = Path.of(args[2]);
        String setCluster = args[3];
        String clearCluster = args[4];
        String rejectCluster = args[5];
        String taskPrefix = args[6];
        String modeKey = args[7];
        String maskKey = args[8];
        String maskName = args[9];
        String maskValue = args[10];

        VcenterEvcClient client = new VcenterEvcClient(
                apiRoot, session, Duration.ofSeconds(3), 5);

        ArrayList<VcenterEvcClient.FeatureMask> callerMasks =
                new ArrayList<>();
        callerMasks.add(new VcenterEvcClient.FeatureMask(
                maskKey, maskName, maskValue));
        VcenterEvcClient.EvcMode mode =
                new VcenterEvcClient.EvcMode(modeKey, callerMasks);
        callerMasks.clear();
        check(mode.masks().size() == 1,
                "EvcMode retained the caller's mutable list");
        expectUnsupported(() -> mode.masks().clear(),
                "EvcMode masks accessor must be immutable");

        check(Files.readAllLines(logPath).isEmpty(),
                "constructor made network traffic");
        expectIllegal(
                () -> client.applySafely(" ", mode),
                "blank cluster must fail before traffic");
        expectIllegal(
                () -> client.applySafely(
                        "cluster",
                        new VcenterEvcClient.EvcMode(
                                " ", List.of(
                                        new VcenterEvcClient.FeatureMask(
                                                "k", "n", "v")))),
                "blank EVC key must fail before traffic");
        expectIllegal(
                () -> new VcenterEvcClient(
                        URI.create(apiRoot + "?unexpected=true"),
                        session,
                        Duration.ofSeconds(1),
                        1),
                "apiRoot query must be rejected");
        check(Files.readAllLines(logPath).isEmpty(),
                "validation failure made network traffic");

        VcenterEvcClient.ApplyResult setResult =
                client.applySafely(setCluster, mode);
        check(setResult.precheckTaskId().equals(
                        taskPrefix + "/set task?#\u03a9"),
                "set precheck task ID was not preserved");
        check(setResult.mutationTaskId().equals(
                        taskPrefix + "/mutation-set accepted"),
                "set mutation task ID was not preserved");
        check(!setResult.clearing(), "set result was marked as clear");

        VcenterEvcClient.ApplyResult clearResult =
                client.applySafely(clearCluster, null);
        check(clearResult.precheckTaskId().equals(
                        taskPrefix + "/clear task?#\u03a9"),
                "clear precheck task ID was not preserved");
        check(clearResult.mutationTaskId().equals(
                        taskPrefix + "/mutation-clear accepted"),
                "clear mutation task ID was not preserved");
        check(clearResult.clearing(), "clear result was not marked as clear");

        String rejectedTask = taskPrefix + "/reject task?#\u03a9";
        try {
            client.applySafely(rejectCluster, mode);
            throw new AssertionError(
                    "nonempty precheck result did not block mutation");
        } catch (VcenterEvcClient.PrecheckFailedException expected) {
            check(rejectedTask.equals(expected.taskId()),
                    "rejected precheck task ID was not preserved");
            check("SUCCEEDED".equals(expected.status()),
                    "rejected precheck terminal status was not preserved");
            check(expected.checkResultCount() == 1,
                    "rejected precheck result count was not preserved");
            check(!expected.getMessage().contains(session),
                    "precheck exception leaked the session");
        }

        List<Map<String, String>> log = readLog(logPath);
        check(log.size() == 10,
                "unexpected request count: " + log.size());

        String checkOperation =
                "Vcenter.Cluster.EvcMode_checkSet$Task";
        String getOperation = "Cis.Tasks_get";
        String setOperation =
                "Vcenter.Cluster.EvcMode_set$Task";
        String[] operations = {
            checkOperation, getOperation, getOperation, getOperation,
            setOperation,
            checkOperation, getOperation, setOperation,
            checkOperation, getOperation
        };
        String[] methods = {
            "POST", "GET", "GET", "GET", "PUT",
            "POST", "GET", "PUT", "POST", "GET"
        };
        int[] statuses = {
            202, 200, 200, 200, 202,
            202, 200, 202, 202, 200
        };
        String setTaskTarget = "/api/cis/tasks/"
                + encodeSegment(taskPrefix + "/set task?#\u03a9");
        String clearTaskTarget = "/api/cis/tasks/"
                + encodeSegment(taskPrefix + "/clear task?#\u03a9");
        String rejectTaskTarget = "/api/cis/tasks/"
                + encodeSegment(taskPrefix + "/reject task?#\u03a9");
        String[] targets = {
            checkTarget(setCluster),
            setTaskTarget,
            setTaskTarget,
            setTaskTarget,
            mutateTarget(setCluster),
            checkTarget(clearCluster),
            clearTaskTarget,
            mutateTarget(clearCluster),
            checkTarget(rejectCluster),
            rejectTaskTarget
        };

        String setBody = "{\"evc_mode\":{\"key\":"
                + jsonString(modeKey)
                + ",\"masks\":[{\"key\":" + jsonString(maskKey)
                + ",\"name\":" + jsonString(maskName)
                + ",\"value\":" + jsonString(maskValue)
                + "}]}}";
        String[] bodies = {
            setBody, "", "", "", setBody,
            "{}", "", "{}", setBody, ""
        };

        for (int index = 0; index < log.size(); index++) {
            Map<String, String> entry = log.get(index);
            check(operations[index].equals(entry.get("operationId")),
                    "wrong operation at request " + index);
            check(methods[index].equals(entry.get("method")),
                    "wrong method at request " + index);
            check(targets[index].equals(entry.get("rawTarget")),
                    "wrong raw target at request " + index
                            + ": " + entry.get("rawTarget"));
            check(Integer.toString(statuses[index]).equals(
                            entry.get("status")),
                    "fixture status mismatch at request " + index);
            check(decode(entry, "bodyBase64").equals(bodies[index]),
                    "wrong body bytes at request " + index
                            + ": " + decode(entry, "bodyBase64"));
            check("1".equals(entry.get("sessionCount"))
                            && decode(entry, "sessionBase64").equals(session),
                    "session header mismatch at request " + index);
            check("1".equals(entry.get("acceptCount"))
                            && "application/json".equals(
                            decode(entry, "acceptBase64")),
                    "Accept header mismatch at request " + index);
            check("0".equals(entry.get("authorizationCount")),
                    "Authorization must be absent");
            boolean hasBody = methods[index].equals("POST")
                    || methods[index].equals("PUT");
            if (hasBody) {
                check("1".equals(entry.get("contentTypeCount"))
                                && "application/json".equals(
                                decode(entry, "contentTypeBase64")),
                        "Content-Type mismatch at request " + index);
            } else {
                check("0".equals(entry.get("contentTypeCount")),
                        "task GET sent Content-Type at request " + index);
                check(!entry.get("rawTarget").contains("?"),
                        "unset task spec query was not omitted");
            }
        }

        check(decode(log.get(0), "bodyBase64").equals(
                        decode(log.get(4), "bodyBase64")),
                "set mutation did not reuse byte-identical SetSpec");
        check(decode(log.get(5), "bodyBase64").equals("{}")
                        && decode(log.get(7), "bodyBase64").equals("{}"),
                "clear did not omit evc_mode from both bodies");
        check(log.stream().noneMatch(entry ->
                        entry.get("operationId").equals(setOperation)
                        && entry.get("rawTarget").equals(
                                mutateTarget(rejectCluster))),
                "mutation was issued after the rejected precheck");

        System.out.println(
                "PASS: EVC mutation was gated and exact wire shape verified.");
    }

    private static String checkTarget(String cluster) {
        return "/api/vcenter/cluster/" + encodeSegment(cluster)
                + "/evc-mode?action=check-set&vmw-task=true";
    }

    private static String mutateTarget(String cluster) {
        return "/api/vcenter/cluster/" + encodeSegment(cluster)
                + "/evc-mode?vmw-task=true";
    }

    private static List<Map<String, String>> readLog(Path path)
            throws Exception {
        List<Map<String, String>> result = new ArrayList<>();
        for (String line : Files.readAllLines(
                path, StandardCharsets.UTF_8)) {
            Map<String, String> entry = new LinkedHashMap<>();
            Matcher strings = STRING_FIELD.matcher(line);
            while (strings.find()) {
                entry.put(strings.group(1), strings.group(2));
            }
            Matcher numbers = NUMBER_FIELD.matcher(line);
            while (numbers.find()) {
                entry.put(numbers.group(1), numbers.group(2));
            }
            result.add(entry);
        }
        return result;
    }

    private static String decode(
            Map<String, String> entry,
            String key) {
        String encoded = entry.get(key);
        if (encoded == null) {
            throw new AssertionError("log field is absent: " + key);
        }
        return new String(
                Base64.getDecoder().decode(encoded),
                StandardCharsets.UTF_8);
    }

    private static String encodeSegment(String value) {
        byte[] bytes = value.getBytes(StandardCharsets.UTF_8);
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
                        .append(HEX[current >>> 4])
                        .append(HEX[current & 0x0f]);
            }
        }
        return result.toString();
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
                        result.append(String.format("\\u%04x", (int) item));
                    } else {
                        result.append(item);
                    }
                }
            }
        }
        return result.append('"').toString();
    }

    private static void expectIllegal(
            ThrowingRunnable action,
            String message) throws Exception {
        try {
            action.run();
            throw new AssertionError(message);
        } catch (IllegalArgumentException expected) {
            // Expected.
        }
    }

    private static void expectUnsupported(
            ThrowingRunnable action,
            String message) throws Exception {
        try {
            action.run();
            throw new AssertionError(message);
        } catch (UnsupportedOperationException expected) {
            // Expected.
        }
    }

    private static void check(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }

    @FunctionalInterface
    private interface ThrowingRunnable {
        void run() throws Exception;
    }
}
