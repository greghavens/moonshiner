// Acceptance tests for the Splunk search-job client.
//
// Runs a loopback fake splunkd (search/v2 job creation, status, paged
// results, v1 job control, REST error envelopes) and drives the client
// against it. No real Splunk, no real credentials, no Thread.sleep —
// waiting is injected and recorded. The wire contract the fake enforces
// is pinned in docs/contract.json. This file and everything under docs/
// are protected; Json.java is starter code you may extend.

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

public final class TestMain {

    static final String TOKEN =
        "eyJrIjoic3BsdW5rLWZpeHR1cmUtdG9rZW4tZHVtbXkifQ.not-a-real-jwt";
    static final String SID = "1770001234.117_A1B2";

    static int checks = 0;

    static void check(boolean ok, String message) {
        checks++;
        if (!ok) {
            throw new AssertionError(message);
        }
    }

    record Req(String method, String path, String query,
               Map<String, String> headers, String body) {
        Map<String, String> form() {
            Map<String, String> out = new LinkedHashMap<>();
            String source = "GET".equals(method) ? query : body;
            if (source == null || source.isEmpty()) {
                return out;
            }
            for (String pair : source.split("&")) {
                int eq = pair.indexOf('=');
                String k = eq < 0 ? pair : pair.substring(0, eq);
                String v = eq < 0 ? "" : pair.substring(eq + 1);
                out.put(URLDecoder.decode(k, StandardCharsets.UTF_8),
                        URLDecoder.decode(v, StandardCharsets.UTF_8));
            }
            return out;
        }
    }

    record Scripted(int status, String json) {
    }

    static final class FakeSplunkd implements AutoCloseable {
        final List<Req> requests = new ArrayList<>();
        final List<Scripted> script = new ArrayList<>();
        final HttpServer server;
        final String baseUrl;

        FakeSplunkd() throws IOException {
            server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
            server.createContext("/", this::handle);
            server.start();
            baseUrl = "http://127.0.0.1:" + server.getAddress().getPort();
        }

        void queue(int status, String json) {
            script.add(new Scripted(status, json));
        }

        synchronized void handle(HttpExchange ex) throws IOException {
            Map<String, String> headers = new LinkedHashMap<>();
            ex.getRequestHeaders().forEach((k, v) ->
                headers.put(k.toLowerCase(Locale.ROOT), String.join(",", v)));
            String body = new String(
                ex.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
            requests.add(new Req(
                ex.getRequestMethod(),
                ex.getRequestURI().getRawPath(),
                ex.getRequestURI().getRawQuery() == null
                    ? "" : ex.getRequestURI().getRawQuery(),
                headers, body));
            Scripted next = script.isEmpty()
                ? new Scripted(500, "{\"messages\":[{\"type\":\"ERROR\","
                    + "\"text\":\"fake splunkd: script exhausted\"}]}")
                : script.remove(0);
            byte[] payload = next.json.getBytes(StandardCharsets.UTF_8);
            ex.getResponseHeaders().set("Content-Type", "application/json");
            if (next.status == 204) {
                ex.sendResponseHeaders(204, -1);
            } else {
                ex.sendResponseHeaders(next.status, payload.length);
                try (OutputStream out = ex.getResponseBody()) {
                    out.write(payload);
                }
            }
            ex.close();
        }

        @Override
        public void close() {
            server.stop(0);
        }
    }

    // ---- canned splunkd JSON bodies ------------------------------------

    static String sidBody(String sid) {
        return Json.write(Map.of("sid", sid));
    }

    static String jobStatusBody(String dispatchState, boolean isDone,
                                boolean isFailed, int resultCount,
                                List<Map<String, Object>> messages) {
        Map<String, Object> content = new LinkedHashMap<>();
        content.put("sid", SID);
        content.put("dispatchState", dispatchState);
        content.put("isDone", isDone);
        content.put("isFailed", isFailed);
        content.put("doneProgress", isDone ? 1.0 : 0.4);
        content.put("resultCount", (double) resultCount);
        content.put("messages", messages);
        Map<String, Object> entry = new LinkedHashMap<>();
        entry.put("name", "search index=web");
        entry.put("content", content);
        return Json.write(Map.of("entry", List.of(entry)));
    }

    static String resultsBody(List<Map<String, Object>> rows, int initOffset) {
        Map<String, Object> doc = new LinkedHashMap<>();
        doc.put("preview", false);
        doc.put("init_offset", (double) initOffset);
        doc.put("fields", List.of(Map.of("name", "host"), Map.of("name", "count")));
        doc.put("results", rows);
        return Json.write(doc);
    }

    static String messagesBody(String type, String text) {
        return Json.write(Map.of(
            "messages", List.of(Map.of("type", type, "text", text))));
    }

    static Map<String, Object> row(String host, String count) {
        Map<String, Object> r = new LinkedHashMap<>();
        r.put("host", host);
        r.put("count", count);
        return r;
    }

    // ---- fixture --------------------------------------------------------

    interface Body {
        void run(FakeSplunkd splunkd, SplunkSearchClient client,
                 List<Long> sleeps) throws Exception;
    }

    static void withFixture(String name, Body body) throws Exception {
        try (FakeSplunkd splunkd = new FakeSplunkd()) {
            List<Long> sleeps = new ArrayList<>();
            SplunkSearchClient client =
                new SplunkSearchClient(splunkd.baseUrl, TOKEN, sleeps::add);
            body.run(splunkd, client, sleeps);
        }
        System.out.println("ok  " + name);
    }

    // ---- tests ----------------------------------------------------------

    static void createJobPostsDocumentedForm() throws Exception {
        withFixture("createJobPostsDocumentedForm", (splunkd, client, sleeps) -> {
            splunkd.queue(201, sidBody(SID));
            String sid = client.createJob(
                "index=web status=500 | stats count by host", "-24h@h", "now");
            check(SID.equals(sid), "createJob must return the sid, got " + sid);
            check(splunkd.requests.size() == 1, "exactly one create request");
            Req req = splunkd.requests.get(0);
            check("POST".equals(req.method()), "job creation is a POST");
            check("/services/search/v2/jobs".equals(req.path()),
                "jobs are created on the v2 endpoint, got " + req.path());
            check(("Bearer " + TOKEN).equals(req.headers().get("authorization")),
                "token auth uses the Authorization: Bearer header");
            check(req.headers().getOrDefault("content-type", "")
                    .startsWith("application/x-www-form-urlencoded"),
                "job creation is form-encoded, got "
                    + req.headers().get("content-type"));
            Map<String, String> form = req.form();
            check("search index=web status=500 | stats count by host"
                    .equals(form.get("search")),
                "plain SPL must be prefixed with the search command, got "
                    + form.get("search"));
            check("-24h@h".equals(form.get("earliest_time")), "earliest_time");
            check("now".equals(form.get("latest_time")), "latest_time");
            check("normal".equals(form.get("exec_mode")),
                "exec_mode=normal so the job is asynchronous");
            check("json".equals(form.get("output_mode")),
                "output_mode=json so the sid comes back as JSON");
            check(!req.query().contains(TOKEN) && !req.path().contains(TOKEN),
                "the token must never appear in a URL");
        });
    }

    static void createJobKeepsGeneratingCommands() throws Exception {
        withFixture("createJobKeepsGeneratingCommands", (splunkd, client, sleeps) -> {
            splunkd.queue(201, sidBody("1770001234.118"));
            splunkd.queue(201, sidBody("1770001234.119"));
            client.createJob("| makeresults count=3", "-1h", "now");
            client.createJob("search index=main", "-1h", "now");
            check("| makeresults count=3"
                    .equals(splunkd.requests.get(0).form().get("search")),
                "generating-command SPL must not get a search prefix");
            check("search index=main"
                    .equals(splunkd.requests.get(1).form().get("search")),
                "SPL already starting with search stays untouched");
        });
    }

    static void createJobParsesSplunkError() throws Exception {
        withFixture("createJobParsesSplunkError", (splunkd, client, sleeps) -> {
            splunkd.queue(400,
                messagesBody("FATAL", "Unknown search command 'serach'."));
            try {
                client.createJob("index=web | serach oops", "-1h", "now");
                check(false, "a 400 from splunkd must raise SplunkRestException");
            } catch (SplunkRestException e) {
                check(e.getStatus() == 400, "status carried on the exception");
                check("FATAL".equals(e.getType()),
                    "message type parsed from the messages array");
                check("Unknown search command 'serach'.".equals(e.getMessageText()),
                    "message text parsed from the messages array");
                check(e.getMessage().contains("Unknown search command"),
                    "exception message includes the splunkd text");
                check(!e.getMessage().contains(TOKEN),
                    "the token must never appear in exception messages");
            }
        });
    }

    static void pollUntilDoneWalksDispatchStates() throws Exception {
        withFixture("pollUntilDoneWalksDispatchStates", (splunkd, client, sleeps) -> {
            splunkd.queue(200, jobStatusBody("QUEUED", false, false, 0, List.of()));
            splunkd.queue(200, jobStatusBody("RUNNING", false, false, 12, List.of()));
            splunkd.queue(200, jobStatusBody("DONE", true, false, 57, List.of()));
            JobStatus status = client.pollUntilDone(SID, 250L, 10);
            check("DONE".equals(status.dispatchState()),
                "terminal dispatchState reported");
            check(status.resultCount() == 57, "resultCount from the DONE poll");
            check(splunkd.requests.size() == 3, "three status polls");
            for (Req req : splunkd.requests) {
                check("GET".equals(req.method())
                        && ("/services/search/v2/jobs/" + SID).equals(req.path()),
                    "status polls GET the v2 job entity, got " + req.path());
                check("json".equals(req.form().get("output_mode")),
                    "status polls request output_mode=json");
            }
            check(List.of(250L, 250L).equals(sleeps),
                "sleeps happen between polls only, got " + sleeps);
        });
    }

    static void pollUntilDoneRaisesOnFailedJob() throws Exception {
        withFixture("pollUntilDoneRaisesOnFailedJob", (splunkd, client, sleeps) -> {
            splunkd.queue(200, jobStatusBody("FAILED", false, true, 0,
                List.of(Map.of("type", "FATAL",
                    "text", "Error in 'stats' command: The argument 'coutn' is invalid."))));
            try {
                client.pollUntilDone(SID, 250L, 10);
                check(false, "isFailed jobs must raise SplunkJobFailedException");
            } catch (SplunkJobFailedException e) {
                check(e.getMessage().contains("Error in 'stats' command"),
                    "failure carries the splunkd FATAL text, got " + e.getMessage());
            }
            check(splunkd.requests.size() == 1, "failure detected on first poll");
            check(sleeps.isEmpty(), "no sleep after a terminal poll");
        });
    }

    static void pollUntilDoneGivesUpAfterMaxPolls() throws Exception {
        withFixture("pollUntilDoneGivesUpAfterMaxPolls", (splunkd, client, sleeps) -> {
            for (int i = 0; i < 3; i++) {
                splunkd.queue(200,
                    jobStatusBody("RUNNING", false, false, 1, List.of()));
            }
            try {
                client.pollUntilDone(SID, 100L, 3);
                check(false, "still-running after max polls must raise");
            } catch (IllegalStateException expected) {
                check(true, "IllegalStateException on poll exhaustion");
            }
            check(splunkd.requests.size() == 3, "exactly maxPolls polls");
            check(List.of(100L, 100L).equals(sleeps),
                "no sleep after the final poll, got " + sleeps);
        });
    }

    static void resultsPagesWithCountAndOffset() throws Exception {
        withFixture("resultsPagesWithCountAndOffset", (splunkd, client, sleeps) -> {
            splunkd.queue(200, resultsBody(
                List.of(row("web-01", "42"), row("web-02", "17")), 0));
            splunkd.queue(200, resultsBody(
                List.of(row("web-03", "9"), row("web-04", "5")), 2));
            splunkd.queue(200, resultsBody(List.of(row("web-05", "1")), 4));
            List<Map<String, Object>> rows = client.results(SID, 2);
            check(rows.size() == 5, "all pages aggregated, got " + rows.size());
            check("web-01".equals(rows.get(0).get("host"))
                    && "web-05".equals(rows.get(4).get("host")),
                "server order preserved across pages");
            check("42".equals(rows.get(0).get("count")),
                "Splunk result cell values stay strings");
            check(splunkd.requests.size() == 3,
                "paging stops at the first short page");
            List<String> offsets = new ArrayList<>();
            for (Req req : splunkd.requests) {
                check(("/services/search/v2/jobs/" + SID + "/results")
                        .equals(req.path()),
                    "results come from the v2 results endpoint, got " + req.path());
                check("json".equals(req.form().get("output_mode")),
                    "results request output_mode=json");
                check("2".equals(req.form().get("count")),
                    "count pins the page size");
                offsets.add(req.form().get("offset"));
            }
            check(List.of("0", "2", "4").equals(offsets),
                "offset advances by count, got " + offsets);
        });
    }

    static void resultsBeforeDoneIsNotReady() throws Exception {
        withFixture("resultsBeforeDoneIsNotReady", (splunkd, client, sleeps) -> {
            splunkd.queue(204, "");
            try {
                client.results(SID, 2);
                check(false, "a 204 (job not done) must raise");
            } catch (IllegalStateException e) {
                check(e.getMessage().toLowerCase(Locale.ROOT).contains("done")
                        || e.getMessage().toLowerCase(Locale.ROOT).contains("ready"),
                    "not-ready error explains itself, got " + e.getMessage());
            }
            check(splunkd.requests.size() == 1, "no blind retry of a 204");
        });
    }

    static void finalizeAndCancelUseV1Control() throws Exception {
        withFixture("finalizeAndCancelUseV1Control", (splunkd, client, sleeps) -> {
            splunkd.queue(200, messagesBody("INFO", "Search job finalized."));
            splunkd.queue(200, messagesBody("INFO", "Search job cancelled."));
            client.finalizeJob(SID);
            client.cancel(SID);
            check(splunkd.requests.size() == 2, "one control POST per action");
            for (Req req : splunkd.requests) {
                check("POST".equals(req.method()), "control actions are POSTs");
                check(("/services/search/jobs/" + SID + "/control")
                        .equals(req.path()),
                    "job control lives on the v1 path (there is no v2 control "
                        + "endpoint), got " + req.path());
                check(("Bearer " + TOKEN).equals(req.headers().get("authorization")),
                    "control actions authenticate like everything else");
            }
            check("finalize".equals(splunkd.requests.get(0).form().get("action")),
                "finalize action name");
            check("cancel".equals(splunkd.requests.get(1).form().get("action")),
                "cancel action name");
        });
    }

    static void controlErrorsAreParsed() throws Exception {
        withFixture("controlErrorsAreParsed", (splunkd, client, sleeps) -> {
            splunkd.queue(404, messagesBody("ERROR", "Unknown sid."));
            try {
                client.cancel(SID);
                check(false, "a 404 from control must raise SplunkRestException");
            } catch (SplunkRestException e) {
                check(e.getStatus() == 404, "status 404 carried");
                check("ERROR".equals(e.getType()), "type ERROR carried");
                check("Unknown sid.".equals(e.getMessageText()), "text carried");
            }
        });
    }

    // ---- main -----------------------------------------------------------

    public static void main(String[] args) throws Exception {
        Map<String, Object> sources = Json.parseObject(
            Files.readString(Path.of("docs", "official_sources.json")));
        Map<String, Object> contract = Json.parseObject(
            Files.readString(Path.of("docs", "contract.json")));
        check(Boolean.TRUE.equals(
                ((Map<?, ?>) sources.get("research")).get("required")),
            "research provenance fixture present");
        check(contract.get("operations") instanceof Map<?, ?>,
            "contract fixture present");

        createJobPostsDocumentedForm();
        createJobKeepsGeneratingCommands();
        createJobParsesSplunkError();
        pollUntilDoneWalksDispatchStates();
        pollUntilDoneRaisesOnFailedJob();
        pollUntilDoneGivesUpAfterMaxPolls();
        resultsPagesWithCountAndOffset();
        resultsBeforeDoneIsNotReady();
        finalizeAndCancelUseV1Control();
        controlErrorsAreParsed();

        System.out.println(checks + " checks passed");
    }
}
