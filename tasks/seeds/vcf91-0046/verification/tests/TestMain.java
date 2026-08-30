import java.nio.file.Path;
import java.util.List;

public final class TestMain {
    public static void main(String[] args) throws Exception {
        try (ContractMock mock = new ContractMock(Path.of("docs", "contract.json"))) {
            VcfSddcClient client =
                    new VcfSddcClient(mock.baseUri(), "fixture-user", "fixture-password");

            List<VcfSddcClient.Domain> domains = client.listDomainsSorted();
            assertEquals(
                    List.of(
                            new VcfSddcClient.Domain("domain-1", "Alpha"),
                            new VcfSddcClient.Domain("domain-2", "Bravo"),
                            new VcfSddcClient.Domain("domain-3", "Bravo"),
                            new VcfSddcClient.Domain("domain-4", "Zulu")),
                    domains,
                    "complete collection must be globally sorted by name then id");

            boolean immutable = false;
            try {
                domains.add(new VcfSddcClient.Domain("other", "Other"));
            } catch (UnsupportedOperationException expected) {
                immutable = true;
            }
            assertTrue(immutable, "returned collection must be immutable");

            List<ContractMock.RequestRecord> requests = mock.requests();
            assertEquals(5, requests.size(), "unexpected request count");
            assertRequest(requests.get(0), "createToken", "POST", "/v1/tokens", null, 201);
            assertRequest(requests.get(1), "getDomains", "GET", "/v1/domains",
                    "Bearer access-expiring", 200);
            assertRequest(requests.get(2), "getDomains", "GET", "/v1/domains",
                    "Bearer access-expiring", 401);
            assertRequest(requests.get(3), "refreshAccessToken", "PATCH",
                    "/v1/tokens/access-token/refresh", null, 200);
            assertRequest(requests.get(4), "getDomains", "GET", "/v1/domains",
                    "Bearer access-renewed", 200);

            assertEquals("pageNumber=0&pageSize=2", normalizedQuery(requests.get(1).rawQuery()),
                    "first page query");
            assertEquals("pageNumber=1&pageSize=2", normalizedQuery(requests.get(2).rawQuery()),
                    "expired page query");
            assertEquals(requests.get(2).rawQuery(), requests.get(4).rawQuery(),
                    "the exact failed page must be retried after refresh");
            assertEquals("\"refresh-token-1\"", requests.get(3).body().trim(),
                    "refresh token id must be encoded as a JSON string");
        }

        System.out.println("PASS: VCF SDDC client contract");
    }

    private static void assertRequest(
            ContractMock.RequestRecord request,
            String operationId,
            String method,
            String path,
            String authorization,
            int status) {
        assertEquals(operationId, request.operationId(), "operation id");
        assertEquals(method, request.method(), operationId + " method");
        assertEquals(path, request.path(), operationId + " path");
        if (authorization != null) {
            assertEquals(authorization, request.authorization(), operationId + " authorization");
        }
        assertEquals(status, request.status(), operationId + " status");
    }

    private static String normalizedQuery(String rawQuery) {
        if (rawQuery == null) {
            return null;
        }
        return java.util.Arrays.stream(rawQuery.split("&"))
                .sorted()
                .collect(java.util.stream.Collectors.joining("&"));
    }

    private static void assertTrue(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }

    private static void assertEquals(Object expected, Object actual, String message) {
        if (!java.util.Objects.equals(expected, actual)) {
            throw new AssertionError(
                    message + ": expected <" + expected + "> but was <" + actual + ">");
        }
    }
}
