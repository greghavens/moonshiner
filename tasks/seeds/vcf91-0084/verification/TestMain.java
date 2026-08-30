import java.io.IOException;
import java.net.http.HttpClient;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.ArrayList;
import java.util.Base64;
import java.util.List;

/**
 * Protected acceptance harness. The loopback mock fsyncs every request before
 * responding, so this test can make gating and exact-wire assertions directly.
 */
public final class TestMain {
    private static int checks;

    private TestMain() {}

    @FunctionalInterface
    private interface ThrowingRunnable {
        void run() throws Exception;
    }

    private record LogRecord(
            String operationId,
            String method,
            String rawTarget,
            String authorization,
            int authorizationCount,
            String accept,
            int acceptCount,
            String contentType,
            int contentTypeCount,
            String contentLength,
            String body,
            int status,
            int mutationCount
    ) {
        static LogRecord parse(String line) {
            String[] fields = line.split("\t", -1);
            equal(13, fields.length, "request-log field count");
            return new LogRecord(
                    fields[0],
                    fields[1],
                    decode(fields[2]),
                    decode(fields[3]),
                    Integer.parseInt(fields[4]),
                    decode(fields[5]),
                    Integer.parseInt(fields[6]),
                    decode(fields[7]),
                    Integer.parseInt(fields[8]),
                    decode(fields[9]),
                    decode(fields[10]),
                    Integer.parseInt(fields[11]),
                    Integer.parseInt(fields[12])
            );
        }

        private static String decode(String value) {
            return new String(
                    Base64.getUrlDecoder().decode(value),
                    StandardCharsets.UTF_8
            );
        }
    }

    public static void main(String[] args) throws Exception {
        equal(5, args.length, "harness arguments");
        String baseUrl = args[0];
        Path logPath = Path.of(args[1]);
        Path effectPath = Path.of(args[2]);
        String token = args[3];
        String expectedContractFingerprint = args[4];
        equal(
                "102d15fd342f6a45bb6d84a5b39a916c65929f4c",
                expectedContractFingerprint,
                "pinned specification blob"
        );

        String username = "svc-" + token;
        String password = "pw:" + token + ":not-logged";
        String expectedAuthorization = "Basic " + Base64.getEncoder().encodeToString(
                (username + ":" + password).getBytes(StandardCharsets.UTF_8)
        );
        HttpClient httpClient = HttpClient.newBuilder()
                .connectTimeout(Duration.ofSeconds(2))
                .build();

        exerciseConstructorAndPreflightValidation(
                baseUrl,
                username,
                password,
                httpClient,
                logPath
        );

        NsxPolicyClient client = new NsxPolicyClient(
                baseUrl,
                username,
                password,
                httpClient
        );
        exerciseFailedPrechecks(
                client,
                token,
                password,
                logPath,
                effectPath,
                expectedAuthorization
        );
        exerciseMinimalSuccess(
                client,
                token,
                logPath,
                effectPath,
                expectedAuthorization
        );
        exerciseSetOptions(
                client,
                token,
                logPath,
                effectPath,
                expectedAuthorization
        );

        List<LogRecord> finalLog = records(logPath, 7);
        equal(7, finalLog.size(), "total request count");
        long patchCount = finalLog.stream()
                .filter(record -> record.operationId().equals("PatchTier1"))
                .count();
        equal(2L, patchCount, "only passing prechecks mutate");
        equal("2", Files.readString(effectPath).trim(), "final mutation effects");

        System.out.println("ALL NSX POLICY CONTRACT CHECKS PASSED (" + checks + " checks)");
    }

    private static void exerciseConstructorAndPreflightValidation(
            String baseUrl,
            String username,
            String password,
            HttpClient httpClient,
            Path logPath
    ) throws Exception {
        expect(
                IllegalArgumentException.class,
                () -> new NsxPolicyClient(
                        baseUrl + "/path",
                        username,
                        password,
                        httpClient
                ),
                "origin path rejected"
        );
        expect(
                IllegalArgumentException.class,
                () -> new NsxPolicyClient(
                        baseUrl + "?query=yes",
                        username,
                        password,
                        httpClient
                ),
                "origin query rejected"
        );
        expect(
                IllegalArgumentException.class,
                () -> new NsxPolicyClient(
                        baseUrl.replace("http://", "http://embedded@"),
                        username,
                        password,
                        httpClient
                ),
                "embedded credentials rejected"
        );
        expect(
                IllegalArgumentException.class,
                () -> new NsxPolicyClient(baseUrl, "bad:user", password, httpClient),
                "colon in username rejected"
        );
        expect(
                IllegalArgumentException.class,
                () -> new NsxPolicyClient(baseUrl, " ", password, httpClient),
                "blank username rejected"
        );
        expect(
                IllegalArgumentException.class,
                () -> new NsxPolicyClient(baseUrl, username, "", httpClient),
                "blank password rejected"
        );
        expect(
                NullPointerException.class,
                () -> new NsxPolicyClient(baseUrl, username, password, null),
                "null transport rejected"
        );
        expect(
                IllegalArgumentException.class,
                () -> new NsxPolicyClient.Tier1DescriptionPatch(" "),
                "blank description rejected"
        );
        expect(
                IllegalArgumentException.class,
                () -> new NsxPolicyClient.Tier1DescriptionPatch("x".repeat(1025)),
                "overlong description rejected"
        );
        NsxPolicyClient.Tier1DescriptionPatch unicodeLimit =
                new NsxPolicyClient.Tier1DescriptionPatch("😀".repeat(1024));
        equal(1024, unicodeLimit.description().codePointCount(
                0,
                unicodeLimit.description().length()
        ), "description limit uses characters");

        NsxPolicyClient client = new NsxPolicyClient(
                baseUrl,
                username,
                password,
                httpClient
        );
        NsxPolicyClient.Tier1DescriptionPatch patch =
                new NsxPolicyClient.Tier1DescriptionPatch("validated");
        expect(
                IllegalArgumentException.class,
                () -> client.updateTier1DescriptionIfReady("", patch, null, null),
                "blank Tier-1 id rejected"
        );
        expect(
                NullPointerException.class,
                () -> client.updateTier1DescriptionIfReady("id", null, null, null),
                "null patch rejected"
        );
        expect(
                IllegalArgumentException.class,
                () -> client.updateTier1DescriptionIfReady("id", patch, "", null),
                "empty enforcement point rejected"
        );
        expect(
                IllegalArgumentException.class,
                () -> client.updateTier1DescriptionIfReady("id", patch, null, ""),
                "empty source rejected"
        );
        expect(
                IllegalArgumentException.class,
                () -> client.updateTier1DescriptionIfReady(
                        "id",
                        patch,
                        null,
                        "live"
                ),
                "unknown source rejected"
        );
        equal(0, records(logPath, 0).size(), "validation opens no connection");
    }

    private static void exerciseFailedPrechecks(
            NsxPolicyClient client,
            String token,
            String password,
            Path logPath,
            Path effectPath,
            String expectedAuthorization
    ) throws Exception {
        String blockedId = "blocked/" + token;
        NsxPolicyClient.PrecheckFailed blocked = expect(
                NsxPolicyClient.PrecheckFailed.class,
                () -> client.updateTier1DescriptionIfReady(
                        blockedId,
                        new NsxPolicyClient.Tier1DescriptionPatch("must not apply"),
                        null,
                        null
                ),
                "failed state blocks PATCH"
        );
        equal(blockedId, blocked.tier1Id(), "failed precheck Tier-1 id");
        equal("failed", blocked.state(), "failed precheck state");
        equal(Long.valueOf(9407), blocked.failureCode(), "failure code retained");
        equal(
                "gateway realization blocked " + token,
                blocked.failureMessage(),
                "failure message retained"
        );
        List<LogRecord> afterBlocked = records(logPath, 1);
        equal(1, afterBlocked.size(), "one request after failed state");
        assertPrecheckRequest(
                afterBlocked.get(0),
                blockedId,
                expectedAuthorization,
                "?type=GATEWAY_STATE"
        );
        equal(0, afterBlocked.get(0).mutationCount(), "failed state has no effect");
        equal("0", Files.readString(effectPath).trim(), "failed state effect file");

        String malformedId = "malformed/" + token;
        expect(
                NsxPolicyClient.ProtocolException.class,
                () -> client.updateTier1DescriptionIfReady(
                        malformedId,
                        new NsxPolicyClient.Tier1DescriptionPatch("must not apply"),
                        null,
                        null
                ),
                "malformed precheck blocks PATCH"
        );
        List<LogRecord> afterMalformed = records(logPath, 2);
        equal(2, afterMalformed.size(), "one request after malformed response");
        assertPrecheckRequest(
                afterMalformed.get(1),
                malformedId,
                expectedAuthorization,
                "?type=GATEWAY_STATE"
        );
        equal(0, afterMalformed.get(1).mutationCount(), "malformed state no effect");
        equal("0", Files.readString(effectPath).trim(), "malformed effect file");

        String outageId = "outage/" + token;
        NsxPolicyClient.NsxPolicyException outage = expect(
                NsxPolicyClient.NsxPolicyException.class,
                () -> client.updateTier1DescriptionIfReady(
                        outageId,
                        new NsxPolicyClient.Tier1DescriptionPatch("must not apply"),
                        null,
                        null
                ),
                "HTTP precheck error blocks PATCH"
        );
        equal(503, outage.statusCode(), "HTTP status retained");
        check(
                outage.responseBody().contains("\"error_code\":50384"),
                "HTTP response body retained"
        );
        check(!outage.getMessage().contains(password), "password absent from error");
        check(
                !outage.getMessage().contains("Authorization"),
                "authorization absent from error"
        );
        List<LogRecord> afterOutage = records(logPath, 3);
        equal(3, afterOutage.size(), "one request after HTTP error");
        assertPrecheckRequest(
                afterOutage.get(2),
                outageId,
                expectedAuthorization,
                "?type=GATEWAY_STATE"
        );
        equal(503, afterOutage.get(2).status(), "mock returned 503");
        equal(0, afterOutage.get(2).mutationCount(), "HTTP error has no effect");
        equal("0", Files.readString(effectPath).trim(), "HTTP error effect file");
    }

    private static void exerciseMinimalSuccess(
            NsxPolicyClient client,
            String token,
            Path logPath,
            Path effectPath,
            String expectedAuthorization
    ) throws Exception {
        String tier1Id = "ready/core ?#% Δ-" + token;
        String description = "approved \"north\"\nΔ " + token;
        NsxPolicyClient.UpdateResult result =
                client.updateTier1DescriptionIfReady(
                        tier1Id,
                        new NsxPolicyClient.Tier1DescriptionPatch(description),
                        null,
                        null
                );
        equal(tier1Id, result.tier1Id(), "result Tier-1 id");
        equal("success", result.precheckState(), "result state");
        check(result.changed(), "result changed");

        List<LogRecord> log = records(logPath, 5);
        equal(5, log.size(), "minimal success adds two requests");
        LogRecord precheck = log.get(3);
        assertPrecheckRequest(
                precheck,
                tier1Id,
                expectedAuthorization,
                "?type=GATEWAY_STATE"
        );
        equal(0, precheck.mutationCount(), "precheck precedes first effect");

        LogRecord patch = log.get(4);
        equal("PatchTier1", patch.operationId(), "mutation operationId");
        equal("PATCH", patch.method(), "mutation method");
        equal(
                "/policy/api/v1/infra/tier-1s/" + encode(tier1Id),
                patch.rawTarget(),
                "escaped mutation target"
        );
        equal(expectedAuthorization, patch.authorization(), "mutation Basic auth");
        equal(1, patch.authorizationCount(), "one mutation Authorization header");
        equal("application/json", patch.accept(), "mutation Accept");
        equal(1, patch.acceptCount(), "one mutation Accept header");
        equal("application/json", patch.contentType(), "mutation Content-Type");
        equal(1, patch.contentTypeCount(), "one mutation Content-Type header");
        String expectedBody = "{\"resource_type\":\"Tier1\",\"description\":"
                + jsonString(description) + "}";
        equal(expectedBody, patch.body(), "byte-exact compact mutation JSON");
        equal(
                Integer.toString(expectedBody.getBytes(StandardCharsets.UTF_8).length),
                patch.contentLength(),
                "UTF-8 mutation Content-Length"
        );
        equal(200, patch.status(), "mutation status");
        equal(1, patch.mutationCount(), "first mutation effect");
        equal("1", Files.readString(effectPath).trim(), "first effect recorded");

        for (String forbidden : List.of(
                "\"display_name\"",
                "\"route_advertisement_types\"",
                "\"tier0_path\"",
                "\"tags\"",
                "\"children\"",
                "\"default_rule_logging\"",
                "\"_revision\"",
                ":null",
                ":[]",
                ":{}"
        )) {
            check(!patch.body().contains(forbidden), "omitted body token " + forbidden);
        }
    }

    private static void exerciseSetOptions(
            NsxPolicyClient client,
            String token,
            Path logPath,
            Path effectPath,
            String expectedAuthorization
    ) throws Exception {
        String tier1Id = "options/" + token;
        String enforcementPoint =
                "/infra/sites/default/enforcement-points/edge A?" + token;
        NsxPolicyClient.UpdateResult result =
                client.updateTier1DescriptionIfReady(
                        tier1Id,
                        new NsxPolicyClient.Tier1DescriptionPatch(
                                "option order " + token
                        ),
                        enforcementPoint,
                        "cached"
                );
        check(result.changed(), "set-option call changed");
        List<LogRecord> log = records(logPath, 7);
        equal(7, log.size(), "set options add two requests");
        String suffix = "?enforcement_point_path=" + encode(enforcementPoint)
                + "&source=cached&type=GATEWAY_STATE";
        assertPrecheckRequest(
                log.get(5),
                tier1Id,
                expectedAuthorization,
                suffix
        );
        check(!log.get(5).rawTarget().contains("+"), "RFC 3986 uses no plus");
        equal("PatchTier1", log.get(6).operationId(), "set-option mutation");
        equal(2, log.get(6).mutationCount(), "second mutation effect");
        equal("2", Files.readString(effectPath).trim(), "second effect recorded");
    }

    private static void assertPrecheckRequest(
            LogRecord record,
            String tier1Id,
            String expectedAuthorization,
            String querySuffix
    ) {
        equal("GetTier1State", record.operationId(), "precheck operationId");
        equal("GET", record.method(), "precheck method");
        equal(
                "/policy/api/v1/infra/tier-1s/" + encode(tier1Id)
                        + "/state" + querySuffix,
                record.rawTarget(),
                "exact precheck target"
        );
        equal(expectedAuthorization, record.authorization(), "precheck Basic auth");
        equal(1, record.authorizationCount(), "one precheck Authorization header");
        equal("application/json", record.accept(), "precheck Accept");
        equal(1, record.acceptCount(), "one precheck Accept header");
        equal("", record.contentType(), "GET Content-Type omitted");
        equal(0, record.contentTypeCount(), "no GET Content-Type header");
        equal("", record.contentLength(), "GET Content-Length omitted");
        equal("", record.body(), "GET body omitted");
        for (String forbidden : List.of(
                "cursor=",
                "included_fields=",
                "interface_path=",
                "page_size=",
                "sort_ascending=",
                "sort_by="
        )) {
            check(
                    !record.rawTarget().contains(forbidden),
                    "unset query omitted: " + forbidden
            );
        }
    }

    private static List<LogRecord> records(Path path, int expectedMinimum)
            throws Exception {
        long deadline = System.nanoTime() + Duration.ofSeconds(2).toNanos();
        List<String> lines = List.of();
        do {
            if (Files.exists(path)) {
                lines = Files.readAllLines(path, StandardCharsets.US_ASCII);
                lines = lines.stream().filter(line -> !line.isEmpty()).toList();
            }
            if (lines.size() >= expectedMinimum) {
                break;
            }
            Thread.sleep(10);
        } while (System.nanoTime() < deadline);

        List<LogRecord> result = new ArrayList<>();
        for (String line : lines) {
            result.add(LogRecord.parse(line));
        }
        return List.copyOf(result);
    }

    private static String encode(String value) {
        byte[] bytes = value.getBytes(StandardCharsets.UTF_8);
        StringBuilder encoded = new StringBuilder(bytes.length * 3);
        for (byte item : bytes) {
            int valueByte = item & 0xff;
            if ((valueByte >= 'a' && valueByte <= 'z')
                    || (valueByte >= 'A' && valueByte <= 'Z')
                    || (valueByte >= '0' && valueByte <= '9')
                    || valueByte == '-'
                    || valueByte == '.'
                    || valueByte == '_'
                    || valueByte == '~') {
                encoded.append((char) valueByte);
            } else {
                encoded.append('%');
                encoded.append("0123456789ABCDEF".charAt(valueByte >>> 4));
                encoded.append("0123456789ABCDEF".charAt(valueByte & 0x0f));
            }
        }
        return encoded.toString();
    }

    private static String jsonString(String value) {
        StringBuilder escaped = new StringBuilder(value.length() + 2);
        escaped.append('"');
        for (int index = 0; index < value.length(); index++) {
            char character = value.charAt(index);
            switch (character) {
                case '"' -> escaped.append("\\\"");
                case '\\' -> escaped.append("\\\\");
                case '\b' -> escaped.append("\\b");
                case '\f' -> escaped.append("\\f");
                case '\n' -> escaped.append("\\n");
                case '\r' -> escaped.append("\\r");
                case '\t' -> escaped.append("\\t");
                default -> {
                    if (character < 0x20) {
                        escaped.append(String.format("\\u%04x", (int) character));
                    } else {
                        escaped.append(character);
                    }
                }
            }
        }
        return escaped.append('"').toString();
    }

    private static <T extends Throwable> T expect(
            Class<T> type,
            ThrowingRunnable action,
            String message
    ) throws Exception {
        checks++;
        try {
            action.run();
        } catch (Throwable thrown) {
            if (type.isInstance(thrown)) {
                return type.cast(thrown);
            }
            throw new AssertionError(
                    message + ": expected " + type.getName()
                            + " but got " + thrown,
                    thrown
            );
        }
        throw new AssertionError(message + ": expected " + type.getName());
    }

    private static void check(boolean condition, String message) {
        checks++;
        if (!condition) {
            throw new AssertionError(message);
        }
    }

    private static void equal(Object expected, Object actual, String message) {
        checks++;
        if (!java.util.Objects.equals(expected, actual)) {
            throw new AssertionError(
                    message + ": expected <" + expected + "> but got <" + actual + ">"
            );
        }
    }
}
