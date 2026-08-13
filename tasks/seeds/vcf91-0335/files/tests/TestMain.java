import java.nio.file.Path;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Deterministic acceptance harness for the VCF Automation change client.
 *
 * <p>It drives one multi-step change against the loopback contract fixture, then checks two
 * things: the exact bytes the client put on the wire, and whether the returned report tells the
 * truth about a change whose last step failed while the earlier steps stayed in place. No VMware
 * endpoint is contacted.
 */
public final class TestMain {

    private static final String TENANT = "prod-fin";
    private static final String REFRESH_TOKEN = "vcfa-rt/9f14+c0d3=b7a2";
    private static final String ENCODED_REFRESH_TOKEN = "vcfa-rt%2F9f14%2Bc0d3%3Db7a2";
    private static final String ZONE_ID = "9c1f4d2e-4f2a-4a63-9b0e-2f70c31d51aa";
    private static final String CATALOG_ITEM_ID = "5b8f31a4-6d7e-4a55-9c2e-1c9c0f6c88d1";
    private static final String API_VERSION = "2021-07-15";
    private static final String ACTION_ID = "Deployment.PowerOff";
    private static final String ACTION_REASON = "CHG-40218: power off after provisioning validation";

    public static void main(String[] args) throws Exception {
        require(args.length == 1, "usage: TestMain <contract.json>");

        try (MockVcfaServer mock = new MockVcfaServer(Path.of(args[0]))) {
            VcfaChangeClient client = new VcfaChangeClient(mock.baseUri(), TENANT, REFRESH_TOKEN);

            VcfaChangeClient.ProjectSpec project = new VcfaChangeClient.ProjectSpec(
                    MockVcfaServer.PROJECT_NAME,
                    "",
                    List.of(new VcfaChangeClient.ZoneAssignment(ZONE_ID, 1, 25, null, null, null)),
                    1800L,
                    "",
                    null,
                    "",
                    Map.of(),
                    API_VERSION,
                    null);

            Map<String, String> inputs = new LinkedHashMap<>();
            inputs.put("flavor", "medium");
            inputs.put("image", "ubuntu-2404");
            inputs.put("clusterSize", "3");
            VcfaChangeClient.CatalogRequestSpec catalogRequest =
                    new VcfaChangeClient.CatalogRequestSpec(CATALOG_ITEM_ID, null,
                            MockVcfaServer.DEPLOYMENT_NAME, inputs, "", null);

            VcfaChangeClient.ActionSpec action =
                    new VcfaChangeClient.ActionSpec(ACTION_ID, Map.of(), ACTION_REASON);

            VcfaChangeClient.ChangeReport report = client.runChange(project, catalogRequest, action);

            assertWireShape(mock);
            assertReport(mock, report);
        }

        System.out.println("PASS: VCF Automation change wire shape and failure reporting verified");
    }

    private static void assertWireShape(MockVcfaServer mock) {
        List<MockVcfaServer.RecordedRequest> log = mock.requestLog();
        require(log.size() == 8,
                "expected exactly the 8 contract calls of this change, observed " + log.size()
                        + ": " + log);

        String expectedProjectBody = "{\"name\":\"" + MockVcfaServer.PROJECT_NAME + "\""
                + ",\"zoneAssignmentConfigurations\":[{\"zoneId\":\"" + ZONE_ID + "\""
                + ",\"priority\":1,\"maxNumberInstances\":25}]"
                + ",\"operationTimeout\":1800}";
        String expectedCatalogBody = "{\"deploymentName\":\"" + MockVcfaServer.DEPLOYMENT_NAME + "\""
                + ",\"inputs\":{\"flavor\":\"medium\",\"image\":\"ubuntu-2404\",\"clusterSize\":\"3\"}"
                + ",\"projectId\":\"" + mock.projectId() + "\"}";
        String expectedActionBody = "{\"actionId\":\"" + ACTION_ID + "\""
                + ",\"reason\":\"" + ACTION_REASON + "\"}";

        MockVcfaServer.RecordedRequest token = log.get(0);
        require("POST".equals(token.method()), "token exchange must be a POST");
        require(("/tm/oauth/tenant/" + TENANT + "/token").equals(token.rawPath()),
                "wrong token exchange path: " + token.rawPath());
        require(token.rawQuery() == null, "token exchange must carry no query string");
        require("application/x-www-form-urlencoded".equals(token.header("content-type")),
                "token exchange must be form-encoded, got " + token.header("content-type"));
        require("application/json".equals(token.header("accept")),
                "token exchange must ask for application/json");
        require(token.header("authorization") == null,
                "token exchange must not send an Authorization header");
        require(("grant_type=refresh_token&refresh_token=" + ENCODED_REFRESH_TOKEN)
                        .equals(token.body()),
                "wrong token exchange payload: " + token.body());

        MockVcfaServer.RecordedRequest createProject = log.get(1);
        assertJsonCall(mock, createProject, "POST", "/iaas/api/projects", "apiVersion=" + API_VERSION,
                expectedProjectBody);
        requireOmitted(createProject, "description", "unset optional description");
        requireOmitted(createProject, "machineNamingTemplate", "unset machineNamingTemplate");
        requireOmitted(createProject, "sharedResources", "unset sharedResources");
        requireOmitted(createProject, "placementPolicy", "empty placementPolicy");
        requireOmitted(createProject, "customProperties", "empty customProperties map");
        requireOmitted(createProject, "administrators", "unsupplied administrators");
        requireOmitted(createProject, "memoryLimitMB", "unset nested zone limit");
        requireOmitted(createProject, "cpuLimit", "unset nested zone limit");
        requireOmitted(createProject, "storageLimitGB", "unset nested zone limit");
        require(!createProject.rawQuery().contains("validatePrincipals"),
                "unset optional query parameter validatePrincipals was still sent");

        MockVcfaServer.RecordedRequest catalog = log.get(2);
        assertJsonCall(mock, catalog, "POST", "/catalog/api/items/" + CATALOG_ITEM_ID + "/request",
                null, expectedCatalogBody);
        requireOmitted(catalog, "reason", "empty catalog request reason");
        requireOmitted(catalog, "bulkRequestCount", "unset bulkRequestCount");
        requireOmitted(catalog, "version", "unset catalog item version");

        String deploymentPath = "/deployment/api/deployments/" + mock.deploymentId();
        assertPoll(mock, log.get(3), deploymentPath);
        assertPoll(mock, log.get(4), deploymentPath);

        MockVcfaServer.RecordedRequest submit = log.get(5);
        assertJsonCall(mock, submit, "POST", deploymentPath + "/requests", null, expectedActionBody);
        requireOmitted(submit, "inputs", "empty day-2 inputs map");

        String requestPath = "/deployment/api/requests/" + mock.actionRequestId();
        assertPoll(mock, log.get(6), requestPath);
        assertPoll(mock, log.get(7), requestPath);
    }

    private static void assertReport(MockVcfaServer mock, VcfaChangeClient.ChangeReport report) {
        require(report != null, "runChange returned no report");

        String[][] expectedSteps = {
                {VcfaChangeClient.STEP_AUTHENTICATE, VcfaChangeClient.SUCCEEDED},
                {VcfaChangeClient.STEP_CREATE_PROJECT, VcfaChangeClient.SUCCEEDED},
                {VcfaChangeClient.STEP_REQUEST_CATALOG_ITEM, VcfaChangeClient.SUCCEEDED},
                {VcfaChangeClient.STEP_AWAIT_DEPLOYMENT, VcfaChangeClient.SUCCEEDED},
                {VcfaChangeClient.STEP_SUBMIT_RESOURCE_ACTION, VcfaChangeClient.SUCCEEDED},
                {VcfaChangeClient.STEP_AWAIT_RESOURCE_ACTION, VcfaChangeClient.FAILED},
        };
        List<VcfaChangeClient.StepOutcome> steps = report.steps();
        require(steps != null && steps.size() == expectedSteps.length,
                "report must describe the " + expectedSteps.length + " attempted steps, got "
                        + (steps == null ? "null" : String.valueOf(steps.size())));
        for (int index = 0; index < expectedSteps.length; index++) {
            VcfaChangeClient.StepOutcome step = steps.get(index);
            require(expectedSteps[index][0].equals(step.name()),
                    "step " + index + " should be " + expectedSteps[index][0] + ", got " + step.name());
            require(expectedSteps[index][1].equals(step.status()),
                    "step " + step.name() + " was reported " + step.status()
                            + " but the appliance reported " + expectedSteps[index][1]);
            require(step.detail() != null && !step.detail().isBlank(),
                    "step " + step.name() + " was reported without any detail");
        }

        require(VcfaChangeClient.FAILED.equals(report.outcome()),
                "the day-2 request reached status FAILED, so the run outcome cannot be "
                        + report.outcome());
        require(VcfaChangeClient.STEP_AWAIT_RESOURCE_ACTION.equals(report.failedStep()),
                "wrong failedStep: " + report.failedStep());
        require(mock.actionRequestId().equals(report.failedRequestId()),
                "report does not identify the failed day-2 request: " + report.failedRequestId());
        require(mock.failureDetail().equals(report.failureDetail()),
                "report must carry the server's own failure detail verbatim, got: "
                        + report.failureDetail());
        require(steps.get(5).detail().contains(mock.failureDetail()),
                "the failing step's detail must repeat what the appliance said");

        List<VcfaChangeClient.PersistedChange> persisted = report.persistedChanges();
        require(persisted != null && persisted.size() == 2,
                "the project and the deployment both survive the failure, so exactly two "
                        + "persisted changes are expected, got "
                        + (persisted == null ? "null" : String.valueOf(persisted.size())));
        assertPersisted(persisted.get(0), "project", mock.projectId(), "CREATED");
        assertPersisted(persisted.get(1), "deployment", mock.deploymentId(), "CREATE_SUCCESSFUL");
    }

    private static void assertPersisted(VcfaChangeClient.PersistedChange change, String kind,
                                        String id, String state) {
        require(kind.equals(change.kind()), "expected a persisted " + kind + ", got " + change.kind());
        require(id.equals(change.id()),
                "persisted " + kind + " must carry the identifier the appliance returned, got "
                        + change.id());
        require(state.equals(change.state()),
                "persisted " + kind + " state should be " + state + ", got " + change.state());
    }

    private static void assertJsonCall(MockVcfaServer mock, MockVcfaServer.RecordedRequest request,
                                       String method, String rawPath, String rawQuery,
                                       String exactBody) {
        assertCommon(mock, request, method, rawPath, rawQuery);
        require("application/json".equals(request.header("content-type")),
                "wrong Content-Type for " + rawPath + ": " + request.header("content-type"));
        require(exactBody.equals(request.body()),
                "wrong JSON wire body for " + rawPath + "\n  expected: " + exactBody
                        + "\n  actual:   " + request.body());
    }

    private static void assertPoll(MockVcfaServer mock, MockVcfaServer.RecordedRequest request,
                                   String rawPath) {
        assertCommon(mock, request, "GET", rawPath, null);
        require(request.body().isEmpty(), "a poll must not carry a request body: " + request.body());
        require(request.header("content-type") == null,
                "a poll must not invent a request Content-Type");
    }

    private static void assertCommon(MockVcfaServer mock, MockVcfaServer.RecordedRequest request,
                                     String method, String rawPath, String rawQuery) {
        require(method.equals(request.method()),
                "wrong HTTP method for " + rawPath + ": " + request.method());
        require(rawPath.equals(request.rawPath()),
                "wrong or misencoded path, expected " + rawPath + " got " + request.rawPath());
        if (rawQuery == null) {
            require(request.rawQuery() == null,
                    "no optional query parameter was set, so " + rawPath
                            + " must be sent without a query string, got ?" + request.rawQuery());
        } else {
            require(rawQuery.equals(request.rawQuery()),
                    "wrong query string for " + rawPath + ", expected " + rawQuery + " got "
                            + request.rawQuery());
        }
        require(("Bearer " + mock.accessToken()).equals(request.header("authorization")),
                "missing or wrong bearer credential for " + rawPath);
    }

    private static void requireOmitted(MockVcfaServer.RecordedRequest request, String field,
                                       String description) {
        require(!request.body().contains("\"" + field + "\""),
                description + " must be omitted from " + request.rawPath()
                        + ", not sent empty: " + request.body());
    }

    private static void require(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }
}
