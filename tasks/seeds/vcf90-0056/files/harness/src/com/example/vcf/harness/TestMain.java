package com.example.vcf.harness;

import com.example.vcf.RoleInventoryClient;

import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;

/**
 * Drives {@link RoleInventoryClient} against the loopback {@link MockVcenter} and hands the
 * resulting request log to the {@link Verifier}.
 *
 * <p>Exits 0 when every assertion holds, 1 otherwise. No live VMware endpoint is contacted.
 */
public final class TestMain {

    private static final Path FIXTURES = Path.of("harness", "fixtures", "roles.json");
    private static final Path REQUEST_LOG = Path.of("build", "requests.jsonl");

    public static void main(String[] args) throws Exception {
        List<String> clientErrors = new ArrayList<>();
        List<Verifier.Scenario> scenarios = new ArrayList<>();

        try (MockVcenter mock = new MockVcenter(FIXTURES, REQUEST_LOG)) {
            mock.start();
            System.out.println("mock vcenter listening on " + mock.baseUrl());

            RoleInventoryClient client = new RoleInventoryClient(mock.baseUrl(), mock.sessionId());

            scenarios.add(run(mock, client, clientErrors,
                    "paged-all", 4, null));
            scenarios.add(run(mock, client, clientErrors,
                    "paged-custom-roles", 3, Boolean.FALSE));
            scenarios.add(run(mock, client, clientErrors,
                    "paged-system-roles", 2, Boolean.TRUE));
            scenarios.add(run(mock, client, clientErrors,
                    "server-default-page-size", null, null));
        }

        List<String> failures = new Verifier(FIXTURES).verify(REQUEST_LOG, scenarios);

        System.out.println();
        for (String error : clientErrors) {
            System.out.println("CLIENT ERROR: " + error);
        }
        for (String failure : failures) {
            System.out.println("FAIL: " + failure);
        }

        if (failures.isEmpty() && clientErrors.isEmpty()) {
            System.out.println("PASS: " + scenarios.size()
                    + " scenario(s) matched the contract in docs/contract.json");
            System.exit(0);
        }
        System.out.println();
        System.out.println("FAILED: " + failures.size() + " assertion failure(s), "
                + clientErrors.size() + " client error(s)");
        System.exit(1);
    }

    private static Verifier.Scenario run(MockVcenter mock, RoleInventoryClient client,
            List<String> clientErrors, String name, Integer pageSize, Boolean isSystem) {
        mock.beginScenario(name);
        String report = null;
        try {
            report = client.listRolesReport(pageSize, isSystem);
        } catch (Throwable t) {
            clientErrors.add("scenario '" + name + "' threw " + t);
        }
        return new Verifier.Scenario(name, pageSize, isSystem, report);
    }
}
