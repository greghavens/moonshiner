package com.broadcom.vcfa;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Drives {@link VcfAutomationClient} against a loopback {@link MockVcfAutomation}.
 *
 * <p>Usage: {@code java -cp build com.broadcom.vcfa.TestMain <requestLog> <resultFile>}.
 *
 * <p>Exits 0 when every assertion below holds. The request log and the result file are written
 * either way, because the verifier re-checks the wire traffic independently of this program.
 *
 * <p>Do not modify. The verifier restores this file from a pristine copy before it compiles.
 */
public final class TestMain {

    private static final int PAGE_SIZE = 3;
    private static final List<String> EXPECTED_IDS =
            List.of("dep-01", "dep-02", "dep-03", "dep-04", "dep-05", "dep-06", "dep-07");
    private static final String RENAMED = "search-cluster-blue";
    private static final String REDESCRIBED = "Nightly ETL workers, retimed to 02:00 UTC";
    private static final String NEW_ICON_ID = "7db3ea6f-7167-4d0f-846d-95f119695139";

    public static void main(String[] args) throws Exception {
        Path logPath = Path.of(args.length > 0 ? args[0] : "run/requests.jsonl");
        Path resultPath = Path.of(args.length > 1 ? args[1] : "run/result.json");

        List<String> failures = new ArrayList<>();
        Map<String, Object> result = new LinkedHashMap<>();

        try (MockVcfAutomation appliance = new MockVcfAutomation(logPath)) {
            VcfAutomationClient client =
                    new VcfAutomationClient(appliance.baseUrl(), MockVcfAutomation.REFRESH_TOKEN);

            List<VcfAutomationClient.Deployment> all = null;
            try {
                all = client.listAllDeployments(PAGE_SIZE);
            } catch (Exception e) {
                failures.add("listAllDeployments threw " + e);
            }
            result.put("deployments", describe(all));

            if (all != null) {
                List<String> ids = new ArrayList<>();
                for (VcfAutomationClient.Deployment d : all) {
                    ids.add(d.id());
                }
                if (!EXPECTED_IDS.equals(ids)) {
                    failures.add("listAllDeployments returned ids " + ids + ", expected " + EXPECTED_IDS);
                }
                for (VcfAutomationClient.Deployment d : all) {
                    if (d.name() == null || d.name().isEmpty()) {
                        failures.add("deployment " + d.id() + " came back without a name");
                    }
                    if (d.status() == null || d.status().isEmpty()) {
                        failures.add("deployment " + d.id() + " came back without a status");
                    }
                }
            }

            // Only the name changes: description and iconId must not appear on the wire at all.
            VcfAutomationClient.Deployment renamed = null;
            try {
                renamed = client.updateDeployment("dep-04", RENAMED, null, null);
            } catch (Exception e) {
                failures.add("updateDeployment(dep-04, name only) threw " + e);
            }
            result.put("renamed", describe(renamed));
            if (renamed != null) {
                if (!RENAMED.equals(renamed.name())) {
                    failures.add("dep-04 name is " + renamed.name() + ", expected " + RENAMED);
                }
                if (!"Elasticsearch cluster backing catalogue search".equals(renamed.description())) {
                    failures.add("dep-04 description changed to " + renamed.description()
                            + "; a field the caller did not set must not be sent");
                }
            }

            // Only the description changes.
            VcfAutomationClient.Deployment redescribed = null;
            try {
                redescribed = client.updateDeployment("dep-05", null, REDESCRIBED, null);
            } catch (Exception e) {
                failures.add("updateDeployment(dep-05, description only) threw " + e);
            }
            result.put("redescribed", describe(redescribed));
            if (redescribed != null) {
                if (!REDESCRIBED.equals(redescribed.description())) {
                    failures.add("dep-05 description is " + redescribed.description()
                            + ", expected " + REDESCRIBED);
                }
                if (!"batch-etl".equals(redescribed.name())) {
                    failures.add("dep-05 name changed to " + redescribed.name()
                            + "; a field the caller did not set must not be sent");
                }
            }

            // Only the icon changes. This also proves a supplied iconId is not silently dropped.
            VcfAutomationClient.Deployment reiconed = null;
            try {
                reiconed = client.updateDeployment("dep-06", null, null, NEW_ICON_ID);
            } catch (Exception e) {
                failures.add("updateDeployment(dep-06, icon only) threw " + e);
            }
            result.put("reiconed", describe(reiconed));
            if (reiconed != null) {
                if (!"dep-06".equals(reiconed.id())) {
                    failures.add("icon-only update returned deployment " + reiconed.id()
                            + ", expected dep-06");
                }
                if (!"observability".equals(reiconed.name())) {
                    failures.add("dep-06 name changed to " + reiconed.name()
                            + "; a field the caller did not set must not be sent");
                }
                if (!"Log and metric collectors".equals(reiconed.description())) {
                    failures.add("dep-06 description changed to " + reiconed.description()
                            + "; a field the caller did not set must not be sent");
                }
            }
        } finally {
            result.put("failures", new ArrayList<Object>(failures));
            Files.createDirectories(resultPath.toAbsolutePath().getParent());
            Files.writeString(resultPath, Json.write(result), StandardCharsets.UTF_8);
        }

        if (failures.isEmpty()) {
            System.out.println("TestMain: OK");
            System.exit(0);
        }
        for (String f : failures) {
            System.out.println("TestMain: FAIL " + f);
        }
        System.exit(1);
    }

    private static Object describe(VcfAutomationClient.Deployment d) {
        if (d == null) {
            return null;
        }
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("id", d.id());
        m.put("name", d.name());
        m.put("description", d.description());
        m.put("status", d.status());
        return m;
    }

    private static Object describe(List<VcfAutomationClient.Deployment> all) {
        if (all == null) {
            return null;
        }
        List<Object> out = new ArrayList<>();
        for (VcfAutomationClient.Deployment d : all) {
            out.add(describe(d));
        }
        return out;
    }
}
