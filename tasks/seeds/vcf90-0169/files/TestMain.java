import java.io.IOException;
import java.lang.reflect.Method;
import java.lang.reflect.Modifier;
import java.lang.reflect.RecordComponent;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Set;

public final class TestMain {
    private static final String NAME = "edge \"gateway\"\\west";
    private static final String CONTENT =
            "formatVersion: 1\ninputs:\n  owner: \"platform\"\n";
    private static final String PROJECT = "project/blue";
    private static final String VALIDATE_BODY =
            "{\"content\":\"formatVersion: 1\\ninputs:\\n  owner: \\\"platform\\\"\\n\",\"projectId\":\"project/blue\"}";
    private static final String CREATE_BODY_WITHOUT_OPTIONALS =
            "{\"content\":\"formatVersion: 1\\ninputs:\\n  owner: \\\"platform\\\"\\n\",\"name\":\"edge \\\"gateway\\\"\\\\west\",\"projectId\":\"project/blue\"}";
    private static final String CREATE_BODY_WITH_DESCRIPTION =
            "{\"content\":\"formatVersion: 1\\nresources:\\n  disk: café\\n\","
                    + "\"description\":\"primary\\nstorage\","
                    + "\"name\":\"db\\nnode\\t\\\"east\\\"\\\\rack\","
                    + "\"projectId\":\"project/green\"}";
    private static final String ALTERNATE_VALIDATE_BODY =
            "{\"content\":\"formatVersion: 1\\nresources:\\n  disk: café\\n\","
                    + "\"projectId\":\"project/green\"}";

    private TestMain() {}

    public static void main(String[] args) throws Exception {
        verifyReferenceProvenance();
        validFlowOmitsUnsetOptionals();
        validFlowIncludesSetOptionals();
        rejectedPrecheckGatesMutation();
        unsuccessfulPrecheckGatesMutation();
        wrongValidationSuccessStatusGatesMutation();
        missingDecisionPrecheckGatesMutation();
        nonBooleanDecisionPrecheckGatesMutation();
        malformedPrecheckGatesMutation();
        nestedTrueCannotOverrideTopLevelRejection();
        wrongCreationSuccessStatusFails();
        missingCreationFieldFails();
        clientSurfaceMatchesContract();
        System.out.println("PASS: VCF Automation contract, wire shape, and precheck gate");
    }

    private static void validFlowOmitsUnsetOptionals() throws Exception {
        try (ContractMockServer mock = new ContractMockServer(
                ContractMockServer.PrecheckOutcome.VALID)) {
            check("127.0.0.1".equals(mock.baseUri().getHost()),
                    "mock must bind to loopback");
            AutomationClient client = new AutomationClient(mock.baseUri(), "unit-token");
            AutomationClient.BlueprintLink link = client.validateAndCreate(
                    new AutomationClient.BlueprintDraft(
                            NAME, CONTENT, PROJECT, null, null));

            equal("bp-123", link.id(), "created blueprint id");
            equal("/blueprint/api/blueprints/bp-123", link.selfLink(),
                    "created blueprint selfLink");
            List<ContractMockServer.RequestLogEntry> log = mock.requests();
            equal(2, log.size(), "valid request count");
            assertCommon(log.get(0), "/blueprint/api/blueprint-validation", null);
            equalJson(VALIDATE_BODY, log.get(0).bodyUtf8(), "validation body");
            assertCommon(log.get(1), "/blueprint/api/blueprints", null);
            equalJson(CREATE_BODY_WITHOUT_OPTIONALS, log.get(1).bodyUtf8(),
                    "create body without optionals");

            for (ContractMockServer.RequestLogEntry request : log) {
                check(!request.bodyUtf8().contains("\"description\""),
                        "unset description must be omitted");
                check(!request.bodyUtf8().contains("\"apiVersion\""),
                        "apiVersion is a query field, not a body field");
                check(!request.bodyUtf8().contains("\"blueprintId\""),
                        "unset blueprintId must be omitted");
                check(!request.bodyUtf8().contains("\"blueprintVersion\""),
                        "unset blueprintVersion must be omitted");
                check(!request.bodyUtf8().contains("\"inputs\""),
                        "unset request inputs must be omitted");
                check(!request.bodyUtf8().contains("\"requestScopeOrg\""),
                        "response-only/default blueprint fields must be omitted");
            }
        }
    }

    private static void validFlowIncludesSetOptionals() throws Exception {
        try (ContractMockServer mock = new ContractMockServer(
                ContractMockServer.PrecheckOutcome.VALID,
                ContractMockServer.CreationOutcome.ALTERNATE_CREATED)) {
            AutomationClient client = new AutomationClient(mock.baseUri(), "second-token");
            AutomationClient.BlueprintLink link = client.validateAndCreate(
                    new AutomationClient.BlueprintDraft(
                            "db\nnode\t\"east\"\\rack",
                            "formatVersion: 1\nresources:\n  disk: café\n",
                            "project/green", "primary\nstorage", "2021-07-15"));

            equal("bp-456", link.id(), "alternate created blueprint id");
            equal("/blueprint/api/blueprints/bp-456?label=\"edge\"",
                    link.selfLink(), "escaped created blueprint selfLink");

            List<ContractMockServer.RequestLogEntry> log = mock.requests();
            equal(2, log.size(), "set-optionals request count");
            assertCommon(log.get(0), "/blueprint/api/blueprint-validation",
                    "apiVersion=2021-07-15", "second-token");
            equalJson(ALTERNATE_VALIDATE_BODY, log.get(0).bodyUtf8(),
                    "validation body with query option");
            assertCommon(log.get(1), "/blueprint/api/blueprints",
                    "apiVersion=2021-07-15", "second-token");
            equalJson(CREATE_BODY_WITH_DESCRIPTION, log.get(1).bodyUtf8(),
                    "create body with description");
        }
    }

    private static void rejectedPrecheckGatesMutation() throws Exception {
        try (ContractMockServer mock = new ContractMockServer(
                ContractMockServer.PrecheckOutcome.REJECTED)) {
            AutomationClient client = new AutomationClient(mock.baseUri(), "unit-token");
            boolean rejected = false;
            try {
                client.validateAndCreate(new AutomationClient.BlueprintDraft(
                        NAME, CONTENT, PROJECT, null, null));
            } catch (AutomationClient.ValidationException expected) {
                rejected = true;
                check(expected.getMessage().contains("unknown resource type"),
                        "validation diagnostics should remain visible");
            }
            check(rejected, "valid:false must raise ValidationException");
            assertOnlyPrecheck(mock.requests(), "rejected validation");
        }
    }

    private static void unsuccessfulPrecheckGatesMutation() throws Exception {
        try (ContractMockServer mock = new ContractMockServer(
                ContractMockServer.PrecheckOutcome.HTTP_ERROR)) {
            AutomationClient client = new AutomationClient(mock.baseUri(), "unit-token");
            boolean failed = false;
            try {
                client.validateAndCreate(new AutomationClient.BlueprintDraft(
                        NAME, CONTENT, PROJECT, null, null));
            } catch (IOException expected) {
                failed = true;
            }
            check(failed, "non-successful validation must fail");
            assertOnlyPrecheck(mock.requests(), "HTTP validation failure");
        }
    }

    private static void malformedPrecheckGatesMutation() throws Exception {
        try (ContractMockServer mock = new ContractMockServer(
                ContractMockServer.PrecheckOutcome.MALFORMED)) {
            AutomationClient client = new AutomationClient(mock.baseUri(), "unit-token");
            boolean failed = false;
            try {
                client.validateAndCreate(new AutomationClient.BlueprintDraft(
                        NAME, CONTENT, PROJECT, null, null));
            } catch (IOException expected) {
                failed = true;
            }
            check(failed, "syntactically malformed validation JSON must fail");
            assertOnlyPrecheck(mock.requests(), "malformed validation");
        }
    }

    private static void missingDecisionPrecheckGatesMutation() throws Exception {
        try (ContractMockServer mock = new ContractMockServer(
                ContractMockServer.PrecheckOutcome.MISSING_VALID)) {
            AutomationClient client = new AutomationClient(mock.baseUri(), "unit-token");
            boolean failed = false;
            try {
                client.validateAndCreate(new AutomationClient.BlueprintDraft(
                        NAME, CONTENT, PROJECT, null, null));
            } catch (IOException expected) {
                failed = true;
            }
            check(failed, "validation without a boolean valid member must fail");
            assertOnlyPrecheck(mock.requests(), "missing validation decision");
        }
    }

    private static void nonBooleanDecisionPrecheckGatesMutation() throws Exception {
        try (ContractMockServer mock = new ContractMockServer(
                ContractMockServer.PrecheckOutcome.WRONG_VALID_TYPE)) {
            AutomationClient client = new AutomationClient(mock.baseUri(), "unit-token");
            boolean failed = false;
            try {
                client.validateAndCreate(new AutomationClient.BlueprintDraft(
                        NAME, CONTENT, PROJECT, null, null));
            } catch (IOException expected) {
                failed = true;
            }
            check(failed, "a string valid member must not pass the boolean gate");
            assertOnlyPrecheck(mock.requests(), "non-boolean validation decision");
        }
    }

    private static void wrongValidationSuccessStatusGatesMutation() throws Exception {
        try (ContractMockServer mock = new ContractMockServer(
                ContractMockServer.PrecheckOutcome.WRONG_SUCCESS_STATUS)) {
            AutomationClient client = new AutomationClient(mock.baseUri(), "unit-token");
            boolean failed = false;
            try {
                client.validateAndCreate(new AutomationClient.BlueprintDraft(
                        NAME, CONTENT, PROJECT, null, null));
            } catch (IOException expected) {
                failed = true;
            }
            check(failed, "validation HTTP 201 must not be accepted as HTTP 200");
            assertOnlyPrecheck(mock.requests(), "wrong validation success status");
        }
    }

    private static void nestedTrueCannotOverrideTopLevelRejection() throws Exception {
        try (ContractMockServer mock = new ContractMockServer(
                ContractMockServer.PrecheckOutcome.NESTED_TRUE_THEN_REJECTED)) {
            AutomationClient client = new AutomationClient(mock.baseUri(), "unit-token");
            boolean rejected = false;
            try {
                client.validateAndCreate(new AutomationClient.BlueprintDraft(
                        NAME, CONTENT, PROJECT, null, null));
            } catch (AutomationClient.ValidationException expected) {
                rejected = true;
            }
            check(rejected, "only the top-level valid member controls the gate");
            assertOnlyPrecheck(mock.requests(), "nested true validation");
        }
    }

    private static void wrongCreationSuccessStatusFails() throws Exception {
        try (ContractMockServer mock = new ContractMockServer(
                ContractMockServer.PrecheckOutcome.VALID,
                ContractMockServer.CreationOutcome.WRONG_SUCCESS_STATUS)) {
            AutomationClient client = new AutomationClient(mock.baseUri(), "unit-token");
            boolean failed = false;
            try {
                client.validateAndCreate(new AutomationClient.BlueprintDraft(
                        NAME, CONTENT, PROJECT, null, null));
            } catch (IOException expected) {
                failed = true;
            }
            check(failed, "creation HTTP 200 must not be accepted as HTTP 201");
            equal(2, mock.requests().size(), "wrong creation success status request count");
        }
    }

    private static void missingCreationFieldFails() throws Exception {
        try (ContractMockServer mock = new ContractMockServer(
                ContractMockServer.PrecheckOutcome.VALID,
                ContractMockServer.CreationOutcome.MISSING_REQUIRED_FIELD)) {
            AutomationClient client = new AutomationClient(mock.baseUri(), "unit-token");
            boolean failed = false;
            try {
                client.validateAndCreate(new AutomationClient.BlueprintDraft(
                        NAME, CONTENT, PROJECT, null, null));
            } catch (IOException expected) {
                failed = true;
            }
            check(failed, "a creation response without selfLink must fail");
            equal(2, mock.requests().size(), "missing creation field request count");
        }
    }

    private static void clientSurfaceMatchesContract() throws Exception {
        AutomationClient.class.getConstructor(URI.class, String.class);
        Method workflow = AutomationClient.class.getMethod(
                "validateAndCreate", AutomationClient.BlueprintDraft.class);
        equal(AutomationClient.BlueprintLink.class, workflow.getReturnType(),
                "workflow return type");
        check(!Modifier.isStatic(workflow.getModifiers()),
                "validateAndCreate must be an instance method");
        equal(Set.of(IOException.class, InterruptedException.class,
                        AutomationClient.ValidationException.class),
                Set.of(workflow.getExceptionTypes()), "workflow checked exceptions");

        assertPublicNestedRecord(AutomationClient.BlueprintDraft.class);
        RecordComponent[] draft =
                AutomationClient.BlueprintDraft.class.getRecordComponents();
        equal(5, draft.length, "BlueprintDraft component count");
        assertComponent(draft[0], "name", String.class);
        assertComponent(draft[1], "content", String.class);
        assertComponent(draft[2], "projectId", String.class);
        assertComponent(draft[3], "description", String.class);
        assertComponent(draft[4], "apiVersion", String.class);

        assertPublicNestedRecord(AutomationClient.BlueprintLink.class);
        RecordComponent[] link = AutomationClient.BlueprintLink.class.getRecordComponents();
        equal(2, link.length, "BlueprintLink component count");
        assertComponent(link[0], "id", String.class);
        assertComponent(link[1], "selfLink", String.class);

        int exceptionModifiers = AutomationClient.ValidationException.class.getModifiers();
        check(Modifier.isPublic(exceptionModifiers)
                        && Modifier.isStatic(exceptionModifiers),
                "ValidationException must be a public static nested type");
        check(Exception.class.isAssignableFrom(AutomationClient.ValidationException.class),
                "ValidationException must extend Exception");
        check(!RuntimeException.class.isAssignableFrom(
                        AutomationClient.ValidationException.class),
                "ValidationException must be checked");
    }

    private static void assertPublicNestedRecord(Class<?> type) {
        check(type.isRecord(), type.getSimpleName() + " must be a record");
        check(Modifier.isPublic(type.getModifiers())
                        && Modifier.isStatic(type.getModifiers()),
                type.getSimpleName() + " must be a public nested record");
    }

    private static void assertComponent(
            RecordComponent component, String name, Class<?> type) {
        equal(name, component.getName(), "record component name");
        equal(type, component.getType(), name + " component type");
    }

    private static void assertOnlyPrecheck(
            List<ContractMockServer.RequestLogEntry> log, String label) {
        equal(1, log.size(), label + " request count");
        assertCommon(log.get(0), "/blueprint/api/blueprint-validation", null);
        equalJson(VALIDATE_BODY, log.get(0).bodyUtf8(), label + " body");
    }

    private static void assertCommon(ContractMockServer.RequestLogEntry request,
                                     String path, String rawQuery) {
        assertCommon(request, path, rawQuery, "unit-token");
    }

    private static void assertCommon(ContractMockServer.RequestLogEntry request,
                                     String path, String rawQuery,
                                     String bearerToken) {
        equal("POST", request.method(), "HTTP method for " + path);
        equal(path, request.rawPath(), "raw path");
        equal(rawQuery, request.rawQuery(), "raw query");
        equal("Bearer " + bearerToken, request.header("Authorization"),
                "Authorization header");
        equal("application/json", request.header("Content-Type"),
                "Content-Type header");
        equal("application/json", request.header("Accept"), "Accept header");
    }

    private static void verifyReferenceProvenance() throws IOException {
        String contract = Files.readString(Path.of("docs", "contract.json"),
                StandardCharsets.UTF_8);
        String sources = Files.readString(Path.of("docs", "official_sources.json"),
                StandardCharsets.UTF_8);
        check(contract.contains("\"sourceKind\": \"reference-documentation\""),
                "contract must identify reference documentation");
        check(contract.contains("\"publishedSpecification\": false"),
                "contract must state it is not a published specification");
        equal(2, occurrences(contract, "\"operationId\""),
                "contract operation count");
        equal(2, occurrences(sources, "\"operation\""),
                "official source operation count");
        equal(3, occurrences(sources, "\"fetchedOn\": \"2026-08-13\""),
                "source fetch dates");
        equal(2, occurrences(sources, "https://developer.broadcom.com/xapis/"),
                "authoritative page URLs");
    }

    private static int occurrences(String value, String needle) {
        int count = 0;
        for (int at = 0; (at = value.indexOf(needle, at)) >= 0; at += needle.length()) {
            count++;
        }
        return count;
    }

    private static void check(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }

    private static void equal(Object expected, Object actual, String label) {
        if (expected == null ? actual != null : !expected.equals(actual)) {
            throw new AssertionError(label + ": expected <" + expected
                    + "> but was <" + actual + ">");
        }
    }

    private static void equalJson(String expected, String actual, String label) {
        equal(compactJson(expected), compactJson(actual), label);
    }

    private static String compactJson(String json) {
        StringBuilder compact = new StringBuilder(json.length());
        boolean inString = false;
        boolean escaped = false;
        for (int i = 0; i < json.length(); i++) {
            char c = json.charAt(i);
            if (inString) {
                compact.append(c);
                if (escaped) {
                    escaped = false;
                } else if (c == '\\') {
                    escaped = true;
                } else if (c == '"') {
                    inString = false;
                }
            } else if (c == '"') {
                compact.append(c);
                inString = true;
            } else if (c != ' ' && c != '\t' && c != '\n' && c != '\r') {
                compact.append(c);
            }
        }
        return compact.toString();
    }
}
