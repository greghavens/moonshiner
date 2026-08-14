import java.nio.file.Files;
import java.nio.file.Path;
import java.io.IOException;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

public final class TestMain {
    private static int passed;
    private static int failed;

    @FunctionalInterface
    private interface CheckedBody {
        void run() throws Exception;
    }

    private static void test(String name, CheckedBody body) {
        try {
            body.run();
            passed++;
            System.out.println("PASS " + name);
        } catch (Throwable error) {
            failed++;
            System.err.println("FAIL " + name + ": " + error);
            error.printStackTrace(System.err);
        }
    }

    public static void main(String[] args) throws Exception {
        test("pinned specification provenance", TestMain::checkPinnedContract);
        test("single production source file", TestMain::checkSingleClientSource);
        test("fetch refresh preserves completed work and wire contract", () ->
                runScenario("Payments", "18230:561:271275765",
                        ContractMockServer.ExpiryPoint.FETCH));
        test("create refresh, JSON escaping, and response field order", () ->
                runScenario("Analytics \"North\" \\ Tier\b\f\n\r\t\u0001",
                        "18230:561:90210411", ContractMockServer.ExpiryPoint.CREATE));

        if (failed != 0) {
            throw new AssertionError(failed + " checks failed; " + passed + " passed");
        }
        System.out.println("all " + passed + " checks passed");
    }

    private static void checkPinnedContract() throws Exception {
        String contract = Files.readString(Path.of("docs/contract.json"));
        require(contract.contains("\"server_base_path\": \"/api/ni\""),
                "contract lost the spec server path");
        require(count(contract, "\"operationId\"") == 3,
                "contract must name exactly three operations");
        for (String operation : List.of("create", "addApplication", "getApplicationById")) {
            require(contract.contains("\"operationId\": \"" + operation + "\""),
                    "contract missing operationId " + operation);
        }
        require(contract.contains("\"fetch_member_counts\""),
                "contract missing optional fetch_member_counts");
        require(contract.contains("\"fetch_update_status\""),
                "contract missing optional fetch_update_status");
        require(contract.contains("\"value\": { \"type\": \"string\" }"),
                "contract missing optional local-domain value");

        String sources = Files.readString(Path.of("docs/official_sources.json"));
        require(sources.contains("85151f6b1bb58f13b6ac0304bfec53904bea085f"),
                "official source commit changed");
        require(count(sources,
                "specifications/vcf-operations/vcf-operations-for-networks-openapi.yaml") == 3,
                "every operation must carry the exact spec path");
        for (String operation : List.of("create", "addApplication", "getApplicationById")) {
            require(sources.contains("\"operationId\": \"" + operation + "\""),
                    "official sources missing operationId " + operation);
        }
    }

    private static void checkSingleClientSource() throws Exception {
        Path root = Path.of(".").toAbsolutePath().normalize();
        try (var paths = Files.walk(root)) {
            Set<String> javaFiles = paths
                    .filter(Files::isRegularFile)
                    .filter(path -> path.getFileName().toString().endsWith(".java"))
                    .map(path -> root.relativize(path).toString())
                    .collect(java.util.stream.Collectors.toSet());
            require(javaFiles.equals(Set.of(
                            "VcfOperationsNetworksClient.java",
                            "ContractMockServer.java",
                            "TestMain.java")),
                    "client implementation must remain one source file; found " + javaFiles);
        }
    }

    private static void runScenario(
            String applicationName, String entityId, ContractMockServer.ExpiryPoint expiryPoint)
            throws Exception {
        try (ContractMockServer mock = new ContractMockServer(
                entityId, applicationName, expiryPoint)) {
            VcfOperationsNetworksClient client = new VcfOperationsNetworksClient(
                    mock.baseUri(), "integration-user", "fixture-password");

            VcfOperationsNetworksClient.Application application =
                    client.createApplicationAndFetch(applicationName);
            require(application.equals(new VcfOperationsNetworksClient.Application(
                            entityId, applicationName, "Application")),
                    "fetched application mismatch: " + application);

            List<ContractMockServer.LoggedRequest> log = mock.requestLog();
            require(log.size() == 5, "expected five requests, got " + describe(log));

            checkAuth(log.get(0));
            if (expiryPoint == ContractMockServer.ExpiryPoint.FETCH) {
                checkCreate(log.get(1), applicationName, "lease-one");
                checkFetch(log.get(2), entityId, "lease-one");
                checkAuth(log.get(3));
                checkFetch(log.get(4), entityId, "lease-two");
            } else {
                checkCreate(log.get(1), applicationName, "lease-one");
                checkAuth(log.get(2));
                checkCreate(log.get(3), applicationName, "lease-two");
                checkFetch(log.get(4), entityId, "lease-two");
            }

            long createAttempts = log.stream()
                    .filter(request -> request.method().equals("POST"))
                    .filter(request -> request.rawPath().equals(
                            "/api/ni/groups/applications"))
                    .count();
            long expectedAttempts = expiryPoint == ContractMockServer.ExpiryPoint.CREATE ? 2 : 1;
            require(createAttempts == expectedAttempts,
                    "refresh retried the wrong workflow step: " + describe(log));
            require(mock.successfulApplicationCreates() == 1,
                    "application was successfully created more than once: " + describe(log));
        }
    }

    private static void checkAuth(ContractMockServer.LoggedRequest request) {
        checkLine(request, "POST", "/api/ni/auth/token");
        require(request.header("Authorization") == null,
                "token request must not carry Authorization");
        require("application/json".equals(request.header("Content-Type")),
                "token request Content-Type mismatch");
        Map<String, Object> expected = Map.of(
                "username", "integration-user",
                "password", "fixture-password",
                "domain", Map.of("domain_type", "LOCAL"));
        require(parseObject(request.body()).equals(expected),
                "local credential body mismatch, extra field, or serialized optional domain.value: "
                        + request.body());
    }

    private static void checkCreate(
            ContractMockServer.LoggedRequest request, String applicationName, String token) {
        checkLine(request, "POST", "/api/ni/groups/applications");
        require(("NetworkInsight " + token).equals(request.header("Authorization")),
                "create Authorization mismatch");
        require("application/json".equals(request.header("Content-Type")),
                "create Content-Type mismatch");
        require(parseObject(request.body()).equals(Map.of("name", applicationName)),
                "application request wire body mismatch: " + request.body());
    }

    private static void checkFetch(
            ContractMockServer.LoggedRequest request, String entityId, String token) {
        checkLine(request, "GET", "/api/ni/groups/applications/" + entityId);
        require(("NetworkInsight " + token).equals(request.header("Authorization")),
                "fetch Authorization mismatch");
        require(request.header("Content-Type") == null,
                "bodyless fetch must omit Content-Type");
        require(request.body().isEmpty(), "GET request must have an empty body");
    }

    private static void checkLine(
            ContractMockServer.LoggedRequest request, String method, String rawPath) {
        require(request.method().equals(method),
                "method mismatch: expected " + method + ", got " + request.method());
        require(request.rawPath().equals(rawPath),
                "path mismatch: expected " + rawPath + ", got " + request.rawPath());
        require(request.rawQuery() == null,
                "unset optional query parameters must be omitted, got " + request.rawQuery());
    }

    private static String describe(List<ContractMockServer.LoggedRequest> log) {
        return log.stream()
                .map(request -> request.method() + " " + request.rawPath()
                        + (request.rawQuery() == null ? "" : "?" + request.rawQuery()))
                .toList()
                .toString();
    }

    private static int count(String text, String needle) {
        int found = 0;
        int from = 0;
        while ((from = text.indexOf(needle, from)) >= 0) {
            found++;
            from += needle.length();
        }
        return found;
    }

    private static Map<String, Object> parseObject(String text) {
        try {
            JsonReader reader = new JsonReader(text);
            Map<String, Object> value = reader.readObject();
            reader.skipWhitespace();
            require(reader.atEnd(), "trailing data in JSON body: " + text);
            return value;
        } catch (IOException error) {
            throw new AssertionError("invalid JSON request body: " + text, error);
        }
    }

    /** Minimal protected parser for the string/object-only request schemas in the contract. */
    private static final class JsonReader {
        private final String text;
        private int cursor;

        JsonReader(String text) {
            this.text = text;
        }

        Map<String, Object> readObject() throws IOException {
            skipWhitespace();
            expect('{');
            Map<String, Object> result = new LinkedHashMap<>();
            skipWhitespace();
            if (take('}')) return result;
            while (true) {
                String key = readString();
                skipWhitespace();
                expect(':');
                skipWhitespace();
                Object value = cursor < text.length() && text.charAt(cursor) == '{'
                        ? readObject() : readString();
                if (result.putIfAbsent(key, value) != null) {
                    throw new IOException("duplicate JSON field " + key);
                }
                skipWhitespace();
                if (take('}')) return result;
                expect(',');
                skipWhitespace();
            }
        }

        String readString() throws IOException {
            skipWhitespace();
            expect('"');
            StringBuilder out = new StringBuilder();
            while (cursor < text.length()) {
                char ch = text.charAt(cursor++);
                if (ch == '"') return out.toString();
                if (ch != '\\') {
                    out.append(ch);
                    continue;
                }
                if (cursor >= text.length()) throw new IOException("bad JSON escape");
                char escaped = text.charAt(cursor++);
                switch (escaped) {
                    case '"', '\\', '/' -> out.append(escaped);
                    case 'b' -> out.append('\b');
                    case 'f' -> out.append('\f');
                    case 'n' -> out.append('\n');
                    case 'r' -> out.append('\r');
                    case 't' -> out.append('\t');
                    case 'u' -> {
                        if (cursor + 4 > text.length()) {
                            throw new IOException("short unicode escape");
                        }
                        try {
                            out.append((char) Integer.parseInt(
                                    text.substring(cursor, cursor + 4), 16));
                        } catch (NumberFormatException error) {
                            throw new IOException("bad unicode escape", error);
                        }
                        cursor += 4;
                    }
                    default -> throw new IOException("bad JSON escape");
                }
            }
            throw new IOException("unterminated JSON string");
        }

        void skipWhitespace() {
            while (cursor < text.length() && Character.isWhitespace(text.charAt(cursor))) cursor++;
        }

        boolean atEnd() {
            return cursor == text.length();
        }

        private boolean take(char expected) {
            if (cursor < text.length() && text.charAt(cursor) == expected) {
                cursor++;
                return true;
            }
            return false;
        }

        private void expect(char expected) throws IOException {
            if (!take(expected)) throw new IOException("expected '" + expected + "'");
        }
    }

    private static void require(boolean condition, String message) {
        if (!condition) throw new AssertionError(message);
    }
}
