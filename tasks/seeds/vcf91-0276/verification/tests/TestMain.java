import java.io.IOException;
import java.net.URI;
import java.time.Duration;
import java.util.List;

public final class TestMain {
    private static void check(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }

    private static List<String> split(String value) {
        return value.isEmpty() ? List.of() : List.of(value.split(",", -1));
    }

    public static void main(String[] args) throws Exception {
        if (args.length != 8) {
            throw new IllegalArgumentException("expected eight harness arguments");
        }
        String mode = args[0];
        VcfOpsAlertHarvestClient client = new VcfOpsAlertHarvestClient(
                URI.create(args[1]),
                args[2],
                args[3],
                null,
                2,
                Duration.ofSeconds(5));
        String resourceKind = args[4];

        if (mode.equals("expired")) {
            expired(client, resourceKind, args[3]);
            return;
        }

        VcfOpsAlertHarvestClient.Harvest harvest = client.harvestCriticalAlerts(resourceKind);

        List<String> expectedIds = split(args[5]);
        List<String> expectedNames = split(args[6]);
        List<String> expectedAlerts = split(args[7]);

        check(
                harvest.resources().stream().map(VcfOpsAlertHarvestClient.MonitoredResource::identifier).toList()
                        .equals(expectedIds),
                "harvested resources do not match the monitored inventory");
        check(
                harvest.resources().stream().map(VcfOpsAlertHarvestClient.MonitoredResource::name).toList()
                        .equals(expectedNames),
                "resource names were not preserved in server order");
        check(
                harvest.resources().stream().allMatch(r -> r.resourceKindKey().equals(resourceKind)),
                "a resource of an unrequested kind was harvested");
        check(
                harvest.alerts().stream().map(VcfOpsAlertHarvestClient.ActiveAlert::alertId).toList()
                        .equals(expectedAlerts),
                "harvested alerts do not match the focused query result");
        check(
                harvest.alerts().stream().allMatch(
                        a -> a.alertLevel().equals("CRITICAL") || a.alertLevel().equals("IMMEDIATE")),
                "an alert outside the requested criticality was harvested");
        check(
                harvest.alerts().stream().noneMatch(a -> a.status().equals("CANCELED")),
                "a cancelled alert was harvested");
        check(
                harvest.alerts().stream().allMatch(a -> expectedIds.contains(a.resourceId())),
                "an alert for an unmonitored resource was harvested");
        int expectedAcquisitions = mode.equals("empty") ? 1 : 2;
        check(
                harvest.tokenAcquisitions() == expectedAcquisitions,
                "unexpected token acquisition count: " + harvest.tokenAcquisitions());

        try {
            harvest.resources().clear();
            throw new AssertionError("harvest exposed a mutable resource list");
        } catch (UnsupportedOperationException expected) {
            // required defensive view
        }
        try {
            harvest.alerts().clear();
            throw new AssertionError("harvest exposed a mutable alert list");
        } catch (UnsupportedOperationException expected) {
            // required defensive view
        }

        System.out.println("SUCCESSFUL");
    }

    private static void expired(
            VcfOpsAlertHarvestClient client,
            String resourceKind,
            String password)
            throws Exception {
        for (String invalid : new String[] {null, "", "  ", " VirtualMachine", "VirtualMachine "}) {
            try {
                client.harvestCriticalAlerts(invalid);
                throw new AssertionError("an invalid resource kind was accepted");
            } catch (IllegalArgumentException expected) {
                // rejected before any traffic
            }
        }

        try {
            client.harvestCriticalAlerts(resourceKind);
            throw new AssertionError("a permanently expired token still produced a harvest");
        } catch (IOException expected) {
            String message = String.valueOf(expected.getMessage());
            check(!message.contains(password), "the failure exposed the password");
            check(!message.contains("OpsToken"), "the failure exposed the token header");
        }

        System.out.println("SUCCESSFUL");
    }
}
