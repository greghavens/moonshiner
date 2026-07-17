import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Objects;
import java.util.TimeZone;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

/**
 * Acceptance harness: loopback fake Kubernetes API server implementing the
 * core v1 pod-log subresource contract pinned in docs/contract.json.
 * Re-checks the existing PodClient behavior and exercises the new
 * PodLogCollector feature. Protected — do not modify. Run: java TestMain.java
 */
public class TestMain {

    static final String TOKEN = "dummy-sa-token-77e0c3"; // dummy; must never leak
    static final String EXPECTED_AUTH = "Bearer " + TOKEN;
    static final String NAMESPACE = "logs-ns";
    static final String PODS_PATH = "/api/v1/namespaces/" + NAMESPACE + "/pods";

    static int checks = 0;

    static void check(boolean cond, String msg) {
        if (!cond) throw new AssertionError(msg);
        checks++;
    }

    static void checkEq(Object got, Object want, String msg) {
        check(Objects.equals(got, want), msg + " — got <" + got + ">, want <" + want + ">");
    }

    // ---------------------------------------------------------------- fake

    static String jsonEscape(String s) {
        StringBuilder sb = new StringBuilder();
        for (char c : s.toCharArray()) {
            switch (c) {
                case '"': sb.append("\\\""); break;
                case '\\': sb.append("\\\\"); break;
                case '\n': sb.append("\\n"); break;
                case '\r': sb.append("\\r"); break;
                case '\t': sb.append("\\t"); break;
                default: sb.append(c);
            }
        }
        return sb.toString();
    }

    static String statusJson(int code, String reason, String message) {
        return "{\"kind\":\"Status\",\"apiVersion\":\"v1\",\"status\":\"Failure\","
                + "\"message\":\"" + jsonEscape(message) + "\","
                + "\"reason\":\"" + jsonEscape(reason) + "\",\"code\":" + code + "}";
    }

    static final class Recorded {
        final String method;
        final String path;
        final String rawQuery;
        final Map<String, String> headers;
        Recorded(String method, String path, String rawQuery, Map<String, String> headers) {
            this.method = method;
            this.path = path;
            this.rawQuery = rawQuery;
            this.headers = headers;
        }
    }

    static final class LogScript {
        int status = 200;
        String statusBody = "";
        final List<byte[]> chunks = new ArrayList<>();
        CountDownLatch hold; // if set, stream stays open until released

        LogScript lines(String text) {
            chunks.add(text.getBytes(StandardCharsets.UTF_8));
            return this;
        }
    }

    static final class FakeKube {
        final Map<String, LogScript> logs = new HashMap<>();
        final List<Recorded> requests = new ArrayList<>();
        String podsListBody = "{\"kind\":\"PodList\",\"apiVersion\":\"v1\",\"items\":[]}";
        int podsListStatus = 200;
        String redirectTo; // one-shot 302 target
        HttpServer server;
        String baseUrl;

        void start() throws IOException {
            server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
            server.createContext("/", this::handle);
            server.start();
            baseUrl = "http://127.0.0.1:" + server.getAddress().getPort();
        }

        void stop() {
            server.stop(0);
        }

        synchronized List<Recorded> recorded() {
            return new ArrayList<>(requests);
        }

        void handle(HttpExchange ex) throws IOException {
            Map<String, String> headers = new LinkedHashMap<>();
            ex.getRequestHeaders().forEach((k, v) ->
                    headers.put(k.toLowerCase(Locale.ROOT), v.isEmpty() ? "" : v.get(0)));
            String rawQuery = ex.getRequestURI().getRawQuery();
            synchronized (this) {
                requests.add(new Recorded(ex.getRequestMethod(), ex.getRequestURI().getPath(),
                        rawQuery == null ? "" : rawQuery, headers));
            }
            ex.getRequestBody().readAllBytes();

            if (!EXPECTED_AUTH.equals(headers.get("authorization"))) {
                send(ex, 401, statusJson(401, "Unauthorized", "credentials required"));
                return;
            }
            String target;
            synchronized (this) {
                target = redirectTo;
                redirectTo = null;
            }
            if (target != null) {
                ex.getResponseHeaders().set("Location", target);
                ex.sendResponseHeaders(302, -1);
                ex.close();
                return;
            }

            String path = ex.getRequestURI().getPath();
            if (path.equals(PODS_PATH)) {
                send(ex, podsListStatus, podsListStatus == 200 ? podsListBody
                        : statusJson(podsListStatus, "Forbidden",
                        "pods is forbidden: User \"system:serviceaccount:obs:logcat\" "
                                + "cannot list resource \"pods\""));
                return;
            }
            if (path.startsWith(PODS_PATH + "/") && path.endsWith("/log")) {
                String pod = path.substring(PODS_PATH.length() + 1,
                        path.length() - "/log".length());
                LogScript script = logs.get(pod);
                if (script == null) {
                    send(ex, 404, statusJson(404, "NotFound",
                            "pods \"" + pod + "\" not found"));
                    return;
                }
                if (script.status != 200) {
                    send(ex, script.status, script.statusBody);
                    return;
                }
                ex.getResponseHeaders().set("Content-Type", "text/plain");
                ex.sendResponseHeaders(200, 0); // chunked
                try (OutputStream os = ex.getResponseBody()) {
                    for (byte[] chunk : script.chunks) {
                        os.write(chunk);
                        os.flush();
                    }
                    if (script.hold != null) {
                        script.hold.await(10, TimeUnit.SECONDS);
                    }
                } catch (IOException | InterruptedException ignored) {
                    // client cancelled mid-stream — expected in the follow test
                }
                return;
            }
            send(ex, 400, statusJson(400, "BadRequest",
                    "unsupported " + ex.getRequestMethod() + " " + path));
        }

        void send(HttpExchange ex, int status, String body) throws IOException {
            byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
            ex.getResponseHeaders().set("Content-Type", "application/json");
            ex.sendResponseHeaders(status, bytes.length);
            try (OutputStream os = ex.getResponseBody()) {
                os.write(bytes);
            }
        }
    }

    static final class EvilHost {
        final List<Map<String, String>> hits = new ArrayList<>();
        HttpServer server;
        String baseUrl;

        void start() throws IOException {
            server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
            server.createContext("/", ex -> {
                Map<String, String> headers = new LinkedHashMap<>();
                ex.getRequestHeaders().forEach((k, v) ->
                        headers.put(k.toLowerCase(Locale.ROOT), v.isEmpty() ? "" : v.get(0)));
                synchronized (hits) {
                    hits.add(headers);
                }
                ex.sendResponseHeaders(200, 2);
                try (OutputStream os = ex.getResponseBody()) {
                    os.write("{}".getBytes(StandardCharsets.UTF_8));
                }
            });
            server.start();
            baseUrl = "http://127.0.0.1:" + server.getAddress().getPort();
        }

        void stop() {
            server.stop(0);
        }
    }

    static String podList(String... names) {
        StringBuilder sb = new StringBuilder(
                "{\"kind\":\"PodList\",\"apiVersion\":\"v1\",\"items\":[");
        for (int i = 0; i < names.length; i++) {
            if (i > 0) sb.append(',');
            sb.append("{\"metadata\":{\"name\":\"").append(names[i])
              .append("\",\"namespace\":\"").append(NAMESPACE).append("\"}}");
        }
        return sb.append("]}").toString();
    }

    static int indexOf(byte[] haystack, byte needle) {
        for (int i = 0; i < haystack.length; i++) {
            if (haystack[i] == needle) return i;
        }
        return -1;
    }

    // ---------------------------------------------------------------- tests

    static void testExistingListPods() throws Exception {
        FakeKube kube = new FakeKube();
        kube.podsListBody = podList("ingest-0", "ingest-1");
        kube.start();
        try {
            PodClient client = new PodClient(kube.baseUrl, TOKEN);
            List<String> names = client.listPodNames(NAMESPACE, "app=ingest");
            checkEq(names, List.of("ingest-0", "ingest-1"), "pod names decode in order");
            Recorded r = kube.recorded().get(0);
            checkEq(r.method, "GET", "pods list method");
            checkEq(r.path, PODS_PATH, "pods list path");
            checkEq(r.rawQuery, "labelSelector=app%3Dingest",
                    "labelSelector '=' must be percent-encoded on the wire");
            checkEq(r.headers.get("authorization"), EXPECTED_AUTH, "bearer token sent");
            checkEq(r.headers.get("accept"), "application/json", "Accept header sent");
        } finally {
            kube.stop();
        }
    }

    static void testExistingListStatusError() throws Exception {
        FakeKube kube = new FakeKube();
        kube.podsListStatus = 403;
        kube.start();
        try {
            PodClient client = new PodClient(kube.baseUrl, TOKEN);
            KubeStatusException thrown = null;
            try {
                client.listPodNames(NAMESPACE, "app=ingest");
            } catch (KubeStatusException e) {
                thrown = e;
            }
            check(thrown != null, "a 403 Status response must raise KubeStatusException");
            checkEq(thrown.code, 403, "code from the Status body");
            checkEq(thrown.reason, "Forbidden", "reason from the Status body");
            check(thrown.statusMessage.contains("cannot list"), "message from the Status body");
            check(!thrown.getMessage().contains(TOKEN), "credentials never appear in errors");
        } finally {
            kube.stop();
        }
    }

    static void testExistingRedirectRefusal() throws Exception {
        FakeKube kube = new FakeKube();
        EvilHost evil = new EvilHost();
        kube.start();
        evil.start();
        try {
            kube.redirectTo = evil.baseUrl + PODS_PATH;
            PodClient client = new PodClient(kube.baseUrl, TOKEN);
            KubeStatusException thrown = null;
            try {
                client.listPodNames(NAMESPACE, "app=ingest");
            } catch (KubeStatusException e) {
                thrown = e;
            }
            check(thrown != null, "a redirect must not be silently followed");
            checkEq(thrown.code, 302, "the redirect status is surfaced");
            checkEq(thrown.reason, "Redirect", "redirects surface as reason Redirect");
            checkEq(evil.hits.size(), 0, "the bearer token must never reach another origin");
            check(!thrown.getMessage().contains(TOKEN), "credentials never appear in errors");
        } finally {
            kube.stop();
            evil.stop();
        }
    }

    static void testFetchSplitsChunkedUtf8Lines() throws Exception {
        FakeKube kube = new FakeKube();
        String content = "Starting über-worker ⚡ ready\nmétrique flushed → ok\ndone";
        byte[] all = content.getBytes(StandardCharsets.UTF_8);
        int cut1 = indexOf(all, (byte) 0xC3) + 1; // mid 'ü' (2-byte sequence)
        int cut2 = indexOf(all, (byte) 0xE2) + 2; // mid '⚡' (3-byte sequence)
        LogScript script = new LogScript();
        script.chunks.add(java.util.Arrays.copyOfRange(all, 0, cut1));
        script.chunks.add(java.util.Arrays.copyOfRange(all, cut1, cut2));
        script.chunks.add(java.util.Arrays.copyOfRange(all, cut2, all.length));
        kube.logs.put("ingest-0", script);
        kube.start();
        try {
            PodLogCollector collector = new PodLogCollector(new PodClient(kube.baseUrl, TOKEN));
            PodLogCollector.Options o = new PodLogCollector.Options();
            o.container = "ingest";
            o.tailLines = 50;
            List<String> lines = collector.fetch(NAMESPACE, "ingest-0", o);
            checkEq(lines, List.of("Starting über-worker ⚡ ready",
                            "métrique flushed → ok", "done"),
                    "multi-byte UTF-8 split across chunk reads must reassemble exactly; "
                            + "a final line without a newline still counts");
            Recorded r = kube.recorded().get(0);
            checkEq(r.path, PODS_PATH + "/ingest-0/log", "core v1 log subresource path");
            checkEq(r.rawQuery, "container=ingest&tailLines=50",
                    "params in alphabetical order; timestamps/follow omitted when off");
            checkEq(r.headers.get("authorization"), EXPECTED_AUTH, "bearer token sent");
        } finally {
            kube.stop();
        }
    }

    static void testFetchTimestampsParam() throws Exception {
        FakeKube kube = new FakeKube();
        kube.logs.put("ingest-0", new LogScript().lines("2026-07-16T08:00:01Z tick\n"));
        kube.start();
        try {
            PodLogCollector collector = new PodLogCollector(new PodClient(kube.baseUrl, TOKEN));
            PodLogCollector.Options o = new PodLogCollector.Options();
            o.container = "ingest";
            o.tailLines = 10;
            o.timestamps = true;
            List<String> lines = collector.fetch(NAMESPACE, "ingest-0", o);
            checkEq(lines.size(), 1, "one line fetched");
            checkEq(kube.recorded().get(0).rawQuery, "container=ingest&tailLines=10&timestamps=true",
                    "timestamps=true joins the query in alphabetical position");
        } finally {
            kube.stop();
        }
    }

    static void testLogStatusErrors() throws Exception {
        FakeKube kube = new FakeKube();
        LogScript bad = new LogScript();
        bad.status = 400;
        bad.statusBody = statusJson(400, "BadRequest",
                "container fluentd is not valid for pod ingest-0");
        kube.logs.put("ingest-0", bad);
        kube.start();
        try {
            PodLogCollector collector = new PodLogCollector(new PodClient(kube.baseUrl, TOKEN));
            PodLogCollector.Options o = new PodLogCollector.Options();

            KubeStatusException notFound = null;
            try {
                collector.fetch(NAMESPACE, "ghost", o);
            } catch (KubeStatusException e) {
                notFound = e;
            }
            check(notFound != null, "a missing pod's log must raise KubeStatusException");
            checkEq(notFound.code, 404, "404 decoded from the Status body");
            checkEq(notFound.reason, "NotFound", "reason decoded");
            check(notFound.statusMessage.contains("ghost"), "message names the pod");

            KubeStatusException badContainer = null;
            try {
                PodLogCollector.Options bo = new PodLogCollector.Options();
                bo.container = "fluentd";
                collector.fetch(NAMESPACE, "ingest-0", bo);
            } catch (KubeStatusException e) {
                badContainer = e;
            }
            check(badContainer != null, "an invalid container must raise KubeStatusException");
            checkEq(badContainer.code, 400, "400 decoded from the Status body");
            checkEq(badContainer.reason, "BadRequest", "reason decoded");
            check(badContainer.statusMessage.contains("fluentd"), "message names the container");
            check(!badContainer.getMessage().contains(TOKEN),
                    "credentials never appear in errors");
        } finally {
            kube.stop();
        }
    }

    static void testFollowCancellation() throws Exception {
        FakeKube kube = new FakeKube();
        LogScript script = new LogScript();
        for (int i = 1; i <= 5; i++) {
            script.lines("event " + i + "\n");
        }
        script.hold = new CountDownLatch(1);
        kube.logs.put("ingest-0", script);
        kube.start();
        try {
            PodLogCollector collector = new PodLogCollector(new PodClient(kube.baseUrl, TOKEN));
            PodLogCollector.Options o = new PodLogCollector.Options();
            o.container = "ingest";
            o.follow = true;
            List<String> seen = new ArrayList<>();
            int delivered = collector.follow(NAMESPACE, "ingest-0", o, line -> {
                seen.add(line);
                return seen.size() < 3; // cancel after the third line
            });
            checkEq(delivered, 3,
                    "follow() returns as soon as the callback cancels, mid-open-stream");
            checkEq(seen, List.of("event 1", "event 2", "event 3"),
                    "exactly the lines delivered before cancellation");
            checkEq(kube.recorded().get(0).rawQuery, "container=ingest&follow=true",
                    "follow=true joins the query in alphabetical position");
        } finally {
            if (kube.logs.get("ingest-0").hold != null) {
                kube.logs.get("ingest-0").hold.countDown();
            }
            kube.stop();
        }
    }

    static void testCollectMergesDeterministically() throws Exception {
        FakeKube kube = new FakeKube();
        kube.logs.put("web-0", new LogScript()
                .lines("2026-07-16T08:00:01Z ingest tick A\n")
                .lines("2026-07-16T08:00:02.5Z ingest tick C\n"));
        kube.logs.put("web-1", new LogScript()
                .lines("2026-07-16T08:00:01.500Z ingest tick B\n")
                .lines("2026-07-16T08:00:02.5Z ingest tick D\n"));
        kube.logs.put("web-2", new LogScript()); // empty log
        kube.start();
        try {
            PodLogCollector collector = new PodLogCollector(new PodClient(kube.baseUrl, TOKEN));
            PodLogCollector.Options o = new PodLogCollector.Options();
            o.container = "ingest"; // note: o.timestamps is FALSE — collect must force it
            List<PodLogCollector.MergedLine> merged =
                    collector.collect(NAMESPACE, List.of("web-0", "web-1", "web-2"), o);

            checkEq(merged.size(), 4, "empty pods contribute nothing");
            List<String> messages = new ArrayList<>();
            List<String> pods = new ArrayList<>();
            for (PodLogCollector.MergedLine m : merged) {
                messages.add(m.message);
                pods.add(m.pod);
            }
            checkEq(messages, List.of("ingest tick A", "ingest tick B",
                            "ingest tick C", "ingest tick D"),
                    "merge orders by PARSED timestamp — '08:00:01Z' sorts before "
                            + "'08:00:01.500Z' even though a raw string compare disagrees");
            checkEq(pods, List.of("web-0", "web-1", "web-0", "web-1"),
                    "equal timestamps break ties by pod name ascending");
            checkEq(merged.get(0).timestamp, "2026-07-16T08:00:01Z",
                    "the original timestamp token is preserved");
            checkEq(merged.get(0).pod, "web-0", "each line remembers its pod");

            List<Recorded> reqs = kube.recorded();
            checkEq(reqs.size(), 3, "one log request per selected pod");
            List<String> paths = new ArrayList<>();
            for (Recorded r : reqs) {
                paths.add(r.path);
                checkEq(r.rawQuery, "container=ingest&timestamps=true",
                        "collect must force timestamps=true so lines can be merged");
            }
            checkEq(paths, List.of(PODS_PATH + "/web-0/log", PODS_PATH + "/web-1/log",
                    PODS_PATH + "/web-2/log"), "pods fetched in the order given");
        } finally {
            kube.stop();
        }
    }

    @SuppressWarnings("unchecked")
    static void testProtectedDocsFixtures() throws Exception {
        Map<String, Object> contract = Json.asObject(Json.parse(
                Files.readString(Path.of("docs", "contract.json"))));
        Map<String, Object> sources = Json.asObject(Json.parse(
                Files.readString(Path.of("docs", "official_sources.json"))));
        Map<String, Object> research = Json.asObject(sources.get("research"));
        checkEq(research.get("required"), Boolean.TRUE, "research provenance is mandatory");
        List<Object> officialSources = Json.asArray(research.get("official_sources"));
        check(officialSources.size() >= 2, "at least two official sources required");
        for (Object o : officialSources) {
            Map<String, Object> src = Json.asObject(o);
            String url = String.valueOf(src.get("url"));
            boolean firstParty = url.contains("kubernetes.io")
                    || url.contains("github.com/kubernetes/kubernetes")
                    || url.contains("githubusercontent.com/kubernetes/kubernetes");
            check(url.startsWith("https://") && firstParty,
                    "official source must be first-party Kubernetes: " + url);
            check(!String.valueOf(src.get("used_for")).isEmpty(), "used_for must be recorded");
        }
        check(Json.asArray(sources.get("verified_facts")).size() >= 4,
                "verified facts must be summarized");
        Map<String, Object> log = Json.asObject(contract.get("log_subresource"));
        checkEq(log.get("path"), "/api/v1/namespaces/{namespace}/pods/{name}/log",
                "contract pins the core v1 log subresource path");
        List<Object> params = Json.asArray(log.get("params"));
        String joined = String.valueOf(params);
        check(joined.contains("container") && joined.contains("follow")
                        && joined.contains("tailLines") && joined.contains("timestamps"),
                "contract pins the researched query parameters");
        Map<String, Object> merge = Json.asObject(contract.get("merge_policy"));
        check(String.valueOf(merge.get("order")).contains("parsed"),
                "contract pins parsed-timestamp ordering");
    }

    public static void main(String[] args) throws Exception {
        Locale.setDefault(Locale.ROOT);
        TimeZone.setDefault(TimeZone.getTimeZone("UTC"));
        testProtectedDocsFixtures();
        System.out.println("ok  testProtectedDocsFixtures");
        testExistingListPods();
        System.out.println("ok  testExistingListPods");
        testExistingListStatusError();
        System.out.println("ok  testExistingListStatusError");
        testExistingRedirectRefusal();
        System.out.println("ok  testExistingRedirectRefusal");
        testFetchSplitsChunkedUtf8Lines();
        System.out.println("ok  testFetchSplitsChunkedUtf8Lines");
        testFetchTimestampsParam();
        System.out.println("ok  testFetchTimestampsParam");
        testLogStatusErrors();
        System.out.println("ok  testLogStatusErrors");
        testFollowCancellation();
        System.out.println("ok  testFollowCancellation");
        testCollectMergesDeterministically();
        System.out.println("ok  testCollectMergesDeterministically");
        System.out.println("PASS  " + checks + " checks");
    }
}
