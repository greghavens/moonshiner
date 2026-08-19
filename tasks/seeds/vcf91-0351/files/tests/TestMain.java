import java.io.IOException;
import java.net.URI;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Properties;
import java.util.concurrent.TimeUnit;

public final class TestMain {
    public static void main(String[] args) throws Exception {
        if (args.length != 2) {
            throw new AssertionError("TestMain requires repository root and compiled-class directory");
        }
        Path root = Path.of(args[0]).toAbsolutePath().normalize();
        Path classes = Path.of(args[1]).toAbsolutePath().normalize();
        runCase(root, classes, "success", 0, true, 200);
        runCase(root, classes, "conflict", 2, false, 409);
        System.out.println("PASS: VCF Automation success and conflict workflows were verified");
    }

    private static void runCase(Path root, Path classes, String outcome, int expectedExit,
                                boolean expectedOverallSuccess, long expectedActionStatus)
            throws Exception {
        Path temporary = Files.createTempDirectory("vcfa-test-" + outcome + "-");
        Path ready = temporary.resolve("ready.properties");
        Path log = temporary.resolve("requests.jsonl");
        Path mockOutput = temporary.resolve("mock.out");
        Process mock = new ProcessBuilder(
                "python3", root.resolve("mock/vcfa_mock.py").toString(),
                "--contract", root.resolve("docs/contract.json").toString(),
                "--ready-file", ready.toString(),
                "--log-file", log.toString(),
                "--action-outcome", outcome)
                .redirectErrorStream(true)
                .redirectOutput(mockOutput.toFile())
                .start();
        try {
            waitForReady(mock, ready, mockOutput);
            Properties fixture = new Properties();
            try (var input = Files.newInputStream(ready)) {
                fixture.load(input);
            }
            URI base = URI.create(required(fixture, "baseUrl"));
            require("127.0.0.1".equals(base.getHost()),
                    "verifier must use only the loopback mock");

            Path clientOut = temporary.resolve("client.out");
            Path clientErr = temporary.resolve("client.err");
            Process client = new ProcessBuilder(
                    "java", "-cp", classes.toString(), "VcfaChangeClient",
                    base.toString(), required(fixture, "token"),
                    required(fixture, "deploymentName"), required(fixture, "resourceName"),
                    required(fixture, "newDescription"), required(fixture, "actionName"))
                    .redirectOutput(clientOut.toFile())
                    .redirectError(clientErr.toFile())
                    .start();
            if (!client.waitFor(15, TimeUnit.SECONDS)) {
                client.destroyForcibly();
                throw new AssertionError("client timed out in " + outcome + " case");
            }
            String stdout = Files.readString(clientOut, StandardCharsets.UTF_8).trim();
            String stderr = Files.readString(clientErr, StandardCharsets.UTF_8).trim();
            require(client.exitValue() == expectedExit,
                    outcome + " case must exit " + expectedExit + ", got "
                            + client.exitValue() + suffix(stderr));
            Map<String, Object> output = object(Json.parse(stdout), "client output");

            List<Map<String, Object>> entries = new ArrayList<>();
            for (String line : Files.readAllLines(log, StandardCharsets.UTF_8)) {
                if (!line.isBlank()) {
                    entries.add(object(Json.parse(line), "request log entry"));
                }
            }
            verifyWorkflow(entries, output, fixture, expectedOverallSuccess, expectedActionStatus);
        } finally {
            mock.destroy();
            if (!mock.waitFor(3, TimeUnit.SECONDS)) {
                mock.destroyForcibly();
            }
            deleteTree(temporary);
        }
    }

    private static void verifyWorkflow(List<Map<String, Object>> entries,
                                       Map<String, Object> output,
                                       Properties fixture,
                                       boolean expectedOverallSuccess,
                                       long expectedActionStatus) {
        require(entries.size() == 5, "expected exactly five client requests, got " + entries.size());
        String[] expectedOrder = {
                "getDeployments", "patchDeployment", "getDeploymentResources",
                "getResourceActions", "submitResourceActionRequest"
        };
        for (int index = 0; index < expectedOrder.length; index++) {
            Map<String, Object> entry = entries.get(index);
            require(expectedOrder[index].equals(entry.get("operationId")),
                    "request " + (index + 1) + " must be " + expectedOrder[index]);
            Map<String, Object> headers = object(entry.get("requestHeaders"), "request headers");
            require(Boolean.TRUE.equals(headers.get("authorizationPresent")),
                    "request " + (index + 1) + " omitted the bearer authorization header");
        }
        requireJsonContentType(entries.get(1), "deployment update");
        requireJsonContentType(entries.get(4), "action request");

        String deploymentName = required(fixture, "deploymentName");
        String resourceName = required(fixture, "resourceName");
        String actionName = required(fixture, "actionName");

        Map<String, Object> deploymentPage = object(
                entries.get(0).get("responseBody"), "deployment lookup response");
        Map<String, Object> deployment = exactNamed(
                array(deploymentPage.get("content"), "deployment content"),
                deploymentName, "deployment");
        String deploymentId = string(deployment.get("id"), "deployment id");
        require(queryValue(entries.get(0), "name").equals(deploymentName),
                "deployment lookup did not use the exact name query");
        require(rawTarget(entries.get(0)).equals(
                        "/deployment/api/deployments?name=" + encode(deploymentName)),
                "deployment query value was not percent-encoded");

        requireIdentifierReturned(deploymentId,
                pathId(entries.get(1), "deploymentId"), "deployment update");
        requireIdentifierReturned(deploymentId,
                pathId(entries.get(2), "deploymentId"), "resource lookup");
        String encodedDeploymentId = encode(deploymentId);
        require(rawTarget(entries.get(1)).equals(
                        "/deployment/api/deployments/" + encodedDeploymentId),
                "deployment ID path segment was not percent-encoded");
        require(number(entries.get(1).get("responseStatus"), "patch status") == 200,
                "deployment update must succeed before the action response");
        Map<String, Object> patchBody = object(
                entries.get(1).get("requestBody"), "patch request body");
        require(required(fixture, "newDescription").equals(patchBody.get("description")),
                "patch description differs from the requested value");

        Map<String, Object> resourcePage = object(
                entries.get(2).get("responseBody"), "resource lookup response");
        Map<String, Object> resource = exactNamed(
                array(resourcePage.get("content"), "resource content"),
                resourceName, "resource");
        String resourceId = string(resource.get("id"), "resource id");
        require(queryValue(entries.get(2), "names").equals(resourceName),
                "resource lookup did not use the exact names query");
        require(rawTarget(entries.get(2)).equals(
                        "/deployment/api/deployments/" + encodedDeploymentId
                                + "/resources?names=" + encode(resourceName)),
                "resource lookup target was not percent-encoded");
        requireIdentifierReturned(deploymentId,
                pathId(entries.get(3), "deploymentId"), "action lookup deployment");
        requireIdentifierReturned(resourceId,
                pathId(entries.get(3), "resourceId"), "action lookup resource");

        List<Object> actionResponse = array(
                entries.get(3).get("responseBody"), "action lookup response");
        Map<String, Object> action = exactNamed(actionResponse, actionName, "action");
        String actionId = string(action.get("id"), "action id");
        String encodedResourceId = encode(resourceId);
        String resourcePath = "/deployment/api/deployments/" + encodedDeploymentId
                + "/resources/" + encodedResourceId;
        require(rawTarget(entries.get(3)).equals(resourcePath + "/actions"),
                "action lookup path segments were not percent-encoded");
        requireIdentifierReturned(deploymentId,
                pathId(entries.get(4), "deploymentId"), "action request deployment");
        requireIdentifierReturned(resourceId,
                pathId(entries.get(4), "resourceId"), "action request resource");
        require(rawTarget(entries.get(4)).equals(resourcePath + "/requests"),
                "action request path segments were not percent-encoded");

        Map<String, Object> actionRequestBody = object(
                entries.get(4).get("requestBody"), "action request body");
        requireIdentifierReturned(actionId,
                string(actionRequestBody.get("actionId"), "submitted action id"),
                "action request action");
        require(object(actionRequestBody.get("inputs"), "action inputs").isEmpty(),
                "action inputs must be empty");
        require(number(entries.get(4).get("responseStatus"), "action request status")
                        == expectedActionStatus,
                "action request returned the wrong scenario status");

        require(Boolean.valueOf(expectedOverallSuccess).equals(output.get("overallSuccess")),
                "overallSuccess is inaccurate");
        Map<String, Object> steps = object(output.get("steps"), "steps");
        verifyStep(steps, "deploymentLookup", true, 200, deploymentId, null);
        verifyStep(steps, "deploymentUpdate", true, 200, deploymentId, null);
        verifyStep(steps, "resourceLookup", true, 200, resourceId, null);
        verifyStep(steps, "actionLookup", true, 200, actionId, null);
        String actionError = null;
        if (!expectedOverallSuccess) {
            Map<String, Object> conflict = object(
                    entries.get(4).get("responseBody"), "conflict response");
            actionError = string(conflict.get("details"), "conflict details");
        }
        verifyStep(steps, "actionRequest", expectedOverallSuccess,
                expectedActionStatus, actionId, actionError);
        require(steps.size() == 5, "steps must contain exactly the five attempted workflow steps");
    }

    private static void requireJsonContentType(Map<String, Object> entry, String label) {
        Map<String, Object> headers = object(entry.get("requestHeaders"), label + " headers");
        String contentType = string(headers.get("contentType"), label + " content type");
        require(contentType.toLowerCase().startsWith("application/json"),
                label + " must use application/json");
    }

    private static String queryValue(Map<String, Object> entry, String key) {
        Map<String, Object> query = object(entry.get("query"), "query parameters");
        List<Object> values = array(query.get(key), key + " query values");
        require(values.size() == 1, key + " query parameter must occur once");
        return string(values.get(0), key + " query value");
    }

    private static String rawTarget(Map<String, Object> entry) {
        return string(entry.get("rawTarget"), "raw request target");
    }

    private static String encode(String value) {
        return URLEncoder.encode(value, StandardCharsets.UTF_8).replace("+", "%20");
    }

    private static void verifyStep(Map<String, Object> steps, String name, boolean success,
                                   long status, String id, String error) {
        Map<String, Object> step = object(steps.get(name), name);
        require(Boolean.valueOf(success).equals(step.get("success")),
                name + " success is inaccurate");
        require(number(step.get("httpStatus"), name + " httpStatus") == status,
                name + " httpStatus is inaccurate");
        require(id.equals(step.get("id")), name + " id is inaccurate");
        if (error != null) {
            require(error.equals(step.get("error")),
                    name + " error does not preserve response details");
        }
    }

    private static void requireIdentifierReturned(String returned, String used, String context) {
        require(returned.equals(used),
                context + " used identifier " + used + " that its own lookup did not return");
    }

    private static Map<String, Object> exactNamed(List<Object> values, String name, String label) {
        Map<String, Object> found = null;
        for (Object value : values) {
            Map<String, Object> candidate = object(value, label + " item");
            if (name.equals(candidate.get("name"))) {
                require(found == null, "lookup returned duplicate exact " + label + " names");
                found = candidate;
            }
        }
        require(found != null, "lookup did not return exact " + label + " name");
        return found;
    }

    private static String pathId(Map<String, Object> entry, String key) {
        return string(object(entry.get("pathParameters"), "path parameters").get(key), key);
    }

    private static void waitForReady(Process process, Path ready, Path output) throws Exception {
        long deadline = System.nanoTime() + Duration.ofSeconds(8).toNanos();
        while (System.nanoTime() < deadline) {
            if (Files.isRegularFile(ready)) {
                return;
            }
            if (!process.isAlive()) {
                throw new AssertionError("mock failed to start" + suffix(Files.readString(output)));
            }
            Thread.sleep(25);
        }
        throw new AssertionError("mock did not become ready");
    }

    private static String required(Properties properties, String key) {
        String value = properties.getProperty(key);
        require(value != null && !value.isEmpty(), "missing fixture property " + key);
        return value;
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> object(Object value, String label) {
        require(value instanceof Map<?, ?>, label + " must be a JSON object");
        return (Map<String, Object>) value;
    }

    @SuppressWarnings("unchecked")
    private static List<Object> array(Object value, String label) {
        require(value instanceof List<?>, label + " must be a JSON array");
        return (List<Object>) value;
    }

    private static String string(Object value, String label) {
        require(value instanceof String, label + " must be a string");
        return (String) value;
    }

    private static long number(Object value, String label) {
        require(value instanceof Number, label + " must be a number");
        return ((Number) value).longValue();
    }

    private static void require(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }

    private static String suffix(String message) {
        return message == null || message.isBlank() ? "" : ": " + message;
    }

    private static void deleteTree(Path root) {
        if (root == null || !Files.exists(root)) {
            return;
        }
        try (var paths = Files.walk(root)) {
            paths.sorted((left, right) -> right.compareTo(left)).forEach(path -> {
                try {
                    Files.deleteIfExists(path);
                } catch (IOException ignored) {
                    // Temporary test output is best-effort cleanup.
                }
            });
        } catch (IOException ignored) {
            // Temporary test output is best-effort cleanup.
        }
    }

    private static final class Json {
        private final String text;
        private int index;

        private Json(String text) {
            this.text = text;
        }

        static Object parse(String text) {
            require(text != null && !text.isBlank(), "expected JSON output");
            Json parser = new Json(text);
            Object value = parser.value();
            parser.space();
            require(parser.index == text.length(), "unexpected data after JSON document");
            return value;
        }

        private Object value() {
            space();
            require(index < text.length(), "unexpected end of JSON");
            return switch (text.charAt(index)) {
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
            LinkedHashMap<String, Object> result = new LinkedHashMap<>();
            index++;
            space();
            if (take('}')) return result;
            while (true) {
                space();
                String key = string();
                space();
                require(take(':'), "expected ':' in JSON object");
                result.put(key, value());
                space();
                if (take('}')) return result;
                require(take(','), "expected ',' in JSON object");
            }
        }

        private List<Object> array() {
            ArrayList<Object> result = new ArrayList<>();
            index++;
            space();
            if (take(']')) return result;
            while (true) {
                result.add(value());
                space();
                if (take(']')) return result;
                require(take(','), "expected ',' in JSON array");
            }
        }

        private String string() {
            require(take('"'), "expected JSON string");
            StringBuilder result = new StringBuilder();
            while (index < text.length()) {
                char next = text.charAt(index++);
                if (next == '"') return result.toString();
                if (next != '\\') {
                    result.append(next);
                    continue;
                }
                require(index < text.length(), "bad JSON escape");
                char escaped = text.charAt(index++);
                switch (escaped) {
                    case '"', '\\', '/' -> result.append(escaped);
                    case 'b' -> result.append('\b');
                    case 'f' -> result.append('\f');
                    case 'n' -> result.append('\n');
                    case 'r' -> result.append('\r');
                    case 't' -> result.append('\t');
                    case 'u' -> {
                        require(index + 4 <= text.length(), "bad unicode escape");
                        result.append((char) Integer.parseInt(text.substring(index, index + 4), 16));
                        index += 4;
                    }
                    default -> throw new AssertionError("bad JSON escape");
                }
            }
            throw new AssertionError("unterminated JSON string");
        }

        private Object number() {
            int start = index;
            if (take('-')) { }
            while (index < text.length() && Character.isDigit(text.charAt(index))) index++;
            if (take('.')) while (index < text.length() && Character.isDigit(text.charAt(index))) index++;
            if (index < text.length() && (text.charAt(index) == 'e' || text.charAt(index) == 'E')) {
                index++;
                if (index < text.length() && (text.charAt(index) == '+' || text.charAt(index) == '-')) index++;
                while (index < text.length() && Character.isDigit(text.charAt(index))) index++;
            }
            require(index > start, "expected JSON number");
            String token = text.substring(start, index);
            return token.contains(".") || token.contains("e") || token.contains("E")
                    ? Double.valueOf(token) : Long.valueOf(token);
        }

        private Object literal(String token, Object value) {
            require(text.startsWith(token, index), "bad JSON literal");
            index += token.length();
            return value;
        }

        private boolean take(char expected) {
            if (index < text.length() && text.charAt(index) == expected) {
                index++;
                return true;
            }
            return false;
        }

        private void space() {
            while (index < text.length() && Character.isWhitespace(text.charAt(index))) index++;
        }
    }
}
