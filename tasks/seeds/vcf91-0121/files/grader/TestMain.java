import java.io.IOException;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;

public final class TestMain {
    private static final String SESSION_ID = "session-test-9.1";

    private static final String EXPECTED_JSON_LINES = """
            {"category_id":"cat-a1","name":"Alpha","description":"first alpha\\nline","cardinality":"MULTIPLE","associable_types":["Datastore"],"used_by":[]}
            {"category_id":"cat-a2","name":"Alpha","description":"second alpha","cardinality":"SINGLE","associable_types":[],"used_by":["com.acme.ops"]}
            {"category_id":"cat-b","name":"Beta","description":"middle","cardinality":"SINGLE","associable_types":[],"used_by":[]}
            {"category_id":"cat-q","name":"Quote \\"Ops\\"","description":"path C:\\\\inventory","cardinality":"SINGLE","associable_types":["Folder"],"used_by":["team-a","team-b"]}
            {"category_id":"cat-z","name":"Zulu","description":"last page-order item","cardinality":"MULTIPLE","associable_types":["VirtualMachine"],"used_by":[]}
            {"category_id":"cat-omega","name":"Ωmega","description":"unicode name","cardinality":"MULTIPLE","associable_types":["VirtualMachine","Datastore"],"used_by":[]}
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
                            "Alpha\u0000cat-a1",
                            "Alpha\u0000cat-a2",
                            "Beta\u0000cat-b",
                            "Quote \"Ops\"\u0000cat-q",
                            "Zulu\u0000cat-z",
                            "Ωmega\u0000cat-omega"),
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
                "marker=next+marker%2F2%3Fafter%3Dcat-z%26full%3Dtrue%2Bkeep",
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
