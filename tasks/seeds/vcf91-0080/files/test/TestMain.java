import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;

public final class TestMain {
    private static final String EXPECTED_PATCH_PATH =
            "/policy/api/v1/infra/domains/tenant%20a/groups/web%2Fapps";
    private static final String EXPECTED_BODY =
            "{\"display_name\":\"Web workloads\",\"expression\":["
                    + "{\"resource_type\":\"Condition\","
                    + "\"member_type\":\"VirtualMachine\","
                    + "\"key\":\"Tag\","
                    + "\"operator\":\"EQUALS\","
                    + "\"value\":\"line\\n\\\"blue\\\"\\\\prod\"}]}";
    private static final String EXPECTED_READ_BODY =
            "{\"id\":\"web/apps\",\"display_name\":\"Web workloads\","
                    + "\"resource_type\":\"Group\","
                    + "\"path\":\"/infra/domains/tenant a/groups/web/apps\"}";

    private static final class RotatingTokens implements NsxPolicyClient.AccessTokenProvider {
        private String current = "token-old";
        private int refreshes;

        @Override
        public String currentToken() {
            return current;
        }

        @Override
        public String refreshToken() {
            refreshes++;
            current = "token-fresh";
            return current;
        }
    }

    public static void main(String[] args) throws Exception {
        if (args.length != 2) {
            throw new IllegalArgumentException(
                    "usage: TestMain docs/contract.json docs/official_sources.json");
        }
        Path contractPath = Path.of(args[0]);
        Path sourcesPath = Path.of(args[1]);
        List<String> failures = new ArrayList<>();

        checkOfficialSources(sourcesPath, failures);
        RotatingTokens tokens = new RotatingTokens();
        String readBody = null;

        try (ContractMockServer mock = new ContractMockServer(contractPath)) {
            check("PATCH".equals(mock.operation("PatchGroupForDomain").method()),
                    "PatchGroupForDomain must be PATCH", failures);
            check("GET".equals(mock.operation("ReadGroupForDomain").method()),
                    "ReadGroupForDomain must be GET", failures);

            NsxPolicyClient client = new NsxPolicyClient(mock.endpoint(), tokens);
            NsxPolicyClient.Group group = new NsxPolicyClient.Group(
                    "Web workloads",
                    null,
                    null,
                    List.of(new NsxPolicyClient.Condition(
                            "VirtualMachine",
                            "Tag",
                            "EQUALS",
                            "line\n\"blue\"\\prod",
                            null)));

            try {
                client.patchGroup("tenant a", "web/apps", group);
            } catch (Exception e) {
                failures.add("patchGroup threw " + describe(e));
            }
            try {
                readBody = client.readGroup("tenant a", "web/apps");
            } catch (Exception e) {
                failures.add("readGroup did not refresh and retry successfully: " + describe(e));
            }

            List<ContractMockServer.LoggedRequest> requests = mock.requests();
            check(requests.size() == 3,
                    "expected exactly PATCH, expired GET, retried GET; observed " + requests.size(),
                    failures);
            if (requests.size() >= 1) {
                assertPatch(requests.get(0), failures);
            }
            if (requests.size() >= 2) {
                assertGet(requests.get(1), "token-old", failures);
            }
            if (requests.size() >= 3) {
                assertGet(requests.get(2), "token-fresh", failures);
            }
            if (requests.size() > 3) {
                failures.add("completed work was replayed after token expiry");
            }

            byte[] stored = mock.storedGroup();
            check(stored != null, "mock did not retain the successful PATCH", failures);
            if (stored != null) {
                check(EXPECTED_BODY.equals(new String(stored, StandardCharsets.UTF_8)),
                        "stored group differs from the exact PATCH wire body", failures);
            }
        }

        check(tokens.refreshes == 1,
                "access token must be refreshed exactly once; observed " + tokens.refreshes,
                failures);
        check(EXPECTED_READ_BODY.equals(readBody),
                "readGroup must return the successful retried response body", failures);

        if (!failures.isEmpty()) {
            System.err.println("VERIFICATION FAILED");
            for (String failure : failures) {
                System.err.println(" - " + failure);
            }
            System.exit(1);
        }
        System.out.println("VERIFICATION PASSED: exact contract wire shape and token retry");
    }

    private static void assertPatch(
            ContractMockServer.LoggedRequest request,
            List<String> failures) {
        check("PatchGroupForDomain".equals(request.operationId()),
                "first request did not map to PatchGroupForDomain", failures);
        check("PATCH".equals(request.method()), "first request must be PATCH", failures);
        check(EXPECTED_PATCH_PATH.equals(request.rawPath()),
                "PATCH raw path was " + request.rawPath(), failures);
        check(request.rawQuery() == null, "PATCH must omit a query string", failures);
        check("Bearer token-old".equals(request.firstHeader("Authorization")),
                "PATCH Authorization header is wrong", failures);
        check("application/json".equals(request.firstHeader("Accept")),
                "PATCH Accept header is wrong", failures);
        check("application/json".equals(request.firstHeader("Content-Type")),
                "PATCH Content-Type header is wrong", failures);
        check(EXPECTED_BODY.equals(request.utf8Body()),
                "PATCH body bytes differ.\n   expected: " + EXPECTED_BODY
                        + "\n   actual:   " + request.utf8Body(),
                failures);

        String body = request.utf8Body();
        check(!body.contains("\"description\""),
                "unset optional description must be omitted", failures);
        check(!body.contains("\"group_type\""),
                "unset optional group_type must be omitted", failures);
        check(!body.contains("\"scope_operator\""),
                "unset optional scope_operator must be omitted", failures);
        check(!body.contains(":null"),
                "unset optionals must not be sent as JSON null", failures);
    }

    private static void assertGet(
            ContractMockServer.LoggedRequest request,
            String token,
            List<String> failures) {
        check("ReadGroupForDomain".equals(request.operationId()),
                "GET request did not map to ReadGroupForDomain", failures);
        check("GET".equals(request.method()), "read request must be GET", failures);
        check(EXPECTED_PATCH_PATH.equals(request.rawPath()),
                "GET raw path was " + request.rawPath(), failures);
        check(request.rawQuery() == null, "GET must omit a query string", failures);
        check(("Bearer " + token).equals(request.firstHeader("Authorization")),
                "GET Authorization header does not contain " + token, failures);
        check("application/json".equals(request.firstHeader("Accept")),
                "GET Accept header is wrong", failures);
        check(request.firstHeader("Content-Type") == null,
                "GET must omit Content-Type when it has no entity", failures);
        check(request.body().length == 0, "GET must have no request body", failures);
    }

    private static void checkOfficialSources(Path path, List<String> failures)
            throws IOException {
        String sources = Files.readString(path, StandardCharsets.UTF_8);
        check(sources.contains(
                        "\"repository_commit_sha\": "
                                + "\"c3f3b52c845dd967cabbc21680e893292077d5ba\""),
                "official_sources.json does not pin the VCF 9.1 commit", failures);
        check(sources.contains(
                        "\"spec_path\": "
                                + "\"specifications/nsx/openapi-2.0/nsx_policy_api.yaml\""),
                "official_sources.json does not pin the NSX Policy YAML path", failures);
        check(count(sources, "\"PatchGroupForDomain\"") == 2,
                "official_sources.json must record PatchGroupForDomain in its ID list and detail",
                failures);
        check(count(sources, "\"ReadGroupForDomain\"") == 2,
                "official_sources.json must record ReadGroupForDomain in its ID list and detail",
                failures);
    }

    private static int count(String text, String needle) {
        int count = 0;
        int offset = 0;
        while ((offset = text.indexOf(needle, offset)) >= 0) {
            count++;
            offset += needle.length();
        }
        return count;
    }

    private static String describe(Exception exception) {
        String message = exception.getMessage();
        return exception.getClass().getSimpleName()
                + (message == null ? "" : ": " + message);
    }

    private static void check(boolean condition, String failure, List<String> failures) {
        if (!condition) {
            failures.add(failure);
        }
    }
}
