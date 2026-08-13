package com.broadcom.vcf.sddclcm.harness;

import com.broadcom.vcf.sddclcm.Json;
import com.broadcom.vcf.sddclcm.SddcLcmClient;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Deterministic wire-shape verifier for the contract-pinned request log.
 *
 * <p>It asserts the exact request-target, header multiplicity, encoded body
 * bytes, token rotation and interruption/resume behaviour of one fleet component
 * upgrade. Unset optional members must be absent from the wire, never sent as
 * null, an empty string, an empty array, an empty object, a false default or a
 * bare delimiter. No live VMware endpoint is contacted.
 *
 * <p>This file is part of the protected harness. Do not modify it.
 */
public final class WireVerifier {

    private WireVerifier() {
    }

    /** Canonical encoded {@code DepotComponentsSpec} for the fixture's resolution request. */
    public static String expectedDepotResolutionBody() {
        Map<String, Object> fleetDepotSpec = Json.object();
        fleetDepotSpec.put("fqdn", ContractMock.FLEET_DEPOT_FQDN);
        fleetDepotSpec.put("certificate", ContractMock.FLEET_DEPOT_CERTIFICATE);

        Map<String, Object> componentVersion = Json.object();
        componentVersion.put("component", ContractMock.COMPONENT_TYPE);
        componentVersion.put("version", ContractMock.TARGET_VERSION);

        Map<String, Object> spec = Json.object();
        spec.put("fleetDepotSpec", fleetDepotSpec);
        spec.put("componentVersions", List.of(componentVersion));
        return Json.write(spec);
    }

    /** Canonical encoded {@code ComponentUpgradeSpec} for the precheck action. */
    public static String expectedPrecheckBody() {
        Map<String, Object> software = Json.object();
        software.put("version", ContractMock.TARGET_VERSION);

        Map<String, Object> depot = Json.object();
        depot.put("url", ContractMock.RESOLVED_BINARY_URL);

        Map<String, Object> componentSpec = Json.object();
        componentSpec.put("software", software);
        componentSpec.put("depot", depot);

        Map<String, Object> spec = Json.object();
        spec.put("componentSpec", componentSpec);
        return Json.write(spec);
    }

    /** Canonical encoded {@code ComponentUpgradeSpec} for the apply action. */
    public static String expectedApplyBody() {
        return expectedApplyBody(true);
    }

    /** Canonical encoded apply body for the supplied backup selection. */
    public static String expectedApplyBody(boolean performBackup) {
        Map<String, Object> software = Json.object();
        software.put("version", ContractMock.TARGET_VERSION);

        Map<String, Object> depot = Json.object();
        depot.put("url", ContractMock.RESOLVED_BINARY_URL);
        depot.put("certificate", List.of(ContractMock.FLEET_DEPOT_CERTIFICATE));

        Map<String, Object> componentSpec = Json.object();
        componentSpec.put("software", software);
        componentSpec.put("depot", depot);

        Map<String, Object> lcmPlatformSpec = Json.object();
        lcmPlatformSpec.put("performBackup", performBackup);

        Map<String, Object> spec = Json.object();
        spec.put("componentSpec", componentSpec);
        spec.put("lcmPlatformSpec", lcmPlatformSpec);
        spec.put("correlationId", ContractMock.CORRELATION_ID);
        return Json.write(spec);
    }

    /**
     * Asserts the complete ten-request expiry, renewal and resume sequence of a
     * successful fleet component upgrade.
     */
    public static void verifyUpgradeWire(List<ContractMock.RecordedRequest> log,
                                         TokenAuthority authority,
                                         SddcLcmClient.UpgradeOutcome outcome) {
        verifyUpgradeWire(log, authority, outcome, true);
    }

    /** Asserts the complete successful sequence for the supplied backup selection. */
    public static void verifyUpgradeWire(List<ContractMock.RecordedRequest> log,
                                         TokenAuthority authority,
                                         SddcLcmClient.UpgradeOutcome outcome,
                                         boolean performBackup) {
        Failures failures = new Failures(log);

        failures.check(log.size() == 10,
                "expected exactly 10 requests on the wire, saw " + log.size());
        if (log.size() != 10) {
            failures.raise();
        }

        String initial = "Bearer " + TokenAuthority.INITIAL_ACCESS_TOKEN;
        String replacement = "Bearer " + TokenAuthority.REPLACEMENT_ACCESS_TOKEN;
        String componentTarget = "/v1/components/" + ContractMock.COMPONENT_ID;
        String precheckTarget = "/v1/tasks/" + ContractMock.PRECHECK_TASK_ID;
        String applyTaskTarget = "/v1/tasks/" + ContractMock.APPLY_TASK_ID;

        // 0 - unauthenticated health probe.
        ContractMock.RecordedRequest health = log.get(0);
        failures.check("getHealth".equals(health.operationId()),
                "request 0 must reach getHealth, reached " + health.operationId());
        failures.check("GET".equals(health.method()), "request 0 must be a GET");
        failures.check("/v1/health".equals(health.rawTarget()),
                "request 0 target must be exactly /v1/health, saw " + health.rawTarget());
        failures.check(!health.hasHeader("Authorization"),
                "getHealth declares an empty security list, so it must send no Authorization header");
        failures.check(!health.hasHeader("Content-Type"), "request 0 must send no Content-Type");
        failures.check(health.body().isEmpty(), "request 0 must send no body");
        failures.check(health.responseStatus() == 200, "request 0 must be answered 200");

        // 1 - fleet-scoped component listing.
        ContractMock.RecordedRequest components = log.get(1);
        failures.check("getComponents".equals(components.operationId()),
                "request 1 must reach getComponents, reached " + components.operationId());
        failures.check("GET".equals(components.method()), "request 1 must be a GET");
        failures.check("/v1/components?scope=FLEET".equals(components.rawTarget()),
                "request 1 target must be exactly /v1/components?scope=FLEET, saw " + components.rawTarget());
        failures.check(!components.hasHeader("Content-Type"), "request 1 must send no Content-Type");
        failures.check(components.body().isEmpty(), "request 1 must send no body");
        failures.check(components.responseStatus() == 200, "request 1 must be answered 200");

        // 2 - depot resolution.
        ContractMock.RecordedRequest resolve = log.get(2);
        failures.check("resolveDepotComponents".equals(resolve.operationId()),
                "request 2 must reach resolveDepotComponents, reached " + resolve.operationId());
        failures.check("POST".equals(resolve.method()), "request 2 must be a POST");
        failures.check("/v1/depot/components".equals(resolve.rawTarget()),
                "request 2 target must be exactly /v1/depot/components with no query, saw " + resolve.rawTarget());
        failures.check(!resolve.queryDelimiterPresent(),
                "request 2 must not append a bare '?' delimiter for an absent query string");
        failures.check(!resolve.hasHeader("X-Correlation-Id"),
                "resolveDepotComponents declares no header parameter, so X-Correlation-Id must be absent");
        failures.checkBody(resolve, expectedDepotResolutionBody(),
                "request 2 must encode DepotComponentsSpec with fleetDepotSpec then componentVersions and omit the "
                        + "optional top level 'version'");
        failures.check(resolve.responseStatus() == 200, "request 2 must be answered 200");

        // 3 - precheck action.
        ContractMock.RecordedRequest precheck = log.get(3);
        failures.check("performComponentAction".equals(precheck.operationId()),
                "request 3 must reach performComponentAction, reached " + precheck.operationId());
        failures.check("POST".equals(precheck.method()), "request 3 must be a POST");
        failures.check((componentTarget + "?action=precheck").equals(precheck.rawTarget()),
                "request 3 target must be exactly " + componentTarget + "?action=precheck, saw " + precheck.rawTarget());
        failures.check(!precheck.hasHeader("X-Correlation-Id"),
                "the precheck submission leaves the optional X-Correlation-Id header unset, so it must be absent");
        failures.checkBody(precheck, expectedPrecheckBody(),
                "request 3 must encode ComponentUpgradeSpec with componentSpec only, and componentSpec with software "
                        + "then depot, omitting lcmPlatformSpec, correlationId, policy, userInput, additionalInput and "
                        + "DepotSpec.certificate");
        failures.check(precheck.responseStatus() == 202, "request 3 must be answered 202");

        // 4, 5 - precheck task polling.
        for (int index : new int[] {4, 5}) {
            ContractMock.RecordedRequest poll = log.get(index);
            failures.check("getTask".equals(poll.operationId()),
                    "request " + index + " must reach getTask, reached " + poll.operationId());
            failures.check("GET".equals(poll.method()), "request " + index + " must be a GET");
            failures.check(precheckTarget.equals(poll.rawTarget()),
                    "request " + index + " target must be exactly " + precheckTarget + ", saw " + poll.rawTarget());
            failures.check(!poll.queryDelimiterPresent(),
                    "getTask declares no query parameter, so request " + index + " must carry no '?' delimiter");
            failures.check(!poll.hasHeader("Content-Type"), "request " + index + " must send no Content-Type");
            failures.check(poll.body().isEmpty(), "request " + index + " must send no body");
            failures.check(poll.responseStatus() == 200, "request " + index + " must be answered 200");
        }

        // 6 - interrupted apply submission.
        ContractMock.RecordedRequest interrupted = log.get(6);
        failures.check("performComponentAction".equals(interrupted.operationId()),
                "request 6 must reach performComponentAction, reached " + interrupted.operationId());
        failures.check((componentTarget + "?action=apply").equals(interrupted.rawTarget()),
                "request 6 target must be exactly " + componentTarget + "?action=apply, saw " + interrupted.rawTarget());
        failures.check(interrupted.responseStatus() == 401,
                "request 6 must be the single expiry challenge answered 401, saw " + interrupted.responseStatus());
        failures.check(initial.equals(interrupted.header("Authorization")),
                "request 6 must still present the token the session started with");
        failures.check(ContractMock.CORRELATION_ID.equals(interrupted.header("X-Correlation-Id")),
                "the apply submission must send the caller's correlation id in the declared X-Correlation-Id header");
        failures.checkBody(interrupted, expectedApplyBody(performBackup),
                "request 6 must encode ComponentUpgradeSpec with componentSpec, lcmPlatformSpec and correlationId, "
                        + "DepotSpec carrying url then certificate, and no policy, userInput or additionalInput");

        // 7 - resumed apply submission.
        ContractMock.RecordedRequest resumed = log.get(7);
        failures.check("performComponentAction".equals(resumed.operationId()),
                "request 7 must reach performComponentAction, reached " + resumed.operationId());
        failures.check(interrupted.rawTarget().equals(resumed.rawTarget()),
                "request 7 must resume the identical apply target, saw " + resumed.rawTarget());
        failures.check(interrupted.body().equals(resumed.body()),
                "request 7 must resend the identical apply body rather than rebuild a different one");
        failures.check(ContractMock.CORRELATION_ID.equals(resumed.header("X-Correlation-Id")),
                "request 7 must resend the identical X-Correlation-Id header");
        failures.check(replacement.equals(resumed.header("Authorization")),
                "request 7 must present the renewed access token");
        failures.check(resumed.responseStatus() == 202, "request 7 must be answered 202");

        // 8, 9 - apply task polling.
        for (int index : new int[] {8, 9}) {
            ContractMock.RecordedRequest poll = log.get(index);
            failures.check("getTask".equals(poll.operationId()),
                    "request " + index + " must reach getTask, reached " + poll.operationId());
            failures.check(applyTaskTarget.equals(poll.rawTarget()),
                    "request " + index + " target must be exactly " + applyTaskTarget + ", saw " + poll.rawTarget());
            failures.check(replacement.equals(poll.header("Authorization")),
                    "request " + index + " must present the renewed access token");
            failures.check(poll.responseStatus() == 200, "request " + index + " must be answered 200");
        }

        verifyInvariants(log, authority, 1, failures);
        verifyOutcome(outcome, failures);
        failures.raise();
    }

    /** Asserts the log-wide invariants that hold for every scenario. */
    public static void verifyInvariants(List<ContractMock.RecordedRequest> log,
                                        TokenAuthority authority,
                                        int maxUnauthorized,
                                        Failures failures) {
        String initial = "Bearer " + TokenAuthority.INITIAL_ACCESS_TOKEN;
        String replacement = "Bearer " + TokenAuthority.REPLACEMENT_ACCESS_TOKEN;

        Map<String, Integer> reached = new LinkedHashMap<>();
        int unauthorized = 0;
        int firstReplacement = -1;
        int lastInitial = -1;
        for (ContractMock.RecordedRequest request : log) {
            reached.merge(String.valueOf(request.operationId()), 1, Integer::sum);
            if (request.responseStatus() == 401) {
                unauthorized++;
            }
            String authorization = request.header("Authorization");
            if (initial.equals(authorization)) {
                lastInitial = request.sequence();
            }
            if (replacement.equals(authorization) && firstReplacement < 0) {
                firstReplacement = request.sequence();
            }
            boolean healthProbe = "getHealth".equals(request.operationId());
            failures.check(request.headerValues("Authorization").size() == (healthProbe ? 0 : 1),
                    "request " + request.sequence() + (healthProbe
                            ? " must omit Authorization"
                            : " must send exactly one Authorization header"));
            failures.check(List.of("application/json").equals(request.headerValues("Accept")),
                    "request " + request.sequence() + " must send exactly one Accept: application/json header");
            failures.check(request.headerValues("X-Correlation-Id").size() <= 1,
                    "request " + request.sequence() + " sent the X-Correlation-Id header more than once");
            failures.check(!"".equals(request.header("X-Correlation-Id")),
                    "request " + request.sequence() + " sent an empty X-Correlation-Id rather than omitting it");
            failures.check(request.responseStatus() != 400,
                    "request " + request.sequence() + " was rejected by the contract-pinned fixture: " + request);
            failures.check(request.operationId() != null,
                    "request " + request.sequence() + " reached no operation named by docs/contract.json: " + request);
            if (!request.body().isEmpty()) {
                failures.check(List.of("application/json").equals(request.headerValues("Content-Type")),
                        "request " + request.sequence()
                                + " must send exactly one Content-Type: application/json header");
                assertNoUnsetPlaceholders(request, failures);
            }
        }

        failures.check(unauthorized <= maxUnauthorized,
                "at most " + maxUnauthorized + " request(s) may be answered 401 in this scenario, saw " + unauthorized);
        failures.check(authority.refreshCount() <= 1,
                "the access token must be renewed at most once per run, saw " + authority.refreshCount());
        failures.check(reached.getOrDefault("getComponents", 0) <= 1,
                "renewing the session must not replay getComponents, saw "
                        + reached.getOrDefault("getComponents", 0) + " listings");
        failures.check(reached.getOrDefault("resolveDepotComponents", 0) <= 1,
                "renewing the session must not replay resolveDepotComponents, saw "
                        + reached.getOrDefault("resolveDepotComponents", 0) + " resolutions");
        failures.check(reached.getOrDefault("getHealth", 0) <= 1,
                "the health probe runs once per workflow, saw " + reached.getOrDefault("getHealth", 0));
        if (firstReplacement >= 0) {
            failures.check(lastInitial < firstReplacement,
                    "request " + lastInitial + " presented the expired token after the session was renewed");
        }
    }

    /** Asserts the workflow result reported back to the caller. */
    private static void verifyOutcome(SddcLcmClient.UpgradeOutcome outcome, Failures failures) {
        failures.check(outcome != null, "upgradeFleetComponent returned no outcome");
        if (outcome == null) {
            return;
        }
        failures.check(ContractMock.COMPONENT_ID.equals(outcome.componentId()),
                "outcome componentId must be " + ContractMock.COMPONENT_ID + ", saw " + outcome.componentId());
        failures.check(ContractMock.COMPONENT_TYPE.equals(outcome.componentType()),
                "outcome componentType must be " + ContractMock.COMPONENT_TYPE + ", saw " + outcome.componentType());
        failures.check(ContractMock.CURRENT_VERSION.equals(outcome.previousVersion()),
                "outcome previousVersion must be the listed component version, saw " + outcome.previousVersion());
        failures.check(ContractMock.TARGET_VERSION.equals(outcome.targetVersion()),
                "outcome targetVersion must be " + ContractMock.TARGET_VERSION + ", saw " + outcome.targetVersion());
        failures.check(ContractMock.RESOLVED_BINARY_URL.equals(outcome.resolvedBinaryUrl()),
                "outcome resolvedBinaryUrl must be the depot-resolved binary url, saw " + outcome.resolvedBinaryUrl());
        failures.check(ContractMock.PRECHECK_TASK_ID.equals(outcome.precheckTaskId()),
                "outcome precheckTaskId must be " + ContractMock.PRECHECK_TASK_ID + ", saw " + outcome.precheckTaskId());
        failures.check("SUCCEEDED".equals(outcome.precheckStatus()),
                "outcome precheckStatus must be SUCCEEDED, saw " + outcome.precheckStatus());
        failures.check(ContractMock.APPLY_TASK_ID.equals(outcome.applyTaskId()),
                "outcome applyTaskId must be " + ContractMock.APPLY_TASK_ID + ", saw " + outcome.applyTaskId());
        failures.check("SUCCEEDED".equals(outcome.applyStatus()),
                "outcome applyStatus must be SUCCEEDED, saw " + outcome.applyStatus());
        failures.check(outcome.accessTokenRefreshes() == 1,
                "outcome accessTokenRefreshes must report the single renewal, saw " + outcome.accessTokenRefreshes());
    }

    private static void assertNoUnsetPlaceholders(ContractMock.RecordedRequest request, Failures failures) {
        Object document;
        try {
            document = Json.parse(request.body());
        } catch (RuntimeException failure) {
            failures.check(false, "request " + request.sequence() + " sent a body that is not valid JSON");
            return;
        }
        List<String> offenders = new ArrayList<>();
        walk(document, "$", offenders);
        failures.check(offenders.isEmpty(),
                "request " + request.sequence() + " encoded unset optional members instead of omitting them: "
                        + offenders);
    }

    private static void walk(Object node, String pointer, List<String> offenders) {
        switch (node) {
            case null -> offenders.add(pointer + " is null");
            case String text -> {
                if (text.isEmpty()) {
                    offenders.add(pointer + " is an empty string");
                }
            }
            case Map<?, ?> members -> {
                if (members.isEmpty()) {
                    offenders.add(pointer + " is an empty object");
                }
                members.forEach((name, value) -> walk(value, pointer + "." + name, offenders));
            }
            case List<?> items -> {
                if (items.isEmpty()) {
                    offenders.add(pointer + " is an empty array");
                }
                for (int index = 0; index < items.size(); index++) {
                    walk(items.get(index), pointer + "[" + index + "]", offenders);
                }
            }
            default -> {
            }
        }
    }

    /** Accumulates every wire-shape violation so one run reports them together. */
    public static final class Failures {
        private final List<String> messages = new ArrayList<>();
        private final List<ContractMock.RecordedRequest> log;

        public Failures(List<ContractMock.RecordedRequest> log) {
            this.log = log;
        }

        public void check(boolean condition, String message) {
            if (!condition) {
                messages.add(message);
            }
        }

        void checkBody(ContractMock.RecordedRequest request, String expected, String message) {
            if (expected.equals(request.body())) {
                return;
            }
            messages.add(message
                    + System.lineSeparator() + "      expected body: " + expected
                    + System.lineSeparator() + "        actual body: " + request.body());
        }

        public void raise() {
            if (messages.isEmpty()) {
                return;
            }
            StringBuilder report = new StringBuilder("wire contract violated:");
            for (String message : messages) {
                report.append(System.lineSeparator()).append("  - ").append(message);
            }
            report.append(System.lineSeparator()).append("  request log:");
            for (ContractMock.RecordedRequest request : log) {
                report.append(System.lineSeparator()).append("      ").append(request);
            }
            throw new AssertionError(report.toString());
        }
    }
}
