package harness;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.stream.Stream;

/**
 * Deterministic verification for the vCenter onboarding seed.
 *
 * <p>Reads {@code build/report.json} and the per-scenario request logs written by the loopback mock
 * and asserts the exact wire shape of every request the client sent: method, path, headers, the
 * exact key set of every JSON body, and that no unset optional field reached the wire as null, an
 * empty string, an empty object or an empty array. It also asserts the precheck gate -- when the
 * precheck refuses, the mutating operation must never have been issued and nothing may have been
 * created.
 *
 * <p>No network access. Exits 0 when everything holds, 1 otherwise.
 *
 * <p>Harness file. Do not modify.
 */
public final class Verifier {

    private static final String AUTH = "NetworkInsight " + MockVcfOnServer.TOKEN;
    private static final String BASE = "/api/ni";
    private static final String PROXY_DC1 = "18230:901:1706494033";
    private static final String PROXY_DC2 = "18230:901:1706494077";
    private static final String PRECHECK_FAILURE_MESSAGE =
            "Validation failed: the supplied credentials were rejected by the vCenter Server.";

    private final List<String> failures = new ArrayList<>();
    private final Path root;

    private Verifier(Path root) {
        this.root = root;
    }

    public static void main(String[] args) throws Exception {
        Verifier v = new Verifier(Path.of(args.length > 0 ? args[0] : ".").toAbsolutePath().normalize());
        v.run();
        if (v.failures.isEmpty()) {
            System.out.println("VERIFY: PASS");
            return;
        }
        System.out.println("VERIFY: FAIL (" + v.failures.size() + ")");
        for (String f : v.failures) System.out.println("  - " + f);
        System.exit(1);
    }

    private void run() throws Exception {
        checkProtectedFiles();
        checkSingleFileClient();
        checkSeedSelfConsistency();

        Path reportPath = root.resolve("build/report.json");
        if (!Files.exists(reportPath)) {
            fail("build/report.json is missing; TestMain did not produce a report");
            return;
        }
        Map<String, Object> report =
                Json.parseObject(Files.readString(reportPath, StandardCharsets.UTF_8));
        List<Object> scenarios = Json.arr(report.get("scenarios"));
        if (scenarios == null || scenarios.size() != 12) {
            fail("expected 12 scenarios in build/report.json, found "
                    + (scenarios == null ? "none" : scenarios.size()));
            return;
        }

        int totalCreated = 0;
        for (Object o : scenarios) {
            Map<String, Object> s = Json.obj(o);
            String name = Json.str(s.get("name"));
            List<Object> log = readLog(Json.str(s.get("log_file")));
            checkUniversalHygiene(name, log);
            switch (name) {
                case "happy-ip-local-domain" -> happyIp(name, s, log);
                case "precheck-rejected" -> precheckRejected(name, s, log);
                case "happy-fqdn-all-options" -> happyFqdn(name, s, log);
                case "happy-ip-explicit-false" -> happyExplicitFalse(name, s, log);
                case "collector-not-found" -> noCollector(name, s, log, "Collector_10.220.232.250");
                case "platform-node-is-not-a-collector" -> noCollector(name, s, log, "Platform node");
                case "ambiguous-target", "missing-target", "missing-collector-name",
                        "missing-nickname", "missing-vcenter-credentials",
                        "missing-vcenter-username" -> invalidRequest(name, s, log);
                default -> fail("unexpected scenario in report: " + name);
            }
            List<Object> created = Json.arr(s.get("created"));
            totalCreated += created == null ? 0 : created.size();
        }
        if (totalCreated != 3) {
            fail("exactly 3 data sources should exist across all scenarios, found " + totalCreated);
        }
    }

    // ------------------------------------------------------------- structural

    private void checkProtectedFiles() throws Exception {
        Path manifest = root.resolve("tools/protected.sha256");
        if (!Files.exists(manifest)) {
            fail("tools/protected.sha256 is missing");
            return;
        }
        for (String line : Files.readAllLines(manifest, StandardCharsets.UTF_8)) {
            if (line.isBlank() || line.startsWith("#")) continue;
            String[] parts = line.trim().split("\\s+", 2);
            Path target = root.resolve(parts[1]);
            if (!Files.exists(target)) {
                fail("protected file is missing: " + parts[1]);
                continue;
            }
            String actual = sha256(target);
            if (!actual.equals(parts[0])) {
                fail("protected file was modified: " + parts[1]
                        + " (expected sha256 " + parts[0] + ", found " + actual + ")");
            }
        }
    }

    private void checkSingleFileClient() throws IOException {
        Path main = root.resolve("src/main/java");
        if (!Files.isDirectory(main)) {
            fail("src/main/java is missing");
            return;
        }
        List<String> sources = new ArrayList<>();
        try (Stream<Path> walk = Files.walk(main)) {
            walk.filter(p -> p.toString().endsWith(".java"))
                    .map(p -> main.relativize(p).toString().replace('\\', '/'))
                    .sorted()
                    .forEach(sources::add);
        }
        List<String> expected =
                List.of("com/broadcom/vcfops/networks/VcfOpsNetworksClient.java");
        if (!sources.equals(expected)) {
            fail("the client must remain a single file; expected " + expected
                    + " under src/main/java, found " + sources);
        }
    }

    private void checkSeedSelfConsistency() throws IOException {
        Map<String, Object> contract = Json.parseObject(
                Files.readString(root.resolve("docs/contract.json"), StandardCharsets.UTF_8));
        Map<String, Object> sources = Json.parseObject(
                Files.readString(root.resolve("docs/official_sources.json"), StandardCharsets.UTF_8));
        Set<String> fromContract = new LinkedHashSet<>();
        for (Object o : Json.arr(contract.get("operations"))) {
            fromContract.add(Json.str(Json.obj(o).get("operationId")));
        }
        Map<String, Object> spec = Json.obj(Json.arr(sources.get("sources")).get(0));
        Set<String> fromSources = new LinkedHashSet<>();
        for (Object o : Json.arr(spec.get("operations"))) {
            fromSources.add(Json.str(Json.obj(o).get("operationId")));
        }
        if (!fromContract.equals(fromSources)) {
            fail("contract operationIds " + fromContract
                    + " do not match docs/official_sources.json " + fromSources);
        }
        String contractSha = Json.str(Json.obj(contract.get("source_spec")).get("commit_sha"));
        String sourcesSha = Json.str(spec.get("commit_sha"));
        if (contractSha == null || !contractSha.equals(sourcesSha)) {
            fail("contract commit_sha " + contractSha + " does not match official_sources "
                    + sourcesSha);
        }
    }

    // ------------------------------------------------------------- hygiene

    private void checkUniversalHygiene(String scenario, List<Object> log) {
        for (Object o : log) {
            Map<String, Object> r = Json.obj(o);
            String where = scenario + " request #" + r.get("seq");
            if (r.get("operation_id") == Json.NULL) {
                fail(where + " hit " + r.get("method") + " " + r.get("path")
                        + ", which is not an operation named by the contract");
            }
            long status = ((Number) r.get("status")).longValue();
            if (status != 200 && status != 201) {
                fail(where + " (" + r.get("method") + " " + r.get("path") + ") was rejected with "
                        + status + ": " + Json.write(r.get("response_body")));
            }
            if (r.get("query") != Json.NULL) {
                fail(where + " carried a query string: " + r.get("query"));
            }
            if (r.get("body_parse_error") != Json.NULL) {
                fail(where + " sent a body that is not valid JSON: " + r.get("body_parse_error"));
            }
            Object body = r.get("body");
            if (body != Json.NULL) {
                emptinessScan(where, "", body);
            }
        }
    }

    /** No unset optional may reach the wire as null, "", {} or []. */
    private void emptinessScan(String where, String path, Object node) {
        if (node == Json.NULL) {
            fail(where + " sent " + label(path) + " as null; unset optional fields must be omitted");
            return;
        }
        if (node instanceof String s && s.isEmpty()) {
            fail(where + " sent " + label(path)
                    + " as an empty string; unset optional fields must be omitted");
            return;
        }
        if (node instanceof Map<?, ?> m) {
            if (m.isEmpty()) {
                fail(where + " sent " + label(path)
                        + " as an empty object; unset optional fields must be omitted");
                return;
            }
            for (Map.Entry<?, ?> e : m.entrySet()) {
                emptinessScan(where, path.isEmpty() ? String.valueOf(e.getKey())
                        : path + "." + e.getKey(), e.getValue());
            }
            return;
        }
        if (node instanceof List<?> l) {
            if (l.isEmpty()) {
                fail(where + " sent " + label(path)
                        + " as an empty array; unset optional fields must be omitted");
                return;
            }
            for (int i = 0; i < l.size(); i++) emptinessScan(where, path + "[" + i + "]", l.get(i));
        }
    }

    private static String label(String path) {
        return path.isEmpty() ? "the request body" : "'" + path + "'";
    }

    // ------------------------------------------------------------- scenarios

    private void happyIp(String name, Map<String, Object> s, List<Object> log) {
        if (!expectOperations(name, log, "create", "listExpandedNodes",
                "validateVCenter", "addVcenterDatasource")) return;

        Map<String, Object> auth = req(log, 0);
        checkRequestLine(name, auth, "POST", BASE + "/auth/token", null, "application/json");
        Map<String, Object> authBody = Json.obj(auth.get("body"));
        exactKeys(name + " auth body", authBody, "username", "password", "domain");
        equals(name + " auth username", authBody.get("username"), MockVcfOnServer.API_USERNAME);
        equals(name + " auth password", authBody.get("password"), MockVcfOnServer.API_PASSWORD);
        Map<String, Object> domain = Json.obj(authBody.get("domain"));
        exactKeys(name + " auth domain", domain, "domain_type");
        equals(name + " auth domain_type", domain == null ? null : domain.get("domain_type"), "LOCAL");

        Map<String, Object> nodes = req(log, 1);
        checkRequestLine(name, nodes, "GET", BASE + "/infra/expanded-nodes", AUTH, null);
        equals(name + " expanded-nodes body", nodes.get("body_raw"), "");

        Map<String, Object> precheck = req(log, 2);
        checkRequestLine(name, precheck, "POST", BASE + "/data-sources/vcenters/validate",
                AUTH, "application/json");
        Map<String, Object> pb = Json.obj(precheck.get("body"));
        exactKeys(name + " precheck body", pb, "ip", "proxy_id", "credentials");
        equals(name + " precheck ip", pb.get("ip"), "10.197.17.68");
        equals(name + " precheck proxy_id", pb.get("proxy_id"), PROXY_DC1);
        checkVcenterCredentials(name + " precheck", Json.obj(pb.get("credentials")));

        Map<String, Object> create = req(log, 3);
        checkRequestLine(name, create, "POST", BASE + "/data-sources/vcenters",
                AUTH, "application/json");
        Map<String, Object> cb = Json.obj(create.get("body"));
        exactKeys(name + " create body", cb, "ip", "proxy_id", "nickname", "credentials");
        equals(name + " create ip", cb.get("ip"), "10.197.17.68");
        equals(name + " create proxy_id", cb.get("proxy_id"), PROXY_DC1);
        equals(name + " create nickname", cb.get("nickname"), "vc-dc1");
        checkVcenterCredentials(name + " create", Json.obj(cb.get("credentials")));

        Map<String, Object> outcome = requireOutcome(name, s);
        if (outcome == null) return;
        equals(name + " succeeded", outcome.get("succeeded"), Boolean.TRUE);
        equals(name + " stage", outcome.get("stage"), "CREATE");
        equals(name + " failureCode", outcome.get("failureCode"), Json.NULL);
        equals(name + " proxyId", outcome.get("proxyId"), PROXY_DC1);
        equals(name + " precheckCode", outcome.get("precheckCode"), 200L);
        equals(name + " precheckMessage", outcome.get("precheckMessage"), "Validation successful.");
        checkCreated(name, s, outcome, 1);
    }

    private void precheckRejected(String name, Map<String, Object> s, List<Object> log) {
        if (!expectOperations(name, log, "create", "listExpandedNodes", "validateVCenter")) return;

        Map<String, Object> authBody = Json.obj(req(log, 0).get("body"));
        exactKeys(name + " auth body", authBody, "username", "password");

        Map<String, Object> precheck = req(log, 2);
        checkRequestLine(name, precheck, "POST", BASE + "/data-sources/vcenters/validate",
                AUTH, "application/json");
        Map<String, Object> pb = Json.obj(precheck.get("body"));
        exactKeys(name + " precheck body", pb, "fqdn", "proxy_id", "credentials");
        equals(name + " precheck fqdn", pb.get("fqdn"), "vc-bad.rainpole.local");
        equals(name + " precheck proxy_id", pb.get("proxy_id"), PROXY_DC1);
        checkVcenterCredentials(name + " precheck", Json.obj(pb.get("credentials")));

        Map<String, Object> outcome = requireOutcome(name, s);
        if (outcome == null) return;
        equals(name + " succeeded", outcome.get("succeeded"), Boolean.FALSE);
        equals(name + " stage", outcome.get("stage"), "PRECHECK");
        equals(name + " failureCode", outcome.get("failureCode"), "PRECHECK_REJECTED");
        equals(name + " proxyId", outcome.get("proxyId"), PROXY_DC1);
        equals(name + " precheckCode", outcome.get("precheckCode"), 400L);
        equals(name + " precheckMessage", outcome.get("precheckMessage"), PRECHECK_FAILURE_MESSAGE);
        equals(name + " entityId", outcome.get("entityId"), Json.NULL);
        checkCreated(name, s, outcome, 0);
    }

    private void happyFqdn(String name, Map<String, Object> s, List<Object> log) {
        if (!expectOperations(name, log, "create", "listExpandedNodes",
                "validateVCenter", "addVcenterDatasource")) return;

        Map<String, Object> authBody = Json.obj(req(log, 0).get("body"));
        exactKeys(name + " auth body", authBody, "username", "password", "domain");
        Map<String, Object> domain = Json.obj(authBody.get("domain"));
        exactKeys(name + " auth domain", domain, "domain_type", "value");
        equals(name + " auth domain_type", domain == null ? null : domain.get("domain_type"), "LDAP");
        equals(name + " auth domain value", domain == null ? null : domain.get("value"),
                "rainpole.local");

        Map<String, Object> pb = Json.obj(req(log, 2).get("body"));
        exactKeys(name + " precheck body", pb, "fqdn", "proxy_id", "credentials", "ipfix_enabled");
        equals(name + " precheck fqdn", pb.get("fqdn"), "vc02.rainpole.local");
        equals(name + " precheck proxy_id", pb.get("proxy_id"), PROXY_DC2);
        equals(name + " precheck ipfix_enabled", pb.get("ipfix_enabled"), Boolean.TRUE);
        checkVcenterCredentials(name + " precheck", Json.obj(pb.get("credentials")));

        Map<String, Object> cb = Json.obj(req(log, 3).get("body"));
        exactKeys(name + " create body", cb, "fqdn", "proxy_id", "nickname", "credentials",
                "notes", "enabled", "ipfix_request", "is_vmc");
        equals(name + " create fqdn", cb.get("fqdn"), "vc02.rainpole.local");
        equals(name + " create proxy_id", cb.get("proxy_id"), PROXY_DC2);
        equals(name + " create nickname", cb.get("nickname"), "vc-dc2");
        equals(name + " create notes", cb.get("notes"), "Located in DC2");
        equals(name + " create enabled", cb.get("enabled"), Boolean.FALSE);
        equals(name + " create is_vmc", cb.get("is_vmc"), Boolean.FALSE);
        Map<String, Object> ipfix = Json.obj(cb.get("ipfix_request"));
        exactKeys(name + " create ipfix_request", ipfix, "enable_all");
        equals(name + " create ipfix_request.enable_all",
                ipfix == null ? null : ipfix.get("enable_all"), Boolean.TRUE);
        checkVcenterCredentials(name + " create", Json.obj(cb.get("credentials")));

        Map<String, Object> outcome = requireOutcome(name, s);
        if (outcome == null) return;
        equals(name + " succeeded", outcome.get("succeeded"), Boolean.TRUE);
        equals(name + " stage", outcome.get("stage"), "CREATE");
        equals(name + " proxyId", outcome.get("proxyId"), PROXY_DC2);
        equals(name + " precheckCode", outcome.get("precheckCode"), 200L);
        checkCreated(name, s, outcome, 1);
    }

    private void happyExplicitFalse(String name, Map<String, Object> s, List<Object> log) {
        if (!expectOperations(name, log, "create", "listExpandedNodes",
                "validateVCenter", "addVcenterDatasource")) return;

        Map<String, Object> pb = Json.obj(req(log, 2).get("body"));
        exactKeys(name + " precheck body", pb, "ip", "proxy_id", "credentials", "ipfix_enabled");
        equals(name + " precheck ip", pb.get("ip"), "10.197.17.69");
        equals(name + " precheck proxy_id", pb.get("proxy_id"), PROXY_DC1);
        equals(name + " precheck ipfix_enabled", pb.get("ipfix_enabled"), Boolean.FALSE);
        Map<String, Object> precheckCredentials = Json.obj(pb.get("credentials"));
        exactKeys(name + " precheck credentials", precheckCredentials, "username");
        equals(name + " precheck credentials.username",
                precheckCredentials == null ? null : precheckCredentials.get("username"),
                TestMain.VC_USERNAME);

        Map<String, Object> cb = Json.obj(req(log, 3).get("body"));
        exactKeys(name + " create body", cb, "ip", "proxy_id", "nickname", "credentials",
                "enabled", "ipfix_request", "is_vmc");
        equals(name + " create ip", cb.get("ip"), "10.197.17.69");
        equals(name + " create proxy_id", cb.get("proxy_id"), PROXY_DC1);
        equals(name + " create nickname", cb.get("nickname"), "vc-explicit-false");
        equals(name + " create enabled", cb.get("enabled"), Boolean.TRUE);
        equals(name + " create is_vmc", cb.get("is_vmc"), Boolean.FALSE);
        Map<String, Object> ipfix = Json.obj(cb.get("ipfix_request"));
        exactKeys(name + " create ipfix_request", ipfix, "enable_all");
        equals(name + " create ipfix_request.enable_all",
                ipfix == null ? null : ipfix.get("enable_all"), Boolean.FALSE);
        Map<String, Object> createCredentials = Json.obj(cb.get("credentials"));
        exactKeys(name + " create credentials", createCredentials, "username");
        equals(name + " create credentials.username",
                createCredentials == null ? null : createCredentials.get("username"),
                TestMain.VC_USERNAME);

        Map<String, Object> outcome = requireOutcome(name, s);
        if (outcome == null) return;
        equals(name + " succeeded", outcome.get("succeeded"), Boolean.TRUE);
        equals(name + " stage", outcome.get("stage"), "CREATE");
        equals(name + " failureCode", outcome.get("failureCode"), Json.NULL);
        equals(name + " proxyId", outcome.get("proxyId"), PROXY_DC1);
        equals(name + " precheckCode", outcome.get("precheckCode"), 200L);
        checkCreated(name, s, outcome, 1);
    }

    private void noCollector(String name, Map<String, Object> s, List<Object> log, String what) {
        if (!expectOperations(name, log, "create", "listExpandedNodes")) return;
        Map<String, Object> outcome = requireOutcome(name, s);
        if (outcome == null) return;
        equals(name + " succeeded", outcome.get("succeeded"), Boolean.FALSE);
        equals(name + " stage", outcome.get("stage"), "RESOLVE_COLLECTOR");
        equals(name + " failureCode (" + what + ")", outcome.get("failureCode"),
                "COLLECTOR_NOT_FOUND");
        equals(name + " proxyId", outcome.get("proxyId"), Json.NULL);
        equals(name + " precheckCode", outcome.get("precheckCode"), Json.NULL);
        equals(name + " entityId", outcome.get("entityId"), Json.NULL);
        checkCreated(name, s, outcome, 0);
    }

    private void invalidRequest(String name, Map<String, Object> s, List<Object> log) {
        if (!log.isEmpty()) {
            fail(name + " must reject the request before any HTTP call, but "
                    + log.size() + " request(s) reached the appliance");
        }
        Map<String, Object> threw = Json.obj(s.get("threw"));
        if (threw == null) {
            fail(name + " should have thrown IllegalArgumentException, but returned "
                    + Json.write(s.get("outcome")));
        } else {
            equals(name + " exception type", threw.get("type"), "java.lang.IllegalArgumentException");
        }
        checkCreated(name, s, null, 0);
    }

    // ------------------------------------------------------------- assertions

    private boolean expectOperations(String scenario, List<Object> log, String... expected) {
        List<String> actual = new ArrayList<>();
        for (Object o : log) {
            Object id = Json.obj(o).get("operation_id");
            actual.add(id == Json.NULL ? "<unrouted:" + Json.obj(o).get("method") + " "
                    + Json.obj(o).get("path") + ">" : Json.str(id));
        }
        List<String> want = Arrays.asList(expected);
        if (!actual.equals(want)) {
            fail(scenario + " issued " + actual + " but the contract flow requires exactly " + want);
            return false;
        }
        return true;
    }

    private void checkRequestLine(String scenario, Map<String, Object> r, String method,
                                  String path, String authorization, String contentType) {
        equals(scenario + " #" + r.get("seq") + " method", r.get("method"), method);
        equals(scenario + " #" + r.get("seq") + " path", r.get("path"), path);
        Object actualAuth = r.get("authorization");
        if (authorization == null) {
            if (actualAuth != Json.NULL) {
                fail(scenario + " #" + r.get("seq") + " must not send an Authorization header to "
                        + path + ", but sent " + actualAuth);
            }
        } else {
            equals(scenario + " #" + r.get("seq") + " Authorization", actualAuth, authorization);
        }
        Object actualCt = r.get("content_type");
        if (contentType == null) {
            if (actualCt != Json.NULL) {
                fail(scenario + " #" + r.get("seq") + " sent a Content-Type on a bodyless request: "
                        + actualCt);
            }
        } else if (!(actualCt instanceof String ct) || !ct.startsWith(contentType)) {
            fail(scenario + " #" + r.get("seq") + " Content-Type should start with '" + contentType
                    + "' but was " + actualCt);
        }
    }

    private void checkVcenterCredentials(String where, Map<String, Object> creds) {
        exactKeys(where + " credentials", creds, "username", "password");
        if (creds == null) return;
        equals(where + " credentials.username", creds.get("username"), TestMain.VC_USERNAME);
        equals(where + " credentials.password", creds.get("password"), TestMain.VC_PASSWORD);
    }

    private void exactKeys(String where, Map<String, Object> body, String... keys) {
        if (body == null) {
            fail(where + " is missing or is not a JSON object");
            return;
        }
        Set<String> expected = new LinkedHashSet<>(Arrays.asList(keys));
        Set<String> actual = new LinkedHashSet<>(body.keySet());
        if (!expected.equals(actual)) {
            Set<String> missing = new LinkedHashSet<>(expected);
            missing.removeAll(actual);
            Set<String> extra = new LinkedHashSet<>(actual);
            extra.removeAll(expected);
            fail(where + " key set is wrong: expected " + expected + ", got " + actual
                    + (missing.isEmpty() ? "" : "; missing " + missing)
                    + (extra.isEmpty() ? "" : "; unexpected " + extra));
        }
    }

    private void checkCreated(String name, Map<String, Object> s, Map<String, Object> outcome,
                              int expected) {
        List<Object> created = Json.arr(s.get("created"));
        int actual = created == null ? 0 : created.size();
        if (actual != expected) {
            fail(name + " should have left " + expected + " data source(s) on the appliance, found "
                    + actual + ": " + Json.write(s.get("created")));
            return;
        }
        if (expected == 1 && outcome != null) {
            Object entityId = Json.obj(created.get(0)).get("entity_id");
            equals(name + " entityId", outcome.get("entityId"), entityId);
        }
    }

    private void equals(String what, Object actual, Object expected) {
        Object a = actual == null ? Json.NULL : actual;
        Object e = expected == null ? Json.NULL : expected;
        if (!a.equals(e)) {
            fail(what + ": expected " + render(e) + ", got " + render(a));
        }
    }

    private static String render(Object v) {
        return v == Json.NULL ? "null" : Json.write(v);
    }

    private void fail(String message) {
        failures.add(message);
    }

    // ------------------------------------------------------------- utilities

    private List<Object> readLog(String relative) throws IOException {
        Path p = root.resolve(relative);
        List<Object> out = new ArrayList<>();
        if (!Files.exists(p)) {
            fail("request log is missing: " + relative);
            return out;
        }
        for (String line : Files.readAllLines(p, StandardCharsets.UTF_8)) {
            if (!line.isBlank()) out.add(Json.parse(line));
        }
        return out;
    }

    private static Map<String, Object> req(List<Object> log, int index) {
        return Json.obj(log.get(index));
    }

    private Map<String, Object> requireOutcome(String name, Map<String, Object> s) {
        Map<String, Object> outcome = Json.obj(s.get("outcome"));
        if (outcome == null) {
            fail(name + " produced no outcome; the client threw " + Json.write(s.get("threw")));
        }
        return outcome;
    }

    private static String sha256(Path p) throws Exception {
        MessageDigest md = MessageDigest.getInstance("SHA-256");
        byte[] digest = md.digest(Files.readAllBytes(p));
        StringBuilder sb = new StringBuilder();
        for (byte b : digest) sb.append(String.format("%02x", b));
        return sb.toString();
    }
}
