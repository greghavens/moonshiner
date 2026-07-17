import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Base64;
import java.util.Collections;
import java.util.Deque;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.TimeZone;

/**
 * Acceptance tests for the ONTAP identity/capability probe (whoami feature).
 * Loopback mock cluster speaking the pinned contract in docs/contract.json.
 * No network, dummy credentials only. Protected file — do not modify.
 */
public class TestMain {

    static int passed = 0;
    static int failed = 0;

    static void check(boolean condition, String label) {
        if (condition) {
            passed++;
        } else {
            failed++;
            System.out.println("FAIL: " + label);
        }
    }

    record Recorded(String method, String raw, String auth) {}

    record Canned(int status, String body) {}

    static final List<Recorded> LOG = Collections.synchronizedList(new ArrayList<>());
    static final Map<String, Deque<Canned>> ROUTES = new HashMap<>();

    static void reset() {
        LOG.clear();
        ROUTES.clear();
    }

    static void route(String key, Canned... responses) {
        ROUTES.put(key, new ArrayDeque<>(Arrays.asList(responses)));
    }

    static final String USER = "probe-svc";
    static final String PASS = "dummy-pass-999";
    static final String AUTH = "Basic "
            + Base64.getEncoder().encodeToString((USER + ":" + PASS).getBytes(StandardCharsets.UTF_8));

    static final String WHOAMI_KEY = "GET /api/security/login/whoami";
    static final String CLUSTER_KEY = "GET /api/cluster";
    static final String WHOAMI_RAW = "/api/security/login/whoami?fields=username,roles,privileges";
    static final String CLUSTER_RAW = "/api/cluster?fields=name,version";

    static final String WHOAMI_OK =
            "{\"username\":\"probe-svc\",\"roles\":[\"ontap-auditor\"],"
            + "\"privileges\":[{\"path\":\"/api/cluster\",\"access\":\"readonly\"},"
            + "{\"path\":\"/api/security/login/whoami\",\"access\":\"readonly\"}],"
            + "\"_links\":{\"self\":{\"href\":\"/api/security/login/whoami\"}}}";
    static final String WHOAMI_ADMIN =
            "{\"username\":\"admin\",\"roles\":[],"
            + "\"privileges\":[{\"path\":\"/api\",\"access\":\"all\"}]}";
    static final String CLUSTER_OK =
            "{\"name\":\"lab-cluster-01\","
            + "\"version\":{\"full\":\"NetApp Release 9.16.1: Fri Jan 24 08:00:00 UTC 2025\","
            + "\"generation\":9,\"major\":16,\"minor\":1}}";

    static String err(String code, String message) {
        return "{\"error\":{\"code\":\"" + code + "\",\"message\":\"" + message + "\"}}";
    }

    static void handle(HttpExchange exchange) throws java.io.IOException {
        String method = exchange.getRequestMethod();
        LOG.add(new Recorded(method, exchange.getRequestURI().toString(),
                exchange.getRequestHeaders().getFirst("Authorization")));
        String key = method + " " + exchange.getRequestURI().getPath();
        Deque<Canned> queue = ROUTES.get(key);
        Canned canned = (queue == null || queue.isEmpty())
                ? new Canned(599, err("0", "UNEXPECTED " + key))
                : queue.poll();
        byte[] payload = canned.body().getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().set("Content-Type", "application/hal+json");
        exchange.sendResponseHeaders(canned.status(), payload.length);
        try (OutputStream out = exchange.getResponseBody()) {
            out.write(payload);
        }
    }

    public static void main(String[] args) throws Exception {
        Locale.setDefault(Locale.ROOT);
        TimeZone.setDefault(TimeZone.getTimeZone("UTC"));
        HttpServer server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/", TestMain::handle);
        server.start();
        String base = "http://127.0.0.1:" + server.getAddress().getPort();
        try {
            OntapRestClient client = OntapRestClient.builder(base).credentials(USER, PASS).build();
            scenarioClusterRegression(client);
            scenarioClusterError(client);
            scenarioWhoamiSupported(client);
            scenarioWhoamiAdminShape(client);
            scenarioFallbackOlderCluster(client);
            scenarioPermissionDenied(client);
            scenarioAuthFailure(client);
            scenarioFallbackAlsoMissing(client);
            scenarioTlsGuard(base);
        } finally {
            server.stop(0);
        }
        System.out.println("passed=" + passed + " failed=" + failed);
        if (failed > 0) {
            System.exit(1);
        }
    }

    static void scenarioClusterRegression(OntapRestClient client) throws Exception {
        reset();
        route(CLUSTER_KEY, new Canned(200, CLUSTER_OK));
        ClusterIdentity identity = ClusterProbe.probe(client);
        check("lab-cluster-01".equals(identity.name), "cluster probe reads the cluster name");
        check(identity.versionFull != null && identity.versionFull.contains("9.16.1"),
                "cluster probe reads version.full");
        check(identity.generation == 9, "cluster probe reads version.generation");
        check(identity.major == 16, "cluster probe reads version.major");
        check(identity.minor == 1, "cluster probe reads version.minor");
        check(LOG.size() == 1, "cluster probe issues exactly one request");
        check(WHOAMI_RAW != null && LOG.get(0).raw().equals(CLUSTER_RAW),
                "cluster probe requests explicit fields=name,version");
        check(AUTH.equals(LOG.get(0).auth()), "cluster probe sends basic auth");
    }

    static void scenarioClusterError(OntapRestClient client) {
        reset();
        route(CLUSTER_KEY, new Canned(401, err("5", "authentication failed")));
        OntapApiException thrown = null;
        try {
            ClusterProbe.probe(client);
        } catch (OntapApiException e) {
            thrown = e;
        } catch (Exception e) {
            // wrong exception type; leave thrown null
        }
        check(thrown != null, "non-200 cluster probe raises OntapApiException");
        check(thrown != null && thrown.status == 401, "cluster error keeps the HTTP status");
        check(thrown != null && "5".equals(thrown.code), "cluster error decodes the ONTAP code");
        check(thrown != null && thrown.getMessage().contains("authentication failed")
                        && !thrown.getMessage().contains(PASS),
                "cluster error message has the ONTAP text and never the password");
    }

    static void scenarioWhoamiSupported(OntapRestClient client) throws Exception {
        reset();
        route(WHOAMI_KEY, new Canned(200, WHOAMI_OK));
        IdentityReport report = new IdentityProbe(client).probe();
        check(report.whoamiSupported, "modern cluster reports whoamiSupported=true");
        check("probe-svc".equals(report.username), "whoami username decoded");
        check(List.of("ontap-auditor").equals(report.roles), "whoami roles decoded in order");
        check(report.privileges != null && report.privileges.size() == 2, "both privileges decoded");
        check(report.privileges != null && report.privileges.size() == 2
                        && "/api/cluster".equals(report.privileges.get(0).path)
                        && "readonly".equals(report.privileges.get(0).access),
                "first privilege path/access decoded");
        check(report.privileges != null && report.privileges.size() == 2
                        && "/api/security/login/whoami".equals(report.privileges.get(1).path),
                "privilege order preserved");
        check(report.clusterName == null, "no cluster fallback data when whoami works");
        check(report.versionFull == null, "no fallback version string when whoami works");
        check(report.generation == null && report.major == null && report.minor == null,
                "no fallback version numbers when whoami works");
        check(LOG.size() == 1, "supported path issues exactly one request");
        check(LOG.get(0).raw().equals(WHOAMI_RAW),
                "whoami requested with explicit fields=username,roles,privileges");
        check(AUTH.equals(LOG.get(0).auth()), "whoami request sends basic auth");
    }

    static void scenarioWhoamiAdminShape(OntapRestClient client) throws Exception {
        reset();
        route(WHOAMI_KEY, new Canned(200, WHOAMI_ADMIN));
        IdentityReport report = new IdentityProbe(client).probe();
        check(report.roles != null && report.roles.isEmpty(), "empty roles array decodes to empty list");
        check(report.privileges != null && report.privileges.size() == 1
                        && "/api".equals(report.privileges.get(0).path),
                "single privilege decoded");
        check(report.privileges != null && report.privileges.size() == 1
                        && "all".equals(report.privileges.get(0).access),
                "access level 'all' preserved");
    }

    static void scenarioFallbackOlderCluster(OntapRestClient client) throws Exception {
        reset();
        route(WHOAMI_KEY, new Canned(404,
                err("3", "The requested resource /api/security/login/whoami does not exist")));
        route(CLUSTER_KEY, new Canned(200, CLUSTER_OK));
        IdentityReport report = new IdentityProbe(client).probe();
        check(!report.whoamiSupported, "404 whoami means whoamiSupported=false, not an error");
        check("probe-svc".equals(report.username), "fallback username comes from the client login");
        check(report.roles != null && report.roles.isEmpty(), "fallback roles are empty, not null");
        check(report.privileges != null && report.privileges.isEmpty(), "fallback privileges are empty, not null");
        check("lab-cluster-01".equals(report.clusterName), "fallback reads the cluster name");
        check(report.versionFull != null && report.versionFull.contains("9.16.1"),
                "fallback reads version.full");
        check(report.generation != null && report.generation == 9, "fallback reads version.generation");
        check(report.major != null && report.major == 16, "fallback reads version.major");
        check(report.minor != null && report.minor == 1, "fallback reads version.minor");
        check(LOG.size() == 2, "fallback issues exactly two requests");
        check(LOG.get(0).raw().equals(WHOAMI_RAW), "whoami attempted first");
        check(LOG.get(1).raw().equals(CLUSTER_RAW), "fallback requests explicit fields=name,version");
    }

    static void scenarioPermissionDenied(OntapRestClient client) {
        reset();
        route(WHOAMI_KEY, new Canned(403, err("6", "not authorized for that command")));
        OntapPermissionException thrown = null;
        try {
            new IdentityProbe(client).probe();
        } catch (OntapPermissionException e) {
            thrown = e;
        } catch (Exception e) {
            // wrong exception type; leave thrown null
        }
        check(thrown != null, "403 raises OntapPermissionException, never a silent fallback");
        check(thrown instanceof OntapApiException, "permission exception is an OntapApiException");
        check(thrown != null && thrown.status == 403, "permission exception keeps status 403");
        check(thrown != null && "6".equals(thrown.code), "permission exception decodes code 6");
        check(LOG.size() == 1, "permission denial must NOT trigger the older-cluster fallback");
        check(thrown != null && !thrown.getMessage().contains(PASS),
                "permission error never contains the password");
    }

    static void scenarioAuthFailure(OntapRestClient client) {
        reset();
        route(WHOAMI_KEY, new Canned(401, err("5", "authentication failed")));
        Exception thrown = null;
        try {
            new IdentityProbe(client).probe();
        } catch (Exception e) {
            thrown = e;
        }
        check(thrown instanceof OntapApiException, "401 raises OntapApiException");
        check(!(thrown instanceof OntapPermissionException),
                "401 is an auth failure, not a permission distinction");
        check(thrown instanceof OntapApiException && ((OntapApiException) thrown).status == 401,
                "auth failure keeps status 401");
        check(LOG.size() == 1, "auth failure does not fall back");
    }

    static void scenarioFallbackAlsoMissing(OntapRestClient client) {
        reset();
        route(WHOAMI_KEY, new Canned(404, err("3", "The requested resource does not exist")));
        route(CLUSTER_KEY, new Canned(404, err("3", "The requested resource does not exist")));
        Exception thrown = null;
        try {
            new IdentityProbe(client).probe();
        } catch (Exception e) {
            thrown = e;
        }
        check(thrown instanceof OntapApiException, "failing fallback surfaces OntapApiException");
        check(thrown instanceof OntapApiException && ((OntapApiException) thrown).status == 404,
                "failing fallback keeps its HTTP status");
        check(LOG.size() == 2, "fallback attempted exactly once");
    }

    static void scenarioTlsGuard(String base) {
        UnsupportedOperationException thrown = null;
        try {
            OntapRestClient.builder(base).insecureTls();
        } catch (UnsupportedOperationException e) {
            thrown = e;
        }
        check(thrown != null, "insecureTls() must refuse to disable TLS validation");
        check(thrown != null && thrown.getMessage() != null
                        && thrown.getMessage().toLowerCase(Locale.ROOT).contains("tls"),
                "refusal names TLS so callers get a clear answer");
        OntapRestClient normal = OntapRestClient.builder(base).credentials(USER, PASS).build();
        check(normal != null && USER.equals(normal.username()), "normal builder path still works");
    }
}
