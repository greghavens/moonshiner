import java.io.IOException;
import java.nio.file.Path;
import java.util.List;
import java.util.UUID;

/**
 * Deterministic acceptance harness for the VCF Operations inventory client.
 *
 * <p>Everything the client must get right is asserted against the mock's request log:
 * the exact raw target of every request, the exact JSON body bytes of the acquireToken
 * call, the credential header, the absence of every optional field the caller left
 * unset, the exact number of page requests, and the stable emission order. No live
 * VMware endpoint is contacted.
 */
public final class TestMain {

    private static final String ACQUIRE_PATH = "/suite-api/api/auth/token/acquire";
    private static final String RESOURCES_PATH = "/suite-api/api/resources";
    private static final String TOKEN_PREFIX = "OpsToken ";

    private static final String AUTH_SOURCE = "vIDM \"east\"";
    private static final String AUTH_SOURCE_JSON = "vIDM \\\"east\\\"";

    private static Path contract;
    private static String username;
    private static String password;
    private static String passwordJson;

    public static void main(String[] args) throws Exception {
        require(args.length == 1, "usage: TestMain <contract.json>");
        contract = Path.of(args[0]);

        String runId = UUID.randomUUID().toString();
        username = "svc-inventory-" + runId;
        password = "P@ss\"w\\rd-" + runId;
        passwordJson = "P@ss\\\"w\\\\rd-" + runId;

        wholeEstateOfOneAdapterKind();
        everyOptionalFilterSet();
        singlePageAndPercentEncoding();
        allFilterKeysUtf8AndEmptyCollection();
        authenticationFailureReportsStatus();
        failedPageIsNotAPartialResult();

        System.out.println("PASS: contract wire shape and complete stable pagination verified");
        System.exit(0);
    }

    /**
     * Eight matching resources at pageSize 3: three page requests, none past the last,
     * and every unset optional filter absent from the raw target.
     */
    private static void wholeEstateOfOneAdapterKind() throws Exception {
        try (MockVcfOpsServer mock = new MockVcfOpsServer(contract, username, password)) {
            VcfOpsInventoryClient client = new VcfOpsInventoryClient(mock.baseUri());
            String token = client.authenticate(username, password, null);

            List<VcfOpsInventoryClient.Resource> resources =
                    client.listResources(null, List.of("VMWARE"), null, 3);

            require(token.equals(mock.issuedToken()),
                    "authenticate must return the token minted by acquireToken");
            assertResources("scenario A", resources, List.of(
                    "9c8b7a65-4321-4fed-8cba-0987654321fe|cluster-café|VMWARE|"
                            + "ClusterComputeResource|GREEN",
                    "1b2c3d4e-5f60-4718-8293-a4b5c6d7e8f9|db-node-\"primary\"|VMWARE|"
                            + "VirtualMachine|YELLOW",
                    "8f7e6d5c-4b3a-4291-8807-6f5e4d3c2b1a|ds-gold|VMWARE|Datastore|GREEN",
                    "3e4d5c6b-7a89-4012-b345-6789abcdef01|esx-01.vcf.local|VMWARE|"
                            + "HostSystem|GREEN",
                    "6d5c4b3a-2918-4706-b5e4-d3c2b1a09988|esx-02.vcf.local|VMWARE|"
                            + "HostSystem|YELLOW",
                    "2a3b4c5d-6e7f-4801-9213-45566778899a|shared-name|VMWARE|VirtualMachine|GREY",
                    "e7d6c5b4-a392-4817-9f0e-1d2c3b4a5968|shared-name|VMWARE|VirtualMachine|RED",
                    "5f9a1c34-7d21-4e08-9b6f-0a1c2d3e4f50|web tier/01|VMWARE|"
                            + "VirtualMachine|GREEN"));

            List<MockVcfOpsServer.RecordedRequest> log = mock.requestLog();
            require(log.size() == 4,
                    "scenario A expected one acquireToken and three getResources calls, got "
                            + describe(log));
            assertAcquire(log.get(0),
                    "{\"username\":\"" + username + "\",\"password\":\"" + passwordJson + "\"}");
            assertPage(log.get(1), token, "adapterKind=VMWARE&page=0&pageSize=3");
            assertPage(log.get(2), token, "adapterKind=VMWARE&page=1&pageSize=3");
            assertPage(log.get(3), token, "adapterKind=VMWARE&page=2&pageSize=3");
        }
    }

    /**
     * Every optional input supplied: repeated array parameters in contract order, the
     * optional authSource present in the body, and a collection whose size divides the
     * page size exactly, so a fourth speculative page request must not be issued.
     */
    private static void everyOptionalFilterSet() throws Exception {
        try (MockVcfOpsServer mock = new MockVcfOpsServer(contract, username, password)) {
            VcfOpsInventoryClient client = new VcfOpsInventoryClient(mock.baseUri());
            String token = client.authenticate(username, password, AUTH_SOURCE);

            List<VcfOpsInventoryClient.Resource> resources = client.listResources(
                    null,
                    List.of("VMWARE", "NSXT"),
                    List.of("VirtualMachine", "HostSystem"),
                    3);

            assertResources("scenario B", resources, List.of(
                    "1b2c3d4e-5f60-4718-8293-a4b5c6d7e8f9|db-node-\"primary\"|VMWARE|"
                            + "VirtualMachine|YELLOW",
                    "3e4d5c6b-7a89-4012-b345-6789abcdef01|esx-01.vcf.local|VMWARE|"
                            + "HostSystem|GREEN",
                    "6d5c4b3a-2918-4706-b5e4-d3c2b1a09988|esx-02.vcf.local|VMWARE|"
                            + "HostSystem|YELLOW",
                    "2a3b4c5d-6e7f-4801-9213-45566778899a|shared-name|VMWARE|VirtualMachine|GREY",
                    "e7d6c5b4-a392-4817-9f0e-1d2c3b4a5968|shared-name|VMWARE|VirtualMachine|RED",
                    "5f9a1c34-7d21-4e08-9b6f-0a1c2d3e4f50|web tier/01|VMWARE|"
                            + "VirtualMachine|GREEN"));

            List<MockVcfOpsServer.RecordedRequest> log = mock.requestLog();
            require(log.size() == 3,
                    "scenario B expected one acquireToken and exactly two getResources calls, got "
                            + describe(log));
            assertAcquire(log.get(0), "{\"username\":\"" + username + "\",\"password\":\""
                    + passwordJson + "\",\"authSource\":\"" + AUTH_SOURCE_JSON + "\"}");
            String filters = "adapterKind=VMWARE&adapterKind=NSXT"
                    + "&resourceKind=VirtualMachine&resourceKind=HostSystem";
            assertPage(log.get(1), token, filters + "&page=0&pageSize=3");
            assertPage(log.get(2), token, filters + "&page=1&pageSize=3");
        }
    }

    /**
     * A blank authSource is unset, a name filter carrying reserved characters must be
     * percent-encoded, and a collection that fits in one page costs exactly one request.
     */
    private static void singlePageAndPercentEncoding() throws Exception {
        try (MockVcfOpsServer mock = new MockVcfOpsServer(contract, username, password)) {
            VcfOpsInventoryClient client = new VcfOpsInventoryClient(mock.baseUri());
            String token = client.authenticate(username, password, "   ");

            List<VcfOpsInventoryClient.Resource> resources =
                    client.listResources(List.of("web tier/01"), List.of(), List.of(), 50);

            assertResources("scenario C", resources, List.of(
                    "5f9a1c34-7d21-4e08-9b6f-0a1c2d3e4f50|web tier/01|VMWARE|"
                            + "VirtualMachine|GREEN"));

            List<MockVcfOpsServer.RecordedRequest> log = mock.requestLog();
            require(log.size() == 2,
                    "scenario C expected one acquireToken and one getResources call, got "
                            + describe(log));
            assertAcquire(log.get(0),
                    "{\"username\":\"" + username + "\",\"password\":\"" + passwordJson + "\"}");
            assertPage(log.get(1), token, "name=web%20tier%2F01&page=0&pageSize=50");
        }
    }

    /**
     * Repeated UTF-8 and reserved-character values retain caller order, all filter
     * keys retain contract order, and a zero totalCount stops after the initial page.
     */
    private static void allFilterKeysUtf8AndEmptyCollection() throws Exception {
        try (MockVcfOpsServer mock = new MockVcfOpsServer(contract, username, password)) {
            VcfOpsInventoryClient client = new VcfOpsInventoryClient(mock.baseUri());
            String token = client.authenticate(username, password, null);

            List<VcfOpsInventoryClient.Resource> resources = client.listResources(
                    List.of("missing-café", "missing /?"),
                    List.of("VMWARE", "NSXT"),
                    List.of("VirtualMachine", "HostSystem"),
                    7);

            assertResources("scenario D", resources, List.of());
            List<MockVcfOpsServer.RecordedRequest> log = mock.requestLog();
            require(log.size() == 2,
                    "scenario D expected one acquireToken and one empty page, got "
                            + describe(log));
            String filters = "name=missing-caf%C3%A9&name=missing%20%2F%3F"
                    + "&adapterKind=VMWARE&adapterKind=NSXT"
                    + "&resourceKind=VirtualMachine&resourceKind=HostSystem";
            assertPage(log.get(1), token, filters + "&page=0&pageSize=7");
        }
    }

    /** The non-2xx rule applies to acquireToken as well as to paginated GETs. */
    private static void authenticationFailureReportsStatus() throws Exception {
        try (MockVcfOpsServer mock = new MockVcfOpsServer(contract, username, password)) {
            VcfOpsInventoryClient client = new VcfOpsInventoryClient(mock.baseUri());
            String wrongPassword = password + "-wrong";

            IOException failure = null;
            try {
                client.authenticate(username, wrongPassword, null);
            } catch (IOException expected) {
                failure = expected;
            }
            require(failure != null,
                    "a 401 from acquireToken must raise java.io.IOException");
            require(failure.getMessage() != null && failure.getMessage().contains("401"),
                    "the authentication failure must report the HTTP status: "
                            + failure.getMessage());

            List<MockVcfOpsServer.RecordedRequest> log = mock.requestLog();
            require(log.size() == 1,
                    "scenario E expected exactly the failing acquireToken call, got "
                            + describe(log));
            assertAcquire(log.get(0), "{\"username\":\"" + username + "\",\"password\":\""
                    + passwordJson + "-wrong\"}");
        }
    }

    /** A failed page is an error, never a truncated collection. */
    private static void failedPageIsNotAPartialResult() throws Exception {
        try (MockVcfOpsServer mock = new MockVcfOpsServer(contract, username, password, 1)) {
            VcfOpsInventoryClient client = new VcfOpsInventoryClient(mock.baseUri());
            String token = client.authenticate(username, password, null);

            IOException failure = null;
            try {
                client.listResources(null, List.of("VMWARE"), null, 3);
            } catch (IOException expected) {
                failure = expected;
            }
            require(failure != null,
                    "a 503 on page 1 must raise java.io.IOException, not a partial collection");
            require(failure.getMessage() != null && failure.getMessage().contains("503"),
                    "the failure must report the HTTP status: " + failure.getMessage());

            List<MockVcfOpsServer.RecordedRequest> log = mock.requestLog();
            require(log.size() == 3,
                    "scenario F expected acquireToken, page 0 and the failing page 1, got "
                            + describe(log));
            assertPage(log.get(1), token, "adapterKind=VMWARE&page=0&pageSize=3");
            assertPage(log.get(2), token, "adapterKind=VMWARE&page=1&pageSize=3");
        }
    }

    // ------------------------------------------------------------- assertions

    private static void assertAcquire(MockVcfOpsServer.RecordedRequest request, String exactBody) {
        require("POST".equals(request.method()),
                "acquireToken must be a POST, got " + request.method());
        require(ACQUIRE_PATH.equals(request.rawPath()),
                "wrong acquireToken target: " + request.rawPath());
        require(request.rawQuery() == null,
                "acquireToken declares no query parameter: ?" + request.rawQuery());
        require("application/json".equals(request.header("content-type")),
                "acquireToken Content-Type must be application/json, got "
                        + request.header("content-type"));
        require("application/json".equals(request.header("accept")),
                "acquireToken Accept must be application/json, got " + request.header("accept"));
        require(!request.hasHeader("authorization"),
                "acquireToken is declared with no security requirement, "
                        + "so it must carry no Authorization header");
        require(exactBody.equals(request.body()),
                "wrong acquireToken body bytes.\n  expected: " + exactBody
                        + "\n  actual:   " + request.body());
        require(!request.body().contains("\"authSource\":\"\""),
                "an unset authSource was sent as an empty string");
        require(!request.body().contains("null"),
                "an unset optional member was sent as null");
    }

    private static void assertPage(MockVcfOpsServer.RecordedRequest request, String token,
                                   String exactRawQuery) {
        require("GET".equals(request.method()),
                "getResources must be a GET, got " + request.method());
        require(RESOURCES_PATH.equals(request.rawPath()),
                "wrong getResources target: " + request.rawPath());
        require(exactRawQuery.equals(request.rawQuery()),
                "wrong getResources query string.\n  expected: " + exactRawQuery
                        + "\n  actual:   " + request.rawQuery());
        require((TOKEN_PREFIX + token).equals(request.header("authorization")),
                "getResources must carry the acquired credential, got "
                        + request.header("authorization"));
        require("application/json".equals(request.header("accept")),
                "getResources Accept must be application/json, got " + request.header("accept"));
        require(!request.hasHeader("content-type"),
                "a bodyless GET must not declare a request Content-Type");
        require(request.body().isEmpty(), "getResources must not carry a request body");
        for (String unset : List.of("regex", "resourceId", "resourceHealth", "resourceState",
                "resourceStatus", "collectorId", "collectorName", "adapterInstanceId",
                "parentId", "credentialId", "propertyName", "propertyValue", "statKey",
                "includeRelated", "maintenanceScheduleId", "recentlyAdded")) {
            require(!request.rawQuery().contains(unset + "="),
                    "optional parameter " + unset + " was sent although it was never set");
        }
    }

    private static void assertResources(String scenario,
                                        List<VcfOpsInventoryClient.Resource> actual,
                                        List<String> expected) {
        require(actual != null, scenario + ": listResources returned null");
        StringBuilder rendered = new StringBuilder();
        for (VcfOpsInventoryClient.Resource resource : actual) {
            rendered.append(resource.identifier()).append('|')
                    .append(resource.name()).append('|')
                    .append(resource.adapterKindKey()).append('|')
                    .append(resource.resourceKindKey()).append('|')
                    .append(resource.resourceHealth()).append('\n');
        }
        StringBuilder wanted = new StringBuilder();
        for (String row : expected) {
            wanted.append(row).append('\n');
        }
        require(wanted.toString().contentEquals(rendered),
                scenario + ": wrong collection or order.\n  expected:\n" + wanted
                        + "  actual:\n" + rendered);
    }

    private static String describe(List<MockVcfOpsServer.RecordedRequest> log) {
        StringBuilder out = new StringBuilder(log.size() + " request(s):");
        for (MockVcfOpsServer.RecordedRequest request : log) {
            out.append("\n  ").append(request.method()).append(' ').append(request.rawPath());
            if (request.rawQuery() != null) {
                out.append('?').append(request.rawQuery());
            }
        }
        return out.toString();
    }

    private static void require(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }
}
