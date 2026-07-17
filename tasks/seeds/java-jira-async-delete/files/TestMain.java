// Acceptance tests for the project retention tooling. Everything runs against
// a loopback com.sun.net.httpserver mock speaking the Jira Cloud REST API v3
// wire contract pinned in docs/contract.json — no real site, no credentials.
//
// The existing ProjectAdminClient behavior (getProject/archiveProject) is
// pinned here too and must keep passing alongside the new retirement flow.

import com.sun.net.httpserver.HttpServer;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Base64;
import java.util.Collections;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Objects;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.function.BiFunction;

public final class TestMain {
    static final String EMAIL = "adminbot@example.com";
    static final String TOKEN = "dummy-jira-api-token-7742";
    static final String BASIC = "Basic "
            + Base64.getEncoder().encodeToString((EMAIL + ":" + TOKEN).getBytes(StandardCharsets.UTF_8));

    record Recorded(String method, String path, Map<String, String> headers, String body) {}

    record Response(int status, String body, Map<String, String> extraHeaders) {
        static Response json(int status, String body) {
            return new Response(status, body, Map.of());
        }

        static Response empty(int status) {
            return new Response(status, null, Map.of());
        }
    }

    static final class MockJira implements AutoCloseable {
        final HttpServer server;
        final List<Recorded> requests = Collections.synchronizedList(new ArrayList<>());

        MockJira(BiFunction<Integer, Recorded, Response> serve) throws Exception {
            server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
            server.createContext("/", exchange -> {
                byte[] raw = exchange.getRequestBody().readAllBytes();
                Map<String, String> headers = new HashMap<>();
                exchange.getRequestHeaders()
                        .forEach((k, v) -> headers.put(k.toLowerCase(Locale.ROOT), String.join(",", v)));
                Recorded rec = new Recorded(
                        exchange.getRequestMethod(),
                        exchange.getRequestURI().getPath(),
                        headers,
                        new String(raw, StandardCharsets.UTF_8));
                int n = requests.size();
                requests.add(rec);
                Response response = serve.apply(n, rec);
                response.extraHeaders().forEach((k, v) -> exchange.getResponseHeaders().add(k, v));
                if (response.body() == null) {
                    exchange.sendResponseHeaders(response.status(), -1);
                } else {
                    byte[] out = response.body().getBytes(StandardCharsets.UTF_8);
                    exchange.getResponseHeaders().add("Content-Type", "application/json;charset=UTF-8");
                    exchange.sendResponseHeaders(response.status(), out.length);
                    exchange.getResponseBody().write(out);
                }
                exchange.close();
            });
            server.start();
        }

        String base() {
            return "http://127.0.0.1:" + server.getAddress().getPort();
        }

        @Override
        public void close() {
            server.stop(0);
        }
    }

    static final class RecordingBackoff implements ProjectRetirement.Backoff {
        final List<Integer> pauses = new ArrayList<>();

        @Override
        public void pause(int attempt) {
            pauses.add(attempt);
        }
    }

    static void check(boolean cond, String label) {
        if (!cond) {
            throw new AssertionError(label);
        }
    }

    static void checkEq(Object expected, Object actual, String label) {
        if (!Objects.equals(expected, actual)) {
            throw new AssertionError(label + ": expected " + expected + " but got " + actual);
        }
    }

    static void checkAuthed(Recorded req) {
        checkEq(BASIC, req.headers().get("authorization"), "basic auth header on " + req.path());
        checkEq("application/json", req.headers().get("accept"), "accept header on " + req.path());
    }

    static String taskBean(String base, String id, String status, long progress, long lastUpdate, String extra) {
        return "{\"self\":\"" + base + "/rest/api/3/task/" + id + "\",\"id\":\"" + id + "\","
                + "\"status\":\"" + status + "\",\"progress\":" + progress + ","
                + "\"lastUpdate\":" + lastUpdate + ",\"submitted\":1766999990000,"
                + "\"submittedBy\":10000,\"elapsedRuntime\":120,"
                + "\"description\":\"Deletes the project and its data.\""
                + (extra.isEmpty() ? "" : "," + extra) + "}";
    }

    // ---- existing behavior: project lookup and archival -------------------

    static void testGetProjectMapsFlags() throws Exception {
        try (MockJira mock = new MockJira((n, req) -> switch (n) {
            case 0 -> Response.json(200,
                    "{\"id\":\"10042\",\"key\":\"OPS\",\"name\":\"Ops Tooling\",\"deleted\":false,"
                            + "\"style\":\"classic\"}");
            default -> Response.json(200,
                    "{\"id\":\"10077\",\"key\":\"OLD\",\"name\":\"Legacy Intake\",\"archived\":true,"
                            + "\"archivedDate\":\"2026-05-02T08:00:00.000+0000\",\"deleted\":false}");
        })) {
            ProjectAdminClient client = new ProjectAdminClient(mock.base(), EMAIL, TOKEN);

            ProjectAdminClient.ProjectInfo ops = client.getProject("OPS");
            checkEq("GET", mock.requests.get(0).method(), "project lookup method");
            checkEq("/rest/api/3/project/OPS", mock.requests.get(0).path(), "project lookup path");
            checkAuthed(mock.requests.get(0));
            checkEq("10042", ops.id(), "project id");
            checkEq("OPS", ops.key(), "project key");
            checkEq("Ops Tooling", ops.name(), "project name");
            checkEq(false, ops.archived(), "an active project has no archived flag");
            checkEq(false, ops.deleted(), "deleted flag");

            ProjectAdminClient.ProjectInfo old = client.getProject("OLD");
            checkEq(true, old.archived(), "archived flag");
            checkEq(false, old.deleted(), "archived is not trashed");
        }
    }

    static void testArchiveProject() throws Exception {
        try (MockJira mock = new MockJira((n, req) -> Response.empty(204))) {
            ProjectAdminClient client = new ProjectAdminClient(mock.base(), EMAIL, TOKEN);
            client.archiveProject("OLD");
            checkEq(1, mock.requests.size(), "one archive call");
            Recorded req = mock.requests.get(0);
            checkEq("POST", req.method(), "archive method");
            checkEq("/rest/api/3/project/OLD/archive", req.path(), "archive path");
            checkAuthed(req);
            checkEq("", req.body(), "archive sends no body");
        }
    }

    static void testArchiveNotFound() throws Exception {
        try (MockJira mock = new MockJira((n, req) -> Response.json(404,
                "{\"errorMessages\":[\"No project could be found with key 'GONE'.\"],\"errors\":{}}"))) {
            ProjectAdminClient client = new ProjectAdminClient(mock.base(), EMAIL, TOKEN);
            try {
                client.archiveProject("GONE");
                throw new AssertionError("archiving a missing project must fail");
            } catch (JiraApiException e) {
                checkEq(404, e.status(), "http status");
                checkEq(List.of("No project could be found with key 'GONE'."), e.errorMessages(),
                        "decoded error messages");
                check(e.getMessage().contains("No project could be found"), "human-readable message");
                check(!e.getMessage().contains(TOKEN), "error text leaks the API token");
            }
        }
    }

    // ---- new behavior: asynchronous purge ---------------------------------

    static void testPurgeActiveProject() throws Exception {
        final String[] base = new String[1];
        try (MockJira mock = new MockJira((n, req) -> switch (n) {
            case 0 -> Response.json(200,
                    "{\"id\":\"10042\",\"key\":\"OPS\",\"name\":\"Ops Tooling\",\"deleted\":false}");
            case 1 -> new Response(303, taskBean(base[0], "10054", "ENQUEUED", 0, 1767000000100L, ""),
                    Map.of("Location", base[0] + "/rest/api/3/task/10054"));
            // Task progress is not ordered: percentages and lastUpdate stamps
            // may regress between polls. Only the status is authoritative.
            case 2 -> Response.json(200, taskBean(base[0], "10054", "RUNNING", 30, 1767000002000L, ""));
            case 3 -> Response.json(200, taskBean(base[0], "10054", "RUNNING", 80, 1767000001000L, ""));
            case 4 -> Response.json(200, taskBean(base[0], "10054", "RUNNING", 55, 1767000003000L, ""));
            case 5 -> Response.json(200, taskBean(base[0], "10054", "COMPLETE", 100, 1767000004000L,
                    "\"message\":\"Cleanup complete.\",\"result\":\"Deleted project OPS.\""));
            default -> Response.json(500, "{\"errorMessages\":[\"unexpected request\"],\"errors\":{}}");
        })) {
            base[0] = mock.base();
            ProjectAdminClient client = new ProjectAdminClient(mock.base(), EMAIL, TOKEN);
            RecordingBackoff backoff = new RecordingBackoff();
            ProjectRetirement retirement = new ProjectRetirement(client, 8, backoff);

            ProjectRetirement.Result result = retirement.purge("OPS");

            checkEq("10054", result.taskId(), "task id");
            checkEq(false, result.restored(), "active project needs no restore");
            checkEq(4, result.pollCount(), "task polls");
            checkEq("Deleted project OPS.", result.taskResult(), "task result payload");

            checkEq(6, mock.requests.size(), "request count");
            checkEq("GET", mock.requests.get(0).method(), "state check first");
            checkEq("/rest/api/3/project/OPS", mock.requests.get(0).path(), "state check path");
            checkEq("POST", mock.requests.get(1).method(), "async delete is a POST");
            checkEq("/rest/api/3/project/OPS/delete", mock.requests.get(1).path(), "async delete path");
            checkEq("", mock.requests.get(1).body(), "async delete sends no body");
            for (int i = 2; i <= 5; i++) {
                checkEq("GET", mock.requests.get(i).method(), "task poll method");
                checkEq("/rest/api/3/task/10054", mock.requests.get(i).path(),
                        "task polls follow the 303 Location");
            }
            for (Recorded req : mock.requests) {
                checkAuthed(req);
                check(!"DELETE".equals(req.method()),
                        "the synchronous DELETE project endpoint must never be used");
                check(!req.path().endsWith("/restore"), "no restore for an active project");
            }
            checkEq(List.of(1, 2, 3), backoff.pauses, "one pause between consecutive polls");
        }
    }

    static void testPurgeArchivedRestoresFirst() throws Exception {
        final String[] base = new String[1];
        final AtomicBoolean archived = new AtomicBoolean(true);
        try (MockJira mock = new MockJira((n, req) -> {
            if (n == 0) {
                return Response.json(200,
                        "{\"id\":\"10077\",\"key\":\"OLD\",\"name\":\"Legacy Intake\",\"archived\":true,"
                                + "\"deleted\":false}");
            }
            if (req.path().equals("/rest/api/3/project/OLD/restore")) {
                archived.set(false);
                return Response.json(200,
                        "{\"id\":\"10077\",\"key\":\"OLD\",\"name\":\"Legacy Intake\",\"deleted\":false}");
            }
            if (req.path().equals("/rest/api/3/project/OLD/delete")) {
                if (archived.get()) {
                    return Response.json(400,
                            "{\"errorMessages\":[\"Archived projects cannot be deleted. Restore the project"
                                    + " first.\"],\"errors\":{}}");
                }
                return new Response(303, taskBean(base[0], "777", "ENQUEUED", 0, 1767000000100L, ""),
                        Map.of("Location", base[0] + "/rest/api/3/task/777"));
            }
            if (req.path().equals("/rest/api/3/task/777")) {
                return Response.json(200, taskBean(base[0], "777", "COMPLETE", 100, 1767000004000L,
                        "\"result\":\"Deleted project OLD.\""));
            }
            return Response.json(500, "{\"errorMessages\":[\"unexpected request\"],\"errors\":{}}");
        })) {
            base[0] = mock.base();
            ProjectAdminClient client = new ProjectAdminClient(mock.base(), EMAIL, TOKEN);
            RecordingBackoff backoff = new RecordingBackoff();
            ProjectRetirement retirement = new ProjectRetirement(client, 8, backoff);

            ProjectRetirement.Result result = retirement.purge("OLD");

            checkEq(true, result.restored(), "archived project was restored before deletion");
            checkEq("777", result.taskId(), "task id");
            checkEq(1, result.pollCount(), "task polls");
            checkEq(4, mock.requests.size(), "request count");
            checkEq("/rest/api/3/project/OLD", mock.requests.get(0).path(), "state check first");
            checkEq("POST", mock.requests.get(1).method(), "restore method");
            checkEq("/rest/api/3/project/OLD/restore", mock.requests.get(1).path(),
                    "an archived project cannot be deleted — restore comes first");
            checkEq("/rest/api/3/project/OLD/delete", mock.requests.get(2).path(),
                    "delete only after restore");
            checkEq("/rest/api/3/task/777", mock.requests.get(3).path(), "task poll");
            checkEq(List.of(), backoff.pauses, "no pause before the first poll");
        }
    }

    static void testPurgeTaskFailure() throws Exception {
        final String[] base = new String[1];
        try (MockJira mock = new MockJira((n, req) -> switch (n) {
            case 0 -> Response.json(200,
                    "{\"id\":\"10042\",\"key\":\"OPS\",\"name\":\"Ops Tooling\",\"deleted\":false}");
            case 1 -> new Response(303, taskBean(base[0], "9001", "ENQUEUED", 0, 1767000000100L, ""),
                    Map.of("Location", base[0] + "/rest/api/3/task/9001"));
            case 2 -> Response.json(200, taskBean(base[0], "9001", "RUNNING", 40, 1767000002000L, ""));
            case 3 -> Response.json(200, taskBean(base[0], "9001", "FAILED", 40, 1767000003000L,
                    "\"message\":\"attachment store unavailable\""));
            default -> Response.json(500, "{\"errorMessages\":[\"unexpected request\"],\"errors\":{}}");
        })) {
            base[0] = mock.base();
            ProjectAdminClient client = new ProjectAdminClient(mock.base(), EMAIL, TOKEN);
            RecordingBackoff backoff = new RecordingBackoff();
            ProjectRetirement retirement = new ProjectRetirement(client, 8, backoff);

            try {
                retirement.purge("OPS");
                throw new AssertionError("a FAILED task must raise");
            } catch (ProjectRetirement.TaskFailedException e) {
                checkEq("9001", e.taskId(), "failed task id");
                checkEq("FAILED", e.taskStatus(), "failed task status");
                check(e.getMessage().contains("attachment store unavailable"),
                        "task message is preserved");
                check(e.getMessage().toLowerCase(Locale.ROOT).contains("not deleted"),
                        "the transactional guarantee is spelled out: the project was not deleted");
                check(!e.getMessage().contains(TOKEN), "error text leaks the API token");
            }
            checkEq(4, mock.requests.size(), "no further polls after a terminal failure");
            checkEq(List.of(1), backoff.pauses, "one pause between the two polls");
        }
    }

    static void testPurgeBoundedPolling() throws Exception {
        final String[] base = new String[1];
        try (MockJira mock = new MockJira((n, req) -> switch (n) {
            case 0 -> Response.json(200,
                    "{\"id\":\"10042\",\"key\":\"OPS\",\"name\":\"Ops Tooling\",\"deleted\":false}");
            case 1 -> new Response(303, taskBean(base[0], "31337", "ENQUEUED", 0, 1767000000100L, ""),
                    Map.of("Location", base[0] + "/rest/api/3/task/31337"));
            default -> Response.json(200,
                    taskBean(base[0], "31337", "RUNNING", 10, 1767000002000L, ""));
        })) {
            base[0] = mock.base();
            ProjectAdminClient client = new ProjectAdminClient(mock.base(), EMAIL, TOKEN);
            RecordingBackoff backoff = new RecordingBackoff();
            ProjectRetirement retirement = new ProjectRetirement(client, 3, backoff);

            try {
                retirement.purge("OPS");
                throw new AssertionError("a stuck task must not poll forever");
            } catch (IllegalStateException e) {
                check(e.getMessage().toLowerCase(Locale.ROOT).contains("still running"),
                        "stuck-task message says the task is still running");
            }
            checkEq(5, mock.requests.size(), "exactly maxPolls task polls: 2 + 3");
            checkEq(List.of(1, 2), backoff.pauses, "pauses only between polls");
        }
    }

    static void testMissingLocationRejected() throws Exception {
        try (MockJira mock = new MockJira((n, req) -> switch (n) {
            case 0 -> Response.json(200,
                    "{\"id\":\"10042\",\"key\":\"OPS\",\"name\":\"Ops Tooling\",\"deleted\":false}");
            case 1 -> Response.json(303, "{}");
            default -> Response.json(500, "{\"errorMessages\":[\"unexpected request\"],\"errors\":{}}");
        })) {
            ProjectAdminClient client = new ProjectAdminClient(mock.base(), EMAIL, TOKEN);
            ProjectRetirement retirement = new ProjectRetirement(client, 3, new RecordingBackoff());
            try {
                retirement.purge("OPS");
                throw new AssertionError("a 303 without Location is a contract violation");
            } catch (IllegalStateException e) {
                check(e.getMessage().contains("Location"), "message names the missing Location header");
            }
            checkEq(2, mock.requests.size(), "nothing to poll without a Location");
        }
    }

    public static void main(String[] args) throws Exception {
        testGetProjectMapsFlags();
        System.out.println("ok  getProject maps archival flags");
        testArchiveProject();
        System.out.println("ok  archiveProject posts to the archive resource");
        testArchiveNotFound();
        System.out.println("ok  archive failures decode the error collection");
        testPurgeActiveProject();
        System.out.println("ok  purge follows 303 Location and polls to COMPLETE");
        testPurgeArchivedRestoresFirst();
        System.out.println("ok  purge restores an archived project before deleting");
        testPurgeTaskFailure();
        System.out.println("ok  purge surfaces transactional task failure");
        testPurgeBoundedPolling();
        System.out.println("ok  purge polling is bounded by maxPolls");
        testMissingLocationRejected();
        System.out.println("ok  purge rejects a 303 without Location");
        System.out.println("all 8 tests passed");
    }
}
