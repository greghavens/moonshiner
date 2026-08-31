import java.io.IOException;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;

public final class TestMain {
    private static final String SESSION_ID = "0123456789abcdef0123456789abcdef";

    private static final String EXPECTED_JSON_LINES = """
            {"category_id":"urn:vmomi:InventoryServiceCategory:11111111-1111-4111-8111-111111111111:GLOBAL","name":"Alpha","description":"first alpha\\nline","cardinality":"MULTIPLE","associable_types":["urn:vim25:Datastore"],"used_by":[]}
            {"category_id":"urn:vmomi:InventoryServiceCategory:22222222-2222-4222-8222-222222222222:GLOBAL","name":"Alpha","description":"second alpha","cardinality":"SINGLE","associable_types":[],"used_by":[]}
            {"category_id":"urn:vmomi:InventoryServiceCategory:33333333-3333-4333-8333-333333333333:GLOBAL","name":"Beta","description":"middle","cardinality":"SINGLE","associable_types":[],"used_by":[]}
            {"category_id":"urn:vmomi:InventoryServiceCategory:44444444-4444-4444-8444-444444444444:GLOBAL","name":"Quote \\"Ops\\"","description":"path C:\\\\inventory","cardinality":"SINGLE","associable_types":["urn:vim25:Folder"],"used_by":[]}
            {"category_id":"urn:vmomi:InventoryServiceCategory:66666666-6666-4666-8666-666666666666:GLOBAL","name":"Zulu","description":"last page-order item","cardinality":"MULTIPLE","associable_types":["urn:vim25:VirtualMachine"],"used_by":[]}
            {"category_id":"urn:vmomi:InventoryServiceCategory:55555555-5555-4555-8555-555555555555:GLOBAL","name":"Ωmega","description":"unicode name","cardinality":"MULTIPLE","associable_types":["urn:vim25:VirtualMachine","urn:vim25:Datastore"],"used_by":[]}
            """;

    public static void main(String[] arguments) throws Exception {
        if (arguments.length != 1) {
            throw new IllegalArgumentException("usage: TestMain docs/contract.json");
        }

        List<String> failures = new ArrayList<>();
        Path contractPath = Path.of(arguments[0]);
        verifySuccessfulMode(
                contractPath, MockVcenterServer.ResponseMode.PAGINATED,
                "absent final marker", failures);
        verifySuccessfulMode(
                contractPath, MockVcenterServer.ResponseMode.PAGINATED_NULL_MARKER,
                "null final marker", failures);
        verifySuccessfulMode(
                contractPath, MockVcenterServer.ResponseMode.PAGINATED_EMPTY_MARKER,
                "empty final marker", failures);
        verifyFailureMode(
                contractPath, MockVcenterServer.ResponseMode.HTTP_ERROR,
                "non-2xx response", failures);
        verifyFailureMode(
                contractPath, MockVcenterServer.ResponseMode.MALFORMED_RESPONSE,
                "malformed response", failures);
        verifyCategorySurface(failures);

        if (!failures.isEmpty()) {
            System.err.println("FAIL (" + failures.size() + " assertions)");
            for (String failure : failures) {
                System.err.println(" - " + failure);
            }
            System.exit(1);
        }
        System.out.println("PASS: complete pagination, stable output, and exact wire shape");
    }

    private static void verifySuccessfulMode(
            Path contractPath,
            MockVcenterServer.ResponseMode mode,
            String label,
            List<String> failures) throws Exception {
        try (MockVcenterServer mock = new MockVcenterServer(contractPath, mode)) {
            VCenterCategoryClient client = mode == MockVcenterServer.ResponseMode.PAGINATED
                    ? new VCenterCategoryClient(mock.apiBaseUri(), SESSION_ID)
                    : new VCenterCategoryClient(
                            mock.apiBaseUri(), SESSION_ID,
                            java.net.http.HttpClient.newHttpClient());
            List<VCenterCategoryClient.Category> categories = List.of();
            try {
                categories = client.listAllCategories();
            } catch (Exception exception) {
                failures.add(label + " list call failed: " + exception);
            }
            List<String> order = new ArrayList<>();
            for (VCenterCategoryClient.Category category : categories) {
                order.add(category.name() + "\u0000" + category.categoryId());
            }
            checkEquals(label + " complete returned collection order",
                    List.of(
                            "Alpha\u0000urn:vmomi:InventoryServiceCategory:11111111-1111-4111-8111-111111111111:GLOBAL",
                            "Alpha\u0000urn:vmomi:InventoryServiceCategory:22222222-2222-4222-8222-222222222222:GLOBAL",
                            "Beta\u0000urn:vmomi:InventoryServiceCategory:33333333-3333-4333-8333-333333333333:GLOBAL",
                            "Quote \"Ops\"\u0000urn:vmomi:InventoryServiceCategory:44444444-4444-4444-8444-444444444444:GLOBAL",
                            "Zulu\u0000urn:vmomi:InventoryServiceCategory:66666666-6666-4666-8666-666666666666:GLOBAL",
                            "Ωmega\u0000urn:vmomi:InventoryServiceCategory:55555555-5555-4555-8555-555555555555:GLOBAL"),
                    order, failures);

            StringBuilder output = new StringBuilder();
            try {
                client.writeAllCategories(output);
            } catch (Exception exception) {
                failures.add(label + " client call failed: " + exception);
            }

            checkEquals(label + " stable complete JSON Lines export",
                    EXPECTED_JSON_LINES, output.toString(), failures);
            verifyWireLog(label, mock.requestLogSnapshot(), failures);
        }
    }

    private static void verifyFailureMode(
            Path contractPath,
            MockVcenterServer.ResponseMode mode,
            String label,
            List<String> failures) throws Exception {
        try (MockVcenterServer mock = new MockVcenterServer(contractPath, mode)) {
            VCenterCategoryClient client =
                    new VCenterCategoryClient(mock.apiBaseUri(), SESSION_ID);
            try {
                client.listAllCategories();
                failures.add(label + ": expected IOException");
            } catch (IOException expected) {
                // Expected contract-facing failure.
            }
        }
    }

    private static void verifyWireLog(
            String modeLabel,
            List<MockVcenterServer.LoggedRequest> log,
            List<String> failures) {
        checkEquals(modeLabel + " request count", 6, log.size(), failures);

        String[] expectedQueries = {
                null,
                MockVcenterServer.liveShapedMarkerQuery(),
                "marker=final%2Bpage%2F3"
        };
        for (int index = 0; index < log.size(); index++) {
            MockVcenterServer.LoggedRequest request = log.get(index);
            String label = modeLabel + " request " + (index + 1);
            checkEquals(label + " operationId",
                    "Vcenter.Tagging.Categories_list", request.operationId(), failures);
            checkEquals(label + " method", "GET", request.method(), failures);
            checkEquals(label + " raw path",
                    "/api/vcenter/tagging/categories", request.rawPath(), failures);
            checkEquals(label + " raw query",
                    expectedQueries[index % expectedQueries.length],
                    request.rawQuery(), failures);
            checkEquals(label + " session header",
                    SESSION_ID, request.sessionHeader(), failures);
            checkEquals(label + " Accept header",
                    "application/json", request.acceptHeader(), failures);
            checkEquals(label + " Content-Type header",
                    null, request.contentTypeHeader(), failures);
            checkEquals(label + " body size", 0, request.bodyBytes(), failures);
        }
    }

    private static void verifyCategorySurface(List<String> failures) {
        VCenterCategoryClient.Category category = new VCenterCategoryClient.Category(
                "category-id",
                "name",
                "description",
                "SINGLE",
                List.of("VirtualMachine"),
                List.of("consumer"));
        checkEquals("Category.categoryId accessor",
                "category-id", category.categoryId(), failures);
        checkEquals("Category.name accessor", "name", category.name(), failures);
        checkEquals("Category.description accessor",
                "description", category.description(), failures);
        checkEquals("Category.cardinality accessor",
                "SINGLE", category.cardinality(), failures);
        checkEquals("Category.associableTypes accessor",
                List.of("VirtualMachine"), category.associableTypes(), failures);
        checkEquals("Category.usedBy accessor",
                List.of("consumer"), category.usedBy(), failures);
    }

    private static void checkEquals(
            String label, Object expected, Object actual, List<String> failures) {
        if (!java.util.Objects.equals(expected, actual)) {
            failures.add(label + ": expected <" + expected + "> but was <" + actual + ">");
        }
    }
}
