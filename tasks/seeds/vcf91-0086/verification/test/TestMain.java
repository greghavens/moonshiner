import java.net.URI;
import java.util.List;

public final class TestMain {
    private static void check(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }

    public static void main(String[] args) throws Exception {
        if (args.length != 1) {
            throw new IllegalArgumentException("usage: TestMain <loopback-manager-uri>");
        }

        NsxPolicyClient client = new NsxPolicyClient(
                URI.create(args[0]),
                "audit-reader",
                "s3cret");

        NsxPolicyClient.DiagnosticReport report =
                client.diagnoseConnectivityFailure("tf incident/42");

        check(report.traceflowId().equals("tf incident/42"), "traceflow id was not preserved");

        List<NsxPolicyClient.AlarmEvidence> alarms = report.errorAlarms();
        check(alarms.size() == 2, "expected exactly the two ERROR alarms");
        check(alarms.get(0).id().equals("BGP_NEIGHBOR_DOWN"), "alarm response order changed");
        check(alarms.get(0).severity().equals("ERROR"), "alarm severity missing");
        check(
                alarms.get(0).message().equals(
                        "Neighbor 192.0.2.1 reported \"hold timer expired\", route withdrawn"),
                "escaped alarm message was not decoded");
        check(
                alarms.get(0).sourceReference().endsWith("/neighbors/192.0.2.1"),
                "alarm source_reference missing");
        check(alarms.get(1).id().equals("REALIZATION_RETRY"), "second error alarm missing");
        check(
                alarms.get(1).message().equals(
                        "Retry pending for object with note: {check, then retry}"),
                "punctuated alarm message was not decoded");

        List<NsxPolicyClient.DropEvidence> drops = report.droppedPackets();
        check(drops.size() == 2, "expected both physical and logical drop observations");
        check(
                drops.get(0).resourceType().equals("TraceflowObservationDropped"),
                "physical drop resource_type missing");
        check(drops.get(0).reason().equals("NO_ROUTE"), "physical drop reason missing");
        check(
                drops.get(0).componentName().equals("prod-t0-service-router"),
                "physical drop component missing");
        check(
                drops.get(0).transportNodeName().equals("edge-01"),
                "physical drop transport node missing");
        check(drops.get(0).sequenceNumber() == 1L, "physical drop sequence_no missing");
        check(
                drops.get(1).resourceType().equals("TraceflowObservationDroppedLogical"),
                "logical drop resource_type missing");
        check(drops.get(1).reason().equals("FW_STATE"), "logical drop reason missing");
        check(drops.get(1).componentName().equals("dfw, slot {7}"), "logical drop component missing");
        check(drops.get(1).transportNodeName().equals("esx-07"), "logical drop node missing");
        check(drops.get(1).sequenceNumber() == 2L, "logical drop sequence_no missing");

        boolean immutable = false;
        try {
            report.errorAlarms().clear();
        } catch (UnsupportedOperationException expected) {
            immutable = true;
        }
        check(immutable, "diagnostic evidence lists must be immutable snapshots");

        System.out.println("TEST_MAIN_OK");
    }
}
