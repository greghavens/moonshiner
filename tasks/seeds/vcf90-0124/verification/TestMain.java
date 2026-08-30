import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.time.Duration;
import java.util.HexFormat;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Optional;
import java.util.TimeZone;

public final class TestMain {
    private static final Path CONTRACT = Path.of("docs", "contract.json");
    private static final Path SOURCES = Path.of("docs", "official_sources.json");
    private static final String CONTRACT_SHA256 =
            "f8b36bae3478c9f71f4cccce7d21375f810547a2acca8187857c4e3bf098710f";
    private static final String SOURCES_SHA256 =
            "1a4d0c692d4727ee774e2b6eb7bb43c223d3434b3cc72de8dab37f521a5570c1";

    public static void main(String[] args) throws Exception {
        Locale.setDefault(Locale.ROOT);
        TimeZone.setDefault(TimeZone.getTimeZone("UTC"));
        checkFixtureDigest(CONTRACT, CONTRACT_SHA256);
        checkFixtureDigest(SOURCES, SOURCES_SHA256);
        checkOfficialSourceProvenance();

        try (MockVsanServer mock = MockVsanServer.start(CONTRACT)) {
            VsanDataProtectionClient client = new VsanDataProtectionClient(
                    mock.apiBaseUri(), "session-test-9", Duration.ZERO);
            VsanDataProtectionClient.TaskResult result =
                    client.createProtectionGroupSnapshotAndWait(
                            "domain-c8",
                            "pg-blue",
                            "pre-upgrade \"gold\"",
                            Optional.empty());

            check("task-42".equals(result.taskId()), "task identifier was not preserved");
            check("SUCCEEDED".equals(result.status()),
                    "terminal task status should be SUCCEEDED, got " + result.status());

            List<MockVsanServer.RequestRecord> requests = mock.requestLog();
            check(requests.size() == 5,
                    "asynchronous create must be polled through every non-terminal state; requests="
                            + requests.size());
            checkCreateRequest(requests.get(0));
            for (int index = 1; index < requests.size(); index++) {
                checkTaskRequest(requests.get(index), index);
            }

            HttpResponse<String> rejected = HttpClient.newHttpClient().send(
                    HttpRequest.newBuilder(mock.apiBaseUri().resolve("cluster-pairs"))
                            .GET()
                            .build(),
                    HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8));
            check(rejected.statusCode() == 404,
                    "mock exposed an operation outside docs/contract.json");
            check(mock.requestLog().size() == 6, "mock request log is not readable by the test");
        }

        try (MockVsanServer mock = MockVsanServer.startFailing(CONTRACT)) {
            VsanDataProtectionClient client = new VsanDataProtectionClient(
                    mock.apiBaseUri(), "session-test-9", Duration.ZERO);
            VsanDataProtectionClient.TaskResult result =
                    client.createProtectionGroupSnapshotAndWait(
                            "domain-c8", "pg-blue", "failure-check", Optional.empty());
            check("FAILED".equals(result.status()),
                    "FAILED is terminal and must be returned without further polling");
            check(mock.requestLog().size() == 5,
                    "FAILED scenario should stop after the terminal task response; requests="
                            + mock.requestLog().size());
        }

        System.out.println("vSAN Data Protection async contract checks passed");
    }

    private static void checkCreateRequest(MockVsanServer.RequestRecord request) {
        check("POST".equals(request.method()), "create method must be POST");
        check("/api/snapservice/clusters/domain-c8/protection-groups/pg-blue/snapshots"
                        .equals(request.rawPath()),
                "create path mismatch: " + request.rawPath());
        check("vmw-task=true".equals(request.rawQuery()),
                "create query must be exactly vmw-task=true");
        check("application/json".equals(header(request.headers(), "Content-Type")),
                "create Content-Type must be application/json");
        checkCommonHeaders(request.headers());

        String expectedBody = "{\"name\":\"pre-upgrade \\\"gold\\\"\"}";
        check(expectedBody.equals(request.body()),
                "create JSON wire body mismatch: " + request.body());
        check(!request.body().contains("retention"),
                "unset optional retention must be omitted from the request body");
    }

    private static void checkTaskRequest(MockVsanServer.RequestRecord request, int pollNumber) {
        check("GET".equals(request.method()), "poll " + pollNumber + " method must be GET");
        check("/api/snapservice/tasks/task-42".equals(request.rawPath()),
                "poll " + pollNumber + " path mismatch: " + request.rawPath());
        check(request.rawQuery() == null, "poll " + pollNumber + " must not send a query");
        check(request.body().isEmpty(), "poll " + pollNumber + " must not send a body");
        check(header(request.headers(), "Content-Type") == null,
                "poll " + pollNumber + " must not send Content-Type");
        checkCommonHeaders(request.headers());
    }

    private static void checkCommonHeaders(Map<String, List<String>> headers) {
        check("application/json".equals(header(headers, "Accept")),
                "Accept must be application/json");
        check("session-test-9".equals(header(headers, "vmware-api-session-id")),
                "vmware-api-session-id header mismatch");
    }

    private static String header(Map<String, List<String>> headers, String wanted) {
        for (Map.Entry<String, List<String>> entry : headers.entrySet()) {
            if (entry.getKey().equalsIgnoreCase(wanted)) {
                check(entry.getValue().size() == 1, wanted + " must occur exactly once");
                return entry.getValue().get(0);
            }
        }
        return null;
    }

    private static void checkOfficialSourceProvenance() throws Exception {
        String sources = Files.readString(SOURCES, StandardCharsets.UTF_8);
        for (String required : List.of(
                "\"tag\": \"9.0.0.0\"",
                "\"commit_sha\": \"85151f6b1bb58f13b6ac0304bfec53904bea085f\"",
                "\"spec_path\": \"specifications/vsan-data-protection/vsan-data-protection-openapi.yaml\"",
                "\"operationId\": \"Snapservice.Clusters.ProtectionGroups.Snapshots_create$Task\"",
                "\"operationId\": \"Snapservice.Tasks_get\"")) {
            check(sources.contains(required), "official source provenance missing " + required);
        }
        check(!sources.contains("9.1"), "official sources must not reference the 9.1 specification");
    }

    private static void checkFixtureDigest(Path path, String expected) throws Exception {
        byte[] bytes = Files.readAllBytes(path);
        String actual = HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes));
        check(expected.equals(actual), path + " was modified; expected " + expected + ", got " + actual);
    }

    private static void check(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }
}
