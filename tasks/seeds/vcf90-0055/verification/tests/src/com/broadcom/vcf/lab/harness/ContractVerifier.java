package com.broadcom.vcf.lab.harness;

import com.broadcom.vcf.lab.VcenterSessionClient.CloneRequest;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Base64;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.stream.Collectors;

/**
 * Asserts the exact wire shape of everything the client put on the socket, using the mock's request
 * log plus the contract the mock was built from. Nothing here contacts a live endpoint.
 *
 * <p>Part of the protected harness: do not modify.
 */
public final class ContractVerifier {

    /** The commit the contract must be transcribed from: tag 9.0.0.0 of vmware/vcf-api-specs. */
    private static final String PINNED_COMMIT = "85151f6b1bb58f13b6ac0304bfec53904bea085f";
    private static final String PINNED_TAG = "9.0.0.0";
    private static final String PINNED_SPEC_PATH = "specifications/vsphere/openapi/automation/vcenter.yaml";
    private static final Set<String> ALLOWED_OPERATIONS = new LinkedHashSet<>(List.of(
            "Cis.Session_create", "Cis.Session_delete", "Vcenter.VM_list", "Vcenter.VM_clone"));

    private static final String SESSION_HEADER = "vmware-api-session-id";

    private final Path contractFile;
    private final Path sourcesFile;
    private final MockVcenter mock;
    private final List<CloneRequest> requested;
    private final List<String> returnedIds;
    private final Throwable runFailure;
    private final Throwable closeFailure;

    private final List<String> failures = new ArrayList<>();
    private int checks = 0;

    public ContractVerifier(Path contractFile, Path sourcesFile, MockVcenter mock,
                            List<CloneRequest> requested, List<String> returnedIds,
                            Throwable runFailure, Throwable closeFailure) {
        this.contractFile = contractFile;
        this.sourcesFile = sourcesFile;
        this.mock = mock;
        this.requested = requested;
        this.returnedIds = returnedIds;
        this.runFailure = runFailure;
        this.closeFailure = closeFailure;
    }

    /** @return true when every check passed */
    public boolean run() throws IOException {
        List<RequestRecord> log = mock.requestLog();

        provenance();
        noUnhandledFailure();
        routing(log);
        sessionCreates(log);
        vmList(log);
        clones(log);
        expiryRecovery(log);
        sessionDelete(log);
        results();

        report(log);
        return failures.isEmpty();
    }

    // ------------------------------------------------------------------ groups of checks

    private void provenance() throws IOException {
        Map<String, Object> contract = Json.parseObject(Files.readString(contractFile, StandardCharsets.UTF_8));
        Map<String, Object> source = MockVcenter.map(contract, "source");
        check("contract is transcribed from the pinned 9.0.0.0 commit",
                PINNED_COMMIT.equals(source.get("commit_sha")),
                "docs/contract.json source.commit_sha is " + source.get("commit_sha"));
        check("contract names tag 9.0.0.0 of the specification",
                PINNED_TAG.equals(source.get("tag")) && PINNED_TAG.equals(source.get("info_version")),
                "docs/contract.json source.tag=" + source.get("tag")
                        + " source.info_version=" + source.get("info_version"));
        check("contract names the vcenter automation specification file",
                PINNED_SPEC_PATH.equals(source.get("spec_path")),
                "docs/contract.json source.spec_path is " + source.get("spec_path"));

        Set<String> declared = new LinkedHashSet<>();
        for (Object o : MockVcenter.list(contract, "operations")) {
            declared.add(MockVcenter.str(MockVcenter.asMap(o), "operationId"));
        }
        check("contract declares exactly the four operations this client may use",
                declared.equals(ALLOWED_OPERATIONS),
                "docs/contract.json declares " + declared);

        Map<String, Object> sources = Json.parseObject(Files.readString(sourcesFile, StandardCharsets.UTF_8));
        Map<String, Object> primary = null;
        for (Object o : MockVcenter.list(sources, "sources")) {
            Map<String, Object> entry = MockVcenter.asMap(o);
            if (PINNED_COMMIT.equals(entry.get("commit_sha"))) primary = entry;
        }
        check("official_sources.json records the pinned specification commit",
                primary != null,
                "no entry in docs/official_sources.json carries commit_sha " + PINNED_COMMIT);
        if (primary != null) {
            check("official_sources.json records the spec path and tag",
                    PINNED_SPEC_PATH.equals(primary.get("spec_path")) && PINNED_TAG.equals(primary.get("tag")),
                    "spec_path=" + primary.get("spec_path") + " tag=" + primary.get("tag"));
            Set<String> recorded = new LinkedHashSet<>(MockVcenter.strings(primary, "operation_ids"));
            check("official_sources.json records every operationId the contract uses",
                    recorded.equals(ALLOWED_OPERATIONS),
                    "operation_ids recorded as " + recorded);
        }
    }

    private void noUnhandledFailure() {
        check("cloneFanOut completed without throwing", runFailure == null, describe(runFailure));
        check("close released the session without throwing", closeFailure == null, describe(closeFailure));
    }

    private void routing(List<RequestRecord> log) {
        check("the client sent at least one request", !log.isEmpty(), "the mock saw no traffic at all");
        List<RequestRecord> unmatched = log.stream()
                .filter(r -> r.operationId == null)
                .collect(Collectors.toList());
        check("every request routed to an operation named in the contract",
                unmatched.isEmpty(),
                "unmatched: " + unmatched);
        if (!log.isEmpty()) {
            check("the run opens with Cis.Session_create",
                    "Cis.Session_create".equals(log.get(0).operationId),
                    "first request was " + log.get(0));
        }
    }

    private void sessionCreates(List<RequestRecord> log) {
        List<RequestRecord> creates = byOperation(log, "Cis.Session_create");
        check("the session is created exactly twice: once up front, once after it expires",
                creates.size() == 2,
                "saw " + creates.size() + " Cis.Session_create requests");

        String expectedBasic = "Basic " + Base64.getEncoder().encodeToString(
                (MockVcenter.USERNAME + ":" + MockVcenter.PASSWORD).getBytes(StandardCharsets.UTF_8));
        for (RequestRecord r : creates) {
            check("Cis.Session_create #" + r.seq + " targets POST /api/session with no query string",
                    "POST".equals(r.method) && "/api/session".equals(r.path) && r.rawQuery == null,
                    "saw " + r.method + " " + r.target());
            check("Cis.Session_create #" + r.seq + " carries the Basic credential",
                    expectedBasic.equals(r.header("authorization")),
                    "authorization header was " + r.header("authorization"));
            check("Cis.Session_create #" + r.seq + " sends no request body",
                    r.body.isEmpty(),
                    "body was " + r.body);
            check("Cis.Session_create #" + r.seq + " does not present a session token",
                    r.header(SESSION_HEADER) == null,
                    SESSION_HEADER + " was " + r.header(SESSION_HEADER));
        }
    }

    private void vmList(List<RequestRecord> log) {
        List<RequestRecord> lists = byOperation(log, "Vcenter.VM_list");
        check("the source virtual machine is resolved exactly once and the lookup is not repeated "
                        + "after the session is refreshed",
                lists.size() == 1,
                "saw " + lists.size() + " Vcenter.VM_list requests");
        if (lists.size() != 1) return;

        RequestRecord r = lists.get(0);
        check("Vcenter.VM_list is a GET on /api/vcenter/vm",
                "GET".equals(r.method) && "/api/vcenter/vm".equals(r.path),
                "saw " + r.method + " " + r.target());
        check("Vcenter.VM_list sets only the names filter, leaving the seven other filters off the "
                        + "query string entirely",
                r.query.keySet().equals(Set.of("names")),
                "query string was " + r.rawQuery);
        check("the names filter carries exactly the source virtual machine's name",
                List.of(MockVcenter.SOURCE_VM_NAME).equals(r.query.get("names")),
                "names was " + r.query.get("names"));
        check("Vcenter.VM_list sends no request body",
                r.body.isEmpty(),
                "body was " + r.body);
    }

    private void clones(List<RequestRecord> log) {
        List<RequestRecord> clones = byOperation(log, "Vcenter.VM_clone");
        int expected = requested.size() + 1; // one attempt per clone, plus the one the 401 interrupted
        check("the batch costs exactly one clone request per clone plus the single retry the "
                        + "expiry forced",
                clones.size() == expected,
                "saw " + clones.size() + " Vcenter.VM_clone requests, expected " + expected);

        for (RequestRecord r : clones) {
            check("Vcenter.VM_clone #" + r.seq + " is POST /api/vcenter/vm?action=clone",
                    "POST".equals(r.method) && "/api/vcenter/vm".equals(r.path)
                            && List.of("clone").equals(r.query.get("action"))
                            && r.query.keySet().equals(Set.of("action")),
                    "saw " + r.method + " " + r.target());
            String contentType = r.header("content-type");
            check("Vcenter.VM_clone #" + r.seq + " declares a JSON request body",
                    contentType != null && contentType.toLowerCase(Locale.ROOT).startsWith("application/json"),
                    "content-type was " + contentType);
        }

        // Match every clone attempt to the CloneRequest it was for, and check its exact body.
        for (RequestRecord r : clones) {
            Map<String, Object> body;
            try {
                body = Json.parseObject(r.body);
            } catch (RuntimeException e) {
                check("Vcenter.VM_clone #" + r.seq + " sends a JSON object body", false, e.getMessage());
                continue;
            }
            Object name = body.get("name");
            CloneRequest spec = requested.stream()
                    .filter(c -> c.name.equals(name))
                    .findFirst().orElse(null);
            if (spec == null) {
                check("Vcenter.VM_clone #" + r.seq + " clones one of the requested names", false,
                        "body name was " + name + ", expected one of "
                                + requested.stream().map(c -> c.name).collect(Collectors.toList()));
                continue;
            }

            Set<String> expectedKeys = new LinkedHashSet<>(List.of("source", "name"));
            if (spec.placementFolder != null) expectedKeys.add("placement");
            if (spec.powerOn != null) expectedKeys.add("power_on");

            check("the CloneSpec for '" + spec.name + "' (request #" + r.seq + ") carries exactly the "
                            + "properties the caller set and omits every property the caller left unset",
                    r.body.isEmpty() ? false : keysOf(body).equals(expectedKeys),
                    "body keys were " + keysOf(body) + ", expected " + expectedKeys + "; body=" + r.body);

            check("the CloneSpec for '" + spec.name + "' (request #" + r.seq + ") clones the resolved "
                            + "source identifier",
                    MockVcenter.SOURCE_VM_ID.equals(body.get("source")),
                    "source was " + body.get("source"));

            check("the CloneSpec for '" + spec.name + "' (request #" + r.seq + ") sends no explicit "
                            + "nulls and no empty placeholder values",
                    noEmptyPlaceholders(body),
                    "an unset property was serialized as null, \"\", [], or {} instead of being "
                            + "omitted; body=" + r.body);

            if (spec.placementFolder != null) {
                Object placement = body.get("placement");
                check("the placement for '" + spec.name + "' (request #" + r.seq + ") carries only the "
                                + "folder the caller set",
                        placement instanceof Map
                                && ((Map<?, ?>) placement).keySet().equals(Set.of("folder"))
                                && spec.placementFolder.equals(((Map<?, ?>) placement).get("folder")),
                        "placement was " + Json.write(placement));
            }
            if (spec.powerOn != null) {
                check("power_on for '" + spec.name + "' (request #" + r.seq + ") is the boolean the "
                                + "caller asked for, sent even when it is false",
                        spec.powerOn.equals(body.get("power_on")),
                        "power_on was " + Json.write(body.get("power_on")));
            }
        }

        // Each requested name is attempted once, except the one the expiry interrupted.
        Map<String, Integer> attempts = new LinkedHashMap<>();
        for (RequestRecord r : clones) {
            try {
                Object n = Json.parseObject(r.body).get("name");
                if (n instanceof String) attempts.merge((String) n, 1, Integer::sum);
            } catch (RuntimeException ignored) {
                // already reported above
            }
        }
        List<String> interrupted = clones.stream()
                .filter(r -> r.status == 401)
                .map(this::cloneName)
                .filter(java.util.Objects::nonNull)
                .collect(Collectors.toList());
        check("exactly one clone request was interrupted by the expired session",
                interrupted.size() == 1,
                "clone requests answered 401: " + interrupted);

        for (CloneRequest c : requested) {
            int want = interrupted.contains(c.name) ? 2 : 1;
            check("'" + c.name + "' was submitted " + want + (want == 1
                            ? " time: work already done before the expiry is not repeated"
                            : " times: the interrupted clone is retried, not abandoned"),
                    Integer.valueOf(want).equals(attempts.get(c.name)),
                    "'" + c.name + "' was submitted " + attempts.getOrDefault(c.name, 0) + " time(s)");
        }
    }

    private void expiryRecovery(List<RequestRecord> log) {
        List<String> tokens = mock.issuedTokens();
        check("the mock issued two session tokens", tokens.size() == 2,
                "issued tokens: " + tokens);
        if (tokens.size() != 2) return;
        String first = tokens.get(0);
        String second = tokens.get(1);

        List<RequestRecord> denied = log.stream().filter(r -> r.status == 401).collect(Collectors.toList());
        check("the run hits the expired session exactly once",
                denied.size() == 1,
                "requests answered 401: " + denied);
        if (denied.size() != 1) return;
        int expirySeq = denied.get(0).seq;

        check("the request that hit the expiry was the one presenting the first token",
                first.equals(denied.get(0).header(SESSION_HEADER)),
                "the 401'd request presented " + denied.get(0).header(SESSION_HEADER));

        check("the first token was spent on exactly the work it was good for before the expiry",
                expirySeq == MockVcenter.FIRST_TOKEN_BUDGET + 2,
                "the expiry landed on request #" + expirySeq + "; with a token good for "
                        + MockVcenter.FIRST_TOKEN_BUDGET + " authenticated requests it should land on #"
                        + (MockVcenter.FIRST_TOKEN_BUDGET + 2)
                        + ", so the client made requests it did not need to");

        for (RequestRecord r : log) {
            if ("Cis.Session_create".equals(r.operationId)) continue;
            String presented = r.header(SESSION_HEADER);
            if (r.seq <= expirySeq) {
                check("request #" + r.seq + " presents the first session token",
                        first.equals(presented),
                        "presented " + presented);
            } else {
                check("request #" + r.seq + " presents the refreshed session token",
                        second.equals(presented),
                        "presented " + presented + "; a request after the refresh must not reuse the "
                                + "token that already expired");
            }
        }

        RequestRecord refresh = log.stream()
                .filter(r -> r.seq > expirySeq && "Cis.Session_create".equals(r.operationId))
                .findFirst().orElse(null);
        check("the client refreshes the session immediately after the 401",
                refresh != null && refresh.seq == expirySeq + 1,
                refresh == null ? "no Cis.Session_create followed the 401"
                        : "the refresh was request #" + refresh.seq + ", not #" + (expirySeq + 1));

        RequestRecord retry = log.stream()
                .filter(r -> refresh != null && r.seq == refresh.seq + 1)
                .findFirst().orElse(null);
        check("the interrupted clone is retried straight after the refresh",
                retry != null && "Vcenter.VM_clone".equals(retry.operationId)
                        && cloneName(denied.get(0)) != null
                        && cloneName(denied.get(0)).equals(cloneName(retry)),
                retry == null ? "nothing followed the refresh"
                        : "request #" + retry.seq + " was " + retry.operationId
                                + " for " + cloneName(retry) + ", expected a retry of "
                                + cloneName(denied.get(0)));
        if (retry != null && denied.get(0).body != null) {
            check("the retry replays the same CloneSpec the expiry interrupted",
                    sameJson(denied.get(0).body, retry.body),
                    "interrupted body " + denied.get(0).body + " vs retry body " + retry.body);
        }
    }

    private void sessionDelete(List<RequestRecord> log) {
        List<RequestRecord> deletes = byOperation(log, "Cis.Session_delete");
        check("the live session is released exactly once",
                deletes.size() == 1,
                "saw " + deletes.size() + " Cis.Session_delete requests; the token that already "
                        + "expired must not be deleted, and the live one must be");
        if (deletes.size() != 1) return;
        RequestRecord r = deletes.get(0);
        check("Cis.Session_delete is a DELETE on /api/session with no query string and no body",
                "DELETE".equals(r.method) && "/api/session".equals(r.path)
                        && r.rawQuery == null && r.body.isEmpty(),
                "saw " + r.method + " " + r.target() + " body=" + r.body);
        check("Cis.Session_delete is the last thing on the wire",
                r.seq == log.size(),
                "the run continued after logout with " + log.get(log.size() - 1));
        check("Cis.Session_delete was accepted",
                r.status == 204,
                "the mock answered " + r.status);
    }

    private void results() {
        List<String> expectedIds = new ArrayList<>();
        for (int i = 0; i < requested.size(); i++) expectedIds.add("vm-" + (2001 + i));
        check("cloneFanOut returns the new identifiers in the order the clones were requested",
                expectedIds.equals(returnedIds),
                "returned " + returnedIds + ", expected " + expectedIds);

        Map<String, String> inventory = mock.inventory();
        for (int i = 0; i < requested.size(); i++) {
            String id = "vm-" + (2001 + i);
            check("the mock holds '" + requested.get(i).name + "' as " + id,
                    requested.get(i).name.equals(inventory.get(id)),
                    id + " is " + inventory.get(id));
        }
        check("no extra virtual machines were created",
                inventory.size() == 4 + requested.size(),
                "the mock holds " + inventory.size() + " virtual machines: " + inventory);
    }

    // ------------------------------------------------------------------ helpers

    private static boolean noEmptyPlaceholders(Object node) {
        if (node == null) return false;
        if (node instanceof String) return !((String) node).isEmpty();
        if (node instanceof Map) {
            Map<?, ?> m = (Map<?, ?>) node;
            if (m.isEmpty()) return false;
            for (Object v : m.values()) if (!noEmptyPlaceholders(v)) return false;
            return true;
        }
        if (node instanceof List) {
            List<?> l = (List<?>) node;
            if (l.isEmpty()) return false;
            for (Object v : l) if (!noEmptyPlaceholders(v)) return false;
            return true;
        }
        return true;
    }

    private static boolean sameJson(String a, String b) {
        try {
            return Json.write(Json.parse(a)).equals(Json.write(Json.parse(b)))
                    || normalizedKeys(a).equals(normalizedKeys(b));
        } catch (RuntimeException e) {
            return false;
        }
    }

    private static Map<String, String> normalizedKeys(String json) {
        Map<String, Object> parsed = Json.parseObject(json);
        Map<String, String> out = new java.util.TreeMap<>();
        parsed.forEach((k, v) -> out.put(k, Json.write(v)));
        return out;
    }

    private String cloneName(RequestRecord r) {
        try {
            Object n = Json.parseObject(r.body).get("name");
            return n instanceof String ? (String) n : null;
        } catch (RuntimeException e) {
            return null;
        }
    }

    private static Set<String> keysOf(Map<String, Object> body) {
        return new LinkedHashSet<>(body.keySet());
    }

    private static List<RequestRecord> byOperation(List<RequestRecord> log, String operationId) {
        return log.stream().filter(r -> operationId.equals(r.operationId)).collect(Collectors.toList());
    }

    private static String describe(Throwable t) {
        if (t == null) return "";
        java.io.StringWriter sw = new java.io.StringWriter();
        t.printStackTrace(new java.io.PrintWriter(sw));
        return sw.toString().trim();
    }

    private void check(String what, boolean ok, String detail) {
        checks++;
        if (ok) {
            System.out.println("  ok   " + what);
        } else {
            System.out.println("  FAIL " + what);
            if (detail != null && !detail.isEmpty()) {
                for (String line : detail.split("\n")) System.out.println("         " + line);
            }
            failures.add(what + (detail == null || detail.isEmpty() ? "" : " -- " + detail));
        }
    }

    private void report(List<RequestRecord> log) {
        System.out.println();
        System.out.println("request log as the mock saw it:");
        for (RequestRecord r : log) System.out.println("  " + r);
        System.out.println();
        System.out.println(checks + " checks, " + failures.size() + " failed");
    }
}
