import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;

/**
 * Acceptance harness: loopback fake Databricks workspace (Cluster Policies
 * API 2.0 + Policy Families API 2.0 subset, contract pinned in
 * docs/contract.json) plus checks for the existing PoliciesClient behavior
 * and the new PolicyReconciler feature. Run with: java TestMain.java
 * Protected — do not modify this file or anything under docs/.
 */
public class TestMain {

    static final String TOKEN = "dapi3fixture51e8ba07c92d64afdummy"; // dummy; must never leak
    static final String EXPECTED_AUTH = "Bearer " + TOKEN;
    static final int MAX_RETRIES = 2; // synced with docs/contract.json rate_limit.max_retries

    static int checks = 0;

    static void check(boolean cond, String msg) {
        if (!cond) throw new AssertionError(msg);
        checks++;
    }

    static void checkEq(Object got, Object want, String msg) {
        check(Objects.equals(got, want), msg + " — got <" + got + ">, want <" + want + ">");
    }

    // ---------------------------------------------------------------- fake

    static final class Recorded {
        final String method;
        final String path;
        final String query;
        final Map<String, String> headers;
        final String body;

        Recorded(String method, String path, String query, Map<String, String> headers, String body) {
            this.method = method;
            this.path = path;
            this.query = query;
            this.headers = headers;
            this.body = body;
        }
    }

    static final class Fault {
        final int status;
        final String body;
        final String retryAfter;

        Fault(int status, String body, String retryAfter) {
            this.status = status;
            this.body = body;
            this.retryAfter = retryAfter;
        }
    }

    static final class FakeWorkspace {
        final List<Recorded> requests = new ArrayList<>();
        final List<Map<String, Object>> policies = new ArrayList<>();
        final Map<String, List<Fault>> failOnce = new LinkedHashMap<>();
        final Map<String, Fault> alwaysFail = new LinkedHashMap<>();
        int nextId = 100;
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

        Map<String, Object> findById(String policyId) {
            for (Map<String, Object> p : policies) {
                if (policyId.equals(p.get("policy_id"))) return p;
            }
            return null;
        }

        List<Recorded> writes() {
            List<Recorded> out = new ArrayList<>();
            for (Recorded r : requests) {
                if ("POST".equals(r.method)) out.add(r);
            }
            return out;
        }

        void handle(HttpExchange ex) throws IOException {
            String path = ex.getRequestURI().getPath();
            String query = ex.getRequestURI().getQuery();
            Map<String, String> headers = new LinkedHashMap<>();
            ex.getRequestHeaders().forEach((k, v) -> headers.put(k.toLowerCase(java.util.Locale.ROOT), v.get(0)));
            String body = new String(ex.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
            synchronized (this) {
                requests.add(new Recorded(ex.getRequestMethod(), path, query, headers, body));
            }
            Fault fault = alwaysFail.get(path);
            if (fault == null) {
                List<Fault> queue = failOnce.get(path);
                if (queue != null && !queue.isEmpty()) fault = queue.remove(0);
            }
            if (fault != null) {
                if (fault.retryAfter != null) ex.getResponseHeaders().set("Retry-After", fault.retryAfter);
                respond(ex, fault.status, fault.body);
                return;
            }
            if (!EXPECTED_AUTH.equals(headers.get("authorization"))) {
                respond(ex, 401, envelope("UNAUTHENTICATED", "Unable to authenticate the request"));
                return;
            }
            switch (ex.getRequestMethod() + " " + path) {
                case "GET /api/2.0/policies/clusters/list" ->
                        respond(ex, 200, Json.write(Map.of("policies", policies)));
                case "GET /api/2.0/policies/clusters/get" -> {
                    String id = query == null ? "" : query.replace("policy_id=", "");
                    Map<String, Object> p = findById(id);
                    if (p == null) respond(ex, 400, envelope("INVALID_PARAMETER_VALUE", "Policy " + id + " does not exist."));
                    else respond(ex, 200, Json.write(p));
                }
                case "POST /api/2.0/policies/clusters/create" -> {
                    Map<String, Object> fields = Json.parseObject(body);
                    Map<String, Object> stored = new LinkedHashMap<>(fields);
                    String id = "pol-" + (nextId++);
                    stored.put("policy_id", id);
                    policies.add(stored);
                    respond(ex, 200, Json.write(Map.of("policy_id", id)));
                }
                case "POST /api/2.0/policies/clusters/edit" -> {
                    Map<String, Object> fields = Json.parseObject(body);
                    Map<String, Object> existing = findById((String) fields.get("policy_id"));
                    if (existing == null) {
                        respond(ex, 400, envelope("INVALID_PARAMETER_VALUE", "Policy does not exist."));
                        return;
                    }
                    existing.clear();
                    existing.putAll(fields);
                    respond(ex, 200, "{}");
                }
                case "GET /api/2.0/policy-families" -> {
                    String q = query == null ? "" : query;
                    if (q.contains("page_token=pf-2")) {
                        respond(ex, 200, Json.write(Map.of("policy_families", List.of(
                                family("shared-compute", "Shared Compute", 3)))));
                    } else {
                        respond(ex, 200, Json.write(Map.of(
                                "policy_families", List.of(
                                        family("job-cluster", "Job Compute", 2),
                                        family("personal-vm", "Personal Compute", 5)),
                                "next_page_token", "pf-2")));
                    }
                }
                default -> respond(ex, 404, envelope("ENDPOINT_NOT_FOUND",
                        "No API found for " + ex.getRequestMethod() + " " + path));
            }
        }

        static void respond(HttpExchange ex, int status, String body) throws IOException {
            byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
            ex.getResponseHeaders().set("Content-Type", "application/json");
            ex.sendResponseHeaders(status, bytes.length);
            try (OutputStream os = ex.getResponseBody()) {
                os.write(bytes);
            }
        }
    }

    static String envelope(String code, String message) {
        return Json.write(Map.of("error_code", code, "message", message));
    }

    static Map<String, Object> family(String id, String name, int version) {
        Map<String, Object> f = new LinkedHashMap<>();
        f.put("policy_family_id", id);
        f.put("name", name);
        f.put("description", name + " policy family");
        f.put("definition", "{\"node_type_id\":{\"type\":\"unlimited\"}}");
        f.put("version", (double) version);
        return f;
    }

    static Map<String, Object> policy(String id, String name, Object... kv) {
        Map<String, Object> p = new LinkedHashMap<>();
        p.put("policy_id", id);
        p.put("name", name);
        p.put("created_at_timestamp", 1720000000000.0);
        p.put("creator_user_name", "infra-bot@example.com");
        for (int i = 0; i < kv.length; i += 2) {
            p.put((String) kv[i], kv[i + 1]);
        }
        return p;
    }

    // ------------------------------------------------------------- fixtures

    static final String DEF_ETL_SERVER =
            "{\"spark_conf.spark.databricks.cluster.profile\":{\"type\":\"forbidden\",\"hidden\":true},"
                    + "\"autoscale.max_workers\":{\"type\":\"range\",\"maxValue\":8,\"defaultValue\":4}}";
    static final String DEF_ETL_DESIRED =
            "{\n  \"autoscale.max_workers\": {\"defaultValue\": 4.0, \"maxValue\": 8.0, \"type\": \"range\"},\n"
                    + "  \"spark_conf.spark.databricks.cluster.profile\": {\"hidden\": true, \"type\": \"forbidden\"}\n}";
    static final String DEF_GPU_OLD = "{\"node_type_id\":{\"type\":\"fixed\",\"value\":\"g5.xlarge\"}}";
    static final String DEF_GPU_NEW = "{\"node_type_id\":{\"type\":\"fixed\",\"value\":\"g6.xlarge\"}}";
    static final String OVR_OLD = "{\"autotermination_minutes\":{\"type\":\"fixed\",\"value\":60}}";
    static final String OVR_NEW = "{\"autotermination_minutes\":{\"type\":\"fixed\",\"value\":45}}";
    static final String DEF_STREAM =
            "{\"spark_version\":{\"type\":\"allowlist\",\"values\":[\"16.4.x-scala2.13\",\"17.0.x-scala2.13\"]},"
                    + "\"autotermination_minutes\":{\"type\":\"fixed\",\"value\":30}}";

    static void seedStore(FakeWorkspace fake) {
        fake.policies.add(policy("pol-11", "autoscale-etl",
                "definition", DEF_ETL_SERVER,
                "description", "Nightly ETL autoscaling",
                "max_clusters_per_user", 10.0));
        fake.policies.add(policy("pol-12", "gpu-research",
                "definition", DEF_GPU_OLD,
                "description", "GPU boxes for the research group",
                "max_clusters_per_user", 2.0,
                "libraries", List.of(Map.of("pypi", Map.of("package", "torch==2.4.0")))));
        fake.policies.add(policy("pol-13", "personal-default",
                "definition", "{\"num_workers\":{\"type\":\"fixed\",\"value\":0}}",
                "is_default", true));
        fake.policies.add(policy("pol-14", "family-shared",
                "policy_family_id", "personal-vm",
                "policy_family_definition_overrides", OVR_OLD));
    }

    static PoliciesClient client(FakeWorkspace fake, List<Long> sleeps) {
        return new PoliciesClient(fake.baseUrl, TOKEN, sleeps::add, MAX_RETRIES);
    }

    // ----------------------------------------------------------------- tests

    static void testDocsFixtures() throws IOException {
        Map<String, Object> contract = Json.parseObject(Files.readString(Path.of("docs", "contract.json")));
        Map<String, Object> sources = Json.parseObject(Files.readString(Path.of("docs", "official_sources.json")));
        @SuppressWarnings("unchecked")
        Map<String, Object> research = (Map<String, Object>) sources.get("research");
        checkEq(research.get("required"), Boolean.TRUE, "research.required must be true");
        @SuppressWarnings("unchecked")
        List<Map<String, Object>> officialSources = (List<Map<String, Object>>) (List<?>) research.get("official_sources");
        check(officialSources.size() >= 2, "at least two official sources required");
        for (Map<String, Object> src : officialSources) {
            String url = (String) src.get("url");
            check(url.startsWith("https://") && url.contains("databricks"),
                    "sources must be first-party Databricks pages: " + url);
            check(src.get("used_for") instanceof String s && !s.isBlank(),
                    "each source must say which facts it backed");
        }
        @SuppressWarnings("unchecked")
        List<Object> facts = (List<Object>) sources.get("verified_facts");
        check(facts.size() >= 4, "contract facts must be summarized");
        @SuppressWarnings("unchecked")
        Map<String, Object> ops = (Map<String, Object>) contract.get("operations");
        @SuppressWarnings("unchecked")
        Map<String, Object> list = (Map<String, Object>) ops.get("list");
        checkEq(list.get("path"), "/api/2.0/policies/clusters/list", "pinned list path");
        check(((String) list.get("success")).contains("unpaginated"),
                "the contract pins that the policy list is a single unpaginated document");
        @SuppressWarnings("unchecked")
        Map<String, Object> fam = (Map<String, Object>) ops.get("families_list");
        checkEq(fam.get("path"), "/api/2.0/policy-families", "pinned families path");
    }

    static void testListWire() throws IOException {
        FakeWorkspace fake = new FakeWorkspace();
        seedStore(fake);
        fake.start();
        try {
            List<Long> sleeps = new ArrayList<>();
            List<Map<String, Object>> policies = client(fake, sleeps).listPolicies();
            checkEq(policies.size(), 4, "all policies come back");
            Recorded r = fake.requests.get(0);
            checkEq(r.method, "GET", "list verb");
            checkEq(r.path, "/api/2.0/policies/clusters/list", "list path");
            checkEq(r.query, "sort_column=POLICY_NAME&sort_order=ASC", "list sort query");
            checkEq(r.headers.get("authorization"), EXPECTED_AUTH, "Bearer auth on list");
            checkEq(r.headers.get("accept"), "application/json", "Accept header on list");
            checkEq(policies.get(0).get("policy_id"), "pol-11", "policy_id preserved");
            check(policies.get(0).get("definition") instanceof String,
                    "definition must stay a JSON string on the wire");
            checkEq(policies.get(2).get("is_default"), Boolean.TRUE, "is_default preserved");
            checkEq(sleeps, List.of(), "no sleeping on a clean list");
        } finally {
            fake.stop();
        }
    }

    static void testCreateEditWire() throws IOException {
        FakeWorkspace fake = new FakeWorkspace();
        fake.start();
        try {
            PoliciesClient c = client(fake, new ArrayList<>());
            Map<String, Object> fields = new LinkedHashMap<>();
            fields.put("name", "smoke-test");
            fields.put("definition", DEF_GPU_OLD);
            fields.put("description", "wire check");
            String id = c.createPolicy(fields);
            checkEq(id, "pol-100", "create returns the new policy_id");
            Recorded create = fake.requests.get(0);
            checkEq(create.method + " " + create.path, "POST /api/2.0/policies/clusters/create", "create route");
            check(create.headers.get("content-type").startsWith("application/json"),
                    "create must send Content-Type: application/json");
            checkEq(Json.parse(create.body), fields, "create body carries exactly the given fields");

            Map<String, Object> edit = new LinkedHashMap<>(fields);
            edit.put("policy_id", id);
            edit.put("description", "wire check v2");
            c.editPolicy(edit);
            Recorded editReq = fake.requests.get(1);
            checkEq(editReq.method + " " + editReq.path, "POST /api/2.0/policies/clusters/edit", "edit route");
            checkEq(Json.parse(editReq.body), edit, "edit body carries exactly the given fields");
            checkEq(fake.findById(id).get("description"), "wire check v2", "edit applied");
        } finally {
            fake.stop();
        }
    }

    static void testFamiliesPagination() throws IOException {
        FakeWorkspace fake = new FakeWorkspace();
        fake.start();
        try {
            List<Map<String, Object>> families = client(fake, new ArrayList<>()).listPolicyFamilies(2);
            checkEq(families.size(), 3, "both family pages combined");
            checkEq(fake.requests.get(0).query, "max_results=2", "first families page query");
            checkEq(fake.requests.get(1).query, "max_results=2&page_token=pf-2",
                    "second page must resend max_results and the opaque page_token");
            checkEq(families.get(1).get("policy_family_id"), "personal-vm", "family ids preserved");
            checkEq(families.get(2).get("version"), 3.0, "family version preserved");
        } finally {
            fake.stop();
        }
    }

    static void testRateLimitRetryThenSuccess() throws IOException {
        FakeWorkspace fake = new FakeWorkspace();
        seedStore(fake);
        fake.start();
        try {
            fake.failOnce.put("/api/2.0/policies/clusters/list", new ArrayList<>(List.of(
                    new Fault(429, envelope("REQUEST_LIMIT_EXCEEDED", "Too many requests."), "4"))));
            List<Long> sleeps = new ArrayList<>();
            List<Map<String, Object>> policies = client(fake, sleeps).listPolicies();
            checkEq(policies.size(), 4, "the retried list still returns everything");
            checkEq(sleeps, List.of(4L), "sleep exactly the Retry-After seconds once");
            checkEq(fake.requests.size(), 2, "one 429 then one retry — no extras");
        } finally {
            fake.stop();
        }
    }

    static void testRateLimitExhaustion() throws IOException {
        FakeWorkspace fake = new FakeWorkspace();
        fake.start();
        try {
            fake.alwaysFail.put("/api/2.0/policies/clusters/get",
                    new Fault(429, envelope("REQUEST_LIMIT_EXCEEDED", "Too many requests."), "1"));
            List<Long> sleeps = new ArrayList<>();
            RateLimitException raised = null;
            try {
                client(fake, sleeps).getPolicy("pol-11");
            } catch (RateLimitException e) {
                raised = e;
            }
            check(raised != null, "persistent 429 must raise RateLimitException");
            checkEq(raised.statusCode(), 429, "status code preserved");
            checkEq(raised.errorCode(), "REQUEST_LIMIT_EXCEEDED", "error code preserved");
            checkEq(raised.retryAfterSeconds(), 1L, "Retry-After preserved");
            checkEq(sleeps.size(), MAX_RETRIES, "sleep once per retry, then give up");
            checkEq(fake.requests.size(), MAX_RETRIES + 1, "original attempt plus max retries");
        } finally {
            fake.stop();
        }
    }

    static void testErrorEnvelopeTypedAndRedacted() throws IOException {
        FakeWorkspace fake = new FakeWorkspace();
        fake.start();
        try {
            fake.alwaysFail.put("/api/2.0/policies/clusters/get",
                    new Fault(403, envelope("PERMISSION_DENIED",
                            "User does not have CAN_MANAGE on cluster-policy pol-11."), null));
            DbxApiException raised = null;
            try {
                client(fake, new ArrayList<>()).getPolicy("pol-11");
            } catch (DbxApiException e) {
                raised = e;
            }
            check(raised != null, "a 403 envelope must raise DbxApiException");
            checkEq(raised.statusCode(), 403, "statusCode decoded");
            checkEq(raised.errorCode(), "PERMISSION_DENIED", "error_code decoded");
            checkEq(raised.apiMessage(), "User does not have CAN_MANAGE on cluster-policy pol-11.",
                    "message decoded verbatim");
            check(raised.getMessage().contains("PERMISSION_DENIED"), "getMessage surfaces the code");
            check(!raised.getMessage().contains(TOKEN), "token must never leak into exceptions");
        } finally {
            fake.stop();
        }
    }

    static void testReconcileCreatesEditsSkipsPreserves() throws IOException {
        FakeWorkspace fake = new FakeWorkspace();
        seedStore(fake);
        fake.start();
        try {
            List<Map<String, Object>> desired = new ArrayList<>();
            Map<String, Object> d1 = new LinkedHashMap<>();
            d1.put("name", "autoscale-etl");
            d1.put("definition", DEF_ETL_DESIRED);
            d1.put("description", "Nightly ETL autoscaling");
            d1.put("max_clusters_per_user", 10.0);
            desired.add(d1);
            Map<String, Object> d2 = new LinkedHashMap<>();
            d2.put("name", "gpu-research");
            d2.put("definition", DEF_GPU_NEW);
            desired.add(d2);
            Map<String, Object> d3 = new LinkedHashMap<>();
            d3.put("name", "family-shared");
            d3.put("policy_family_id", "personal-vm");
            d3.put("policy_family_definition_overrides", OVR_NEW);
            desired.add(d3);
            Map<String, Object> d4 = new LinkedHashMap<>();
            d4.put("name", "stream-ingest");
            d4.put("definition", DEF_STREAM);
            d4.put("description", "Streaming ingestion clusters");
            d4.put("max_clusters_per_user", 5.0);
            desired.add(d4);

            List<Long> sleeps = new ArrayList<>();
            PolicyReconciler reconciler = new PolicyReconciler(client(fake, sleeps));
            ReconcileReport report = reconciler.reconcile(desired);

            checkEq(report.skipped(), List.of("autoscale-etl"),
                    "a semantically equal definition (key order, whitespace, 8 vs 8.0) must be skipped");
            checkEq(report.edited(), List.of("gpu-research", "family-shared"), "edited set");
            checkEq(report.created(), List.of("stream-ingest"), "created set");
            check(report.failures().isEmpty(), "no failures expected: " + report.failures().keySet());

            List<Recorded> listCalls = new ArrayList<>();
            for (Recorded r : fake.requests) {
                if (r.path.endsWith("/list")) listCalls.add(r);
            }
            checkEq(listCalls.size(), 1, "the reconciler must list existing policies exactly once");

            List<Recorded> writes = fake.writes();
            checkEq(writes.size(), 3, "two edits and one create — the skip must not write");

            Map<String, Object> gpuEdit = null;
            Map<String, Object> famEdit = null;
            Map<String, Object> create = null;
            for (Recorded w : writes) {
                Map<String, Object> body = Json.parseObject(w.body);
                if (w.path.endsWith("/create")) create = body;
                else if ("pol-12".equals(body.get("policy_id"))) gpuEdit = body;
                else if ("pol-14".equals(body.get("policy_id"))) famEdit = body;
            }
            check(gpuEdit != null, "gpu-research must be edited via its existing policy_id");
            checkEq(gpuEdit.get("name"), "gpu-research", "edit keeps the name");
            checkEq(Json.parse((String) gpuEdit.get("definition")), Json.parse(DEF_GPU_NEW),
                    "edit sends the new definition");
            checkEq(gpuEdit.get("description"), "GPU boxes for the research group",
                    "unmanaged description must be carried over, not dropped");
            checkEq(gpuEdit.get("max_clusters_per_user"), 2.0,
                    "unmanaged max_clusters_per_user must be carried over");
            checkEq(gpuEdit.get("libraries"),
                    List.of(Map.of("pypi", Map.of("package", "torch==2.4.0"))),
                    "unmanaged libraries must be carried over verbatim");
            check(!gpuEdit.containsKey("policy_family_id"),
                    "a definition-based edit must not send policy_family_id");

            check(famEdit != null, "family-shared must be edited via its existing policy_id");
            checkEq(famEdit.get("policy_family_id"), "personal-vm", "family inheritance kept");
            checkEq(Json.parse((String) famEdit.get("policy_family_definition_overrides")),
                    Json.parse(OVR_NEW), "family overrides updated");
            check(!famEdit.containsKey("definition"),
                    "definition and policy_family_id are mutually exclusive — a family edit must not send definition");

            check(create != null, "stream-ingest must be created");
            check(!create.containsKey("policy_id"), "create must not send a policy_id");
            checkEq(create.get("name"), "stream-ingest", "create name");
            checkEq(Json.parse((String) create.get("definition")), Json.parse(DEF_STREAM), "create definition");
            checkEq(create.get("description"), "Streaming ingestion clusters", "create description");
            checkEq(create.get("max_clusters_per_user"), 5.0, "create max_clusters_per_user");

            for (Recorded w : writes) {
                Map<String, Object> body = Json.parseObject(w.body);
                check(!"pol-13".equals(body.get("policy_id")),
                        "the Databricks-managed default policy must never be written to");
            }
            checkEq(fake.findById("pol-13").get("definition"),
                    "{\"num_workers\":{\"type\":\"fixed\",\"value\":0}}",
                    "default policy untouched in the store");
        } finally {
            fake.stop();
        }
    }

    static void testReconcilePermissionFailureContinues() throws IOException {
        FakeWorkspace fake = new FakeWorkspace();
        fake.policies.add(policy("pol-21", "locked-policy",
                "definition", DEF_GPU_OLD,
                "description", "Owned by infra"));
        fake.start();
        try {
            fake.alwaysFail.put("/api/2.0/policies/clusters/edit",
                    new Fault(403, envelope("PERMISSION_DENIED",
                            "User lacks CAN_MANAGE on policy pol-21."), null));
            List<Map<String, Object>> desired = new ArrayList<>();
            Map<String, Object> d1 = new LinkedHashMap<>();
            d1.put("name", "locked-policy");
            d1.put("definition", DEF_GPU_NEW);
            desired.add(d1);
            Map<String, Object> d2 = new LinkedHashMap<>();
            d2.put("name", "fresh-policy");
            d2.put("definition", DEF_STREAM);
            desired.add(d2);

            ReconcileReport report = new PolicyReconciler(client(fake, new ArrayList<>()))
                    .reconcile(desired);
            checkEq(report.created(), List.of("fresh-policy"),
                    "a permission failure on one policy must not abort the rest");
            checkEq(report.edited(), List.of(), "the denied edit is not reported as edited");
            checkEq(report.skipped(), List.of(), "nothing to skip");
            check(report.failures().containsKey("locked-policy"), "the denied policy lands in failures");
            DbxApiException failure = report.failures().get("locked-policy");
            checkEq(failure.statusCode(), 403, "failure keeps the HTTP status");
            checkEq(failure.errorCode(), "PERMISSION_DENIED", "failure keeps the error_code");
            check(failure.apiMessage().contains("pol-21"), "failure keeps the server message");
            boolean created = false;
            for (Map<String, Object> p : fake.policies) {
                if ("fresh-policy".equals(p.get("name"))) created = true;
            }
            check(created, "the create after the failure must actually run");
            checkEq(fake.findById("pol-21").get("definition"), DEF_GPU_OLD,
                    "the denied policy must remain unmodified");
        } finally {
            fake.stop();
        }
    }

    public static void main(String[] args) throws Exception {
        testDocsFixtures();
        System.out.println("ok  docs fixtures");
        testListWire();
        System.out.println("ok  list wire");
        testCreateEditWire();
        System.out.println("ok  create/edit wire");
        testFamiliesPagination();
        System.out.println("ok  families pagination");
        testRateLimitRetryThenSuccess();
        System.out.println("ok  rate limit retry");
        testRateLimitExhaustion();
        System.out.println("ok  rate limit exhaustion");
        testErrorEnvelopeTypedAndRedacted();
        System.out.println("ok  error envelope");
        testReconcileCreatesEditsSkipsPreserves();
        System.out.println("ok  reconcile create/edit/skip/preserve");
        testReconcilePermissionFailureContinues();
        System.out.println("ok  reconcile partial failure");
        System.out.println("PASS  " + checks + " checks");
    }
}
