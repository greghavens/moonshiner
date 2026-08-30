package com.vmware.vcfops.networks.test;

import com.vmware.vcfops.networks.Json;
import com.vmware.vcfops.networks.NetworkInsightInventoryClient.ApplicationSummary;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Asserts the exact wire shape of every request the client made, against the operations named in
 * {@code docs/contract.json}.
 *
 * <p>Assertions are collected rather than thrown one at a time, so a failing run reports every
 * deviation at once. Nothing here contacts the network: it reads only the in-memory request log
 * captured by {@link MockNiServer}.
 *
 * <p>DO NOT MODIFY.
 */
public final class ContractVerifier {

    private final List<RecordedRequest> log;
    private final List<String> issuedTokens;
    private final List<Map<String, Object>> fixture;
    private final List<ApplicationSummary> snapshot;
    private final List<String> failures = new ArrayList<>();

    public ContractVerifier(List<RecordedRequest> log,
                            List<String> issuedTokens,
                            List<Map<String, Object>> fixture,
                            List<ApplicationSummary> snapshot) {
        this.log = log;
        this.issuedTokens = issuedTokens;
        this.fixture = fixture;
        this.snapshot = snapshot;
    }

    /** Runs every check. Returns the list of failures; empty means the contract was honoured. */
    public List<String> verify() {
        verifyNothingOutsideContract();
        verifyCreateRequests();
        verifyAuthorizationHeaders();
        verifyListRequests();
        verifyDetailRequests();
        verifyRecoveryShape();
        verifyNoWorkLostOrRedone();
        verifyDeleteRequest();
        verifySnapshot();
        return List.copyOf(failures);
    }

    // ------------------------------------------------------------- sections

    private void verifyNothingOutsideContract() {
        for (RecordedRequest r : log) {
            if ("unknown".equals(r.operationId())) {
                fail("Request " + r.describe() + " matches no operation in the pinned contract. "
                        + "The contract names exactly: create, delete, listApplications, "
                        + "getApplicationById.");
            }
            if (r.responseStatus() != 200 && r.responseStatus() != 204 && r.responseStatus() != 401) {
                fail("Request " + r.describe() + " was rejected by the appliance. Body: "
                        + bodyOf(r));
            }
        }
        if (log.isEmpty()) {
            fail("The client made no requests at all.");
        }
    }

    private void verifyCreateRequests() {
        List<RecordedRequest> creates = byOperation("create");
        if (creates.size() != 3) {
            fail("Expected exactly 3 calls to operationId 'create': one to log in, and one after "
                    + "each of the two token expiries. Saw " + creates.size() + ".");
        }
        for (RecordedRequest r : creates) {
            if (r.rawQuery() != null && !r.rawQuery().isEmpty()) {
                fail("create " + r.describe() + " must not carry query parameters.");
            }
            if (r.contentTypeHeader() == null
                    || !r.contentTypeHeader().toLowerCase().contains("application/json")) {
                fail("create " + r.describe() + " must send Content-Type: application/json, saw "
                        + r.contentTypeHeader() + ".");
            }
            if (r.authorizationHeader() != null) {
                fail("create " + r.describe() + " is an unauthenticated operation "
                        + "(security: [] in the specification) and must not send an Authorization "
                        + "header, saw '" + r.authorizationHeader() + "'.");
            }
            verifyCredentialBody(r);
        }
    }

    private void verifyCredentialBody(RecordedRequest r) {
        Map<String, Object> body;
        try {
            body = Json.parseObject(r.body());
        } catch (RuntimeException e) {
            fail("create " + r.describe() + " body is not a JSON object: " + r.body());
            return;
        }
        expectKeys("create " + r.describe() + " UserCredential body", body.keySet(),
                Set.of("username", "password", "domain"));

        if (!MockNiServer.USERNAME.equals(Json.stringAt(body, "username"))) {
            fail("create " + r.describe() + " sent username '" + Json.stringAt(body, "username")
                    + "', expected '" + MockNiServer.USERNAME + "'.");
        }
        if (!MockNiServer.PASSWORD.equals(Json.stringAt(body, "password"))) {
            fail("create " + r.describe() + " sent the wrong password.");
        }

        Map<String, Object> domain = Json.objectAt(body, "domain");
        if (domain == null) {
            fail("create " + r.describe() + " is missing the 'domain' object.");
            return;
        }
        // The spec says Domain.value is "not required for LOCAL domain". Unset means absent.
        expectKeys("create " + r.describe() + " Domain object", domain.keySet(),
                Set.of("domain_type"));
        if (domain.containsKey("value")) {
            fail("create " + r.describe() + " sent 'domain.value' as "
                    + Json.write(domain.get("value")) + ". The specification marks it not required "
                    + "for a LOCAL domain, so an unset value must be omitted from the body, not "
                    + "sent empty or null.");
        }
        if (!MockNiServer.DOMAIN_TYPE.equals(Json.stringAt(domain, "domain_type"))) {
            fail("create " + r.describe() + " sent domain_type '"
                    + Json.stringAt(domain, "domain_type") + "', expected uppercase '"
                    + MockNiServer.DOMAIN_TYPE + "'.");
        }
    }

    private void verifyAuthorizationHeaders() {
        Set<String> valid = new LinkedHashSet<>(issuedTokens);
        for (RecordedRequest r : log) {
            if ("create".equals(r.operationId())) {
                continue;
            }
            String header = r.authorizationHeader();
            if (header == null) {
                fail(r.operationId() + " " + r.describe()
                        + " sent no Authorization header; the contract requires "
                        + "'NetworkInsight {token}'.");
                continue;
            }
            if (!header.startsWith("NetworkInsight ")) {
                fail(r.operationId() + " " + r.describe() + " sent Authorization '" + header
                        + "'. The scheme token is 'NetworkInsight', not Bearer or Basic.");
                continue;
            }
            String token = header.substring("NetworkInsight ".length());
            if (token.startsWith(" ") || token.isBlank()) {
                fail(r.operationId() + " " + r.describe() + " sent Authorization '" + header
                        + "'; expected exactly one space between scheme and token.");
                continue;
            }
            if (!valid.contains(token)) {
                fail(r.operationId() + " " + r.describe() + " presented token '" + token
                        + "', which was never issued by operationId 'create'.");
            }
        }
    }

    private void verifyListRequests() {
        List<RecordedRequest> lists = byOperation("listApplications");
        List<RecordedRequest> successful = lists.stream()
                .filter(r -> r.responseStatus() == 200)
                .toList();
        if (successful.size() != 3) {
            fail("Expected exactly 3 successful calls to operationId 'listApplications', one "
                    + "for each page. Saw " + successful.size() + ": " + describeAll(successful));
        }

        for (RecordedRequest r : lists) {
            Set<String> expectedKeys = r.queryParameters().containsKey("cursor")
                    ? Set.of("size", "cursor")
                    : Set.of("size");
            expectKeys("listApplications " + r.describe(), r.queryParameterNames(), expectedKeys);
            expectSize(r);
            forbidEmptyValues(r);
            if (r.queryParameters().containsKey("modifiedAfter")) {
                fail("listApplications " + r.describe() + " sent 'modifiedAfter'. The task does "
                        + "not use it, so it must be omitted entirely.");
            }
        }
    }

    private void expectSize(RecordedRequest r) {
        String size = r.queryParameters().get("size");
        if (size == null || Double.parseDouble(size) != MockNiServer.PAGE_SIZE) {
            fail("listApplications " + r.describe() + " sent size='" + size + "', expected "
                    + MockNiServer.PAGE_SIZE + ".");
        }
    }

    private void verifyDetailRequests() {
        List<RecordedRequest> details = byOperation("getApplicationById");
        long successful = details.stream().filter(r -> r.responseStatus() == 200).count();
        if (successful != fixture.size()) {
            fail("Expected exactly " + fixture.size()
                    + " successful calls to operationId 'getApplicationById', one per "
                    + "application. Saw " + successful + ".");
        }
        for (RecordedRequest r : details) {
            expectKeys("getApplicationById " + r.describe(), r.queryParameterNames(),
                    Set.of("fetch_member_counts"));
            if (!"true".equals(r.queryParameters().get("fetch_member_counts"))) {
                fail("getApplicationById " + r.describe() + " sent fetch_member_counts='"
                        + r.queryParameters().get("fetch_member_counts") + "', expected 'true'.");
            }
            if (r.queryParameters().containsKey("fetch_update_status")) {
                fail("getApplicationById " + r.describe() + " sent 'fetch_update_status'. The task "
                        + "does not use it, so it must be omitted entirely.");
            }
            forbidEmptyValues(r);
        }
    }

    private void verifyRecoveryShape() {
        Map<String, Integer> issueOrder = new LinkedHashMap<>();
        for (int i = 0; i < issuedTokens.size(); i++) {
            issueOrder.put(issuedTokens.get(i), i);
        }

        List<RecordedRequest> unauthorized = log.stream()
                .filter(r -> r.responseStatus() == 401)
                .toList();
        if (unauthorized.size() != 2) {
            fail("Expected exactly 2 requests to be answered 401 (one per token expiry). Saw "
                    + unauthorized.size() + ": " + describeAll(unauthorized));
        }

        for (RecordedRequest expired : unauthorized) {
            int idx = log.indexOf(expired);
            RecordedRequest next = idx + 1 < log.size() ? log.get(idx + 1) : null;
            RecordedRequest replay = idx + 2 < log.size() ? log.get(idx + 2) : null;

            if (next == null || !"create".equals(next.operationId())) {
                fail("After the 401 on " + expired.describe() + " the client should immediately "
                        + "call operationId 'create' to obtain a fresh token, but the next request "
                        + "was " + (next == null ? "nothing" : next.describe()) + ".");
                continue;
            }
            if (replay == null) {
                fail("After refreshing the token following " + expired.describe()
                        + " the client stopped instead of replaying the failed request.");
                continue;
            }
            boolean sameTarget = replay.method().equals(expired.method())
                    && replay.path().equals(expired.path())
                    && replay.queryParameters().equals(expired.queryParameters());
            if (!sameTarget) {
                fail("The request after the token refresh was " + replay.describe()
                        + ", but it should replay the exact request that was rejected: "
                        + expired.describe() + ". Retrying a different page or restarting the "
                        + "sweep loses or repeats work.");
            }
            Integer expiredIdx = issueOrder.get(expired.presentedToken());
            Integer replayIdx = issueOrder.get(replay.presentedToken());
            if (expiredIdx != null && replayIdx != null && replayIdx <= expiredIdx) {
                fail("The replay of " + expired.describe()
                        + " reused the expired token instead of the newly issued one.");
            }
        }

        int highWater = -1;
        for (RecordedRequest r : log) {
            String token = r.presentedToken();
            Integer order = token == null ? null : issueOrder.get(token);
            if (order == null) {
                continue;
            }
            if (order < highWater) {
                fail("Request " + r.describe() + " went back to an older token after a newer one "
                        + "had been issued.");
            }
            highWater = Math.max(highWater, order);
        }
    }

    private void verifyNoWorkLostOrRedone() {
        List<String> pagesFetched = new ArrayList<>();
        for (RecordedRequest r : byOperation("listApplications")) {
            if (r.responseStatus() == 200) {
                String cursor = r.queryParameters().get("cursor");
                pagesFetched.add(cursor == null ? "<first>" : cursor);
            }
        }
        if (pagesFetched.size() != new LinkedHashSet<>(pagesFetched).size()) {
            fail("The same page of listApplications was fetched successfully more than once: "
                    + pagesFetched + ". A token refresh must resume from the saved cursor, not "
                    + "restart pagination.");
        }
        if (!pagesFetched.equals(List.of("<first>", "NQ==", "MTA="))) {
            fail("Successful listApplications pages were " + pagesFetched
                    + ", expected [<first>, NQ==, MTA=].");
        }

        List<String> detailsFetched = new ArrayList<>();
        for (RecordedRequest r : byOperation("getApplicationById")) {
            if (r.responseStatus() == 200) {
                detailsFetched.add(entityIdOf(r));
            }
        }
        List<String> expectedIds = fixture.stream()
                .map(a -> String.valueOf(a.get("entity_id")))
                .toList();
        if (detailsFetched.size() != new LinkedHashSet<>(detailsFetched).size()) {
            fail("At least one application had its details fetched successfully more than once: "
                    + detailsFetched + ". A token refresh must resume, not redo completed work.");
        }
        if (!new LinkedHashSet<>(detailsFetched).equals(new LinkedHashSet<>(expectedIds))) {
            fail("Successful getApplicationById calls covered " + detailsFetched
                    + ", expected every listed application exactly once: "
                    + expectedIds + ".");
        }
    }

    private void verifyDeleteRequest() {
        List<RecordedRequest> deletes = byOperation("delete");
        if (deletes.size() != 1) {
            fail("Expected exactly 1 call to operationId 'delete' to release the final token. Saw "
                    + deletes.size() + ". The specification warns that deleting an expired token "
                    + "returns 401, so already-expired tokens must not be deleted.");
            return;
        }
        RecordedRequest r = deletes.get(0);
        if (log.indexOf(r) != log.size() - 1) {
            fail("The call to operationId 'delete' must be the last request of the run, but "
                    + (log.size() - 1 - log.indexOf(r)) + " request(s) followed it.");
        }
        if (r.responseStatus() != 204) {
            fail("delete " + r.describe() + " expected 204.");
        }
        if (r.rawQuery() != null && !r.rawQuery().isEmpty()) {
            fail("delete " + r.describe() + " must not carry query parameters.");
        }
        if (!issuedTokens.isEmpty()
                && !issuedTokens.get(issuedTokens.size() - 1).equals(r.presentedToken())) {
            fail("delete " + r.describe() + " must present the final, still-valid token.");
        }
    }

    private void verifySnapshot() {
        if (snapshot == null) {
            fail("collectApplicationInventory() returned null.");
            return;
        }
        if (snapshot.size() != fixture.size()) {
            fail("collectApplicationInventory() returned " + snapshot.size()
                    + " applications, expected " + fixture.size() + ".");
            return;
        }
        for (int i = 0; i < fixture.size(); i++) {
            Map<String, Object> want = fixture.get(i);
            ApplicationSummary got = snapshot.get(i);
            if (got == null) {
                fail("Snapshot entry " + i + " is null.");
                continue;
            }
            String expected = want.get("entity_id") + "/" + want.get("name") + "/"
                    + want.get("tier_count") + "/" + want.get("member_count");
            String actual = got.entityId() + "/" + got.name() + "/" + got.tierCount() + "/"
                    + got.memberCount();
            if (!expected.equals(actual)) {
                fail("Snapshot entry " + i + " was " + actual + ", expected " + expected + ".");
            }
        }
    }

    // -------------------------------------------------------------- helpers

    private String entityIdOf(RecordedRequest r) {
        String prefix = "/api/ni/groups/applications/";
        return r.path().startsWith(prefix) ? r.path().substring(prefix.length()) : r.path();
    }

    private void expectKeys(String what, Set<String> actual, Set<String> expected) {
        if (!actual.equals(expected)) {
            Set<String> missing = new LinkedHashSet<>(expected);
            missing.removeAll(actual);
            Set<String> extra = new LinkedHashSet<>(actual);
            extra.removeAll(expected);
            StringBuilder sb = new StringBuilder(what + " had keys " + actual + ", expected "
                    + expected + ".");
            if (!missing.isEmpty()) {
                sb.append(" Missing: ").append(missing).append('.');
            }
            if (!extra.isEmpty()) {
                sb.append(" Unexpected: ").append(extra)
                        .append(". An optional field with no value must be omitted, not sent empty.");
            }
            fail(sb.toString());
        }
    }

    private void forbidEmptyValues(RecordedRequest r) {
        r.queryParameters().forEach((k, v) -> {
            if (v == null || v.isEmpty()) {
                fail(r.operationId() + " " + r.describe() + " sent query parameter '" + k
                        + "' with no value. An unset optional parameter must be left out of the "
                        + "query string entirely.");
            }
        });
    }

    private List<RecordedRequest> byOperation(String operationId) {
        return log.stream().filter(r -> operationId.equals(r.operationId())).toList();
    }

    private String describeAll(List<RecordedRequest> requests) {
        return requests.stream().map(RecordedRequest::describe).toList().toString();
    }

    private String bodyOf(RecordedRequest r) {
        return r.body() == null || r.body().isEmpty() ? "<empty>" : r.body();
    }

    private void fail(String message) {
        failures.add(message);
    }
}
