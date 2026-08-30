import java.net.URI;
import java.io.IOException;
import java.time.Instant;
import java.util.List;

public final class TestMain {
    private static final String REQUEST_ID = "req-failed-42";

    public static void main(String[] args) throws Exception {
        if (args.length != 1) {
            throw new IllegalArgumentException("usage: TestMain <base-url>");
        }

        var client = new VcfAutomationDiagnostic(URI.create(args[0]), "fixture-token");
        var diagnosis = client.diagnose(REQUEST_ID);

        check(diagnosis.request().id().equals(REQUEST_ID), "wrong request id");
        check(diagnosis.request().name().equals("Create application VM"), "wrong request name");
        check(diagnosis.request().status().equals("FAILED"), "request status was not retained");
        check(
                diagnosis.request().details().equals(
                        "Provisioning request failed. Inspect request events and logs."),
                "request details were not retained");

        var events = diagnosis.events();
        check(
                ids(events).equals(
                        List.of("evt-start", "evt-allocate", "evt-allocate-z", "evt-cleanup")),
                "events must be sorted by timestamp and id: " + ids(events));

        var start = events.get(0);
        check(start.name().equals("Request started"), "start event name was not retained");
        check(start.resourceName().equals("payments-vm"), "start event resource name was not retained");
        check(
                start.resourceType().equals("Cloud.vSphere.Machine"),
                "start event resource type was not retained");
        check(
                start.details().equals("Request accepted for provisioning."),
                "start event details were not retained");
        check(
                start.timestamp().equals(Instant.parse("2026-08-15T14:00:00Z")),
                "start event timestamp was not retained");
        check(start.userEvent(), "start event lost userEvent");
        check(!start.hasLogs(), "start event should not report logs");
        check(start.logs().isEmpty(), "an event with hasLogs=false must have no fetched entries");
        check(start.downloadedLogContent().isEmpty(), "an event with hasLogs=false must have no download");

        var allocate = events.get(1);
        check(allocate.name().equals("Allocate network"), "allocation event name was not retained");
        check(
                allocate.resourceName().equals("payments-vm"),
                "allocation event resource name was not retained");
        check(
                allocate.resourceType().equals("Cloud.vSphere.Machine"),
                "allocation event resource type was not retained");
        check(
                allocate.details().equals("Allocation failed in the provider task."),
                "allocation event details were not retained");
        check(
                allocate.timestamp().equals(Instant.parse("2026-08-15T14:03:00Z")),
                "allocation event timestamp was not retained");
        check(!allocate.userEvent(), "allocation event changed userEvent");
        check(allocate.hasLogs(), "allocation event lost hasLogs");
        check(
                allocate.logs().equals(
                        List.of(
                                log("alloc-log-1", 10, "2026-08-15T14:03:01Z",
                                        "Starting network allocation for payments-vm", false),
                                log("alloc-log-2a", 20, "2026-08-15T14:03:01.500Z",
                                        "Checking address availability", false),
                                log("alloc-log-2b", 20, "2026-08-15T14:03:02Z",
                                        "Allocation conflict confirmed", false),
                                log("alloc-log-2c", 20, "2026-08-15T14:03:02Z",
                                        "IP address 10.20.0.17 is already allocated", false),
                                log("alloc-log-3", 30, "2026-08-15T14:03:03Z",
                                        "Provider task terminated after allocation error", true))),
                "allocation log entries were not fully retained and sorted: " + allocate.logs());
        check(
                allocate.downloadedLogContent().equals(
                        "2026-08-15T14:03:01Z INFO Starting network allocation for payments-vm\n"
                                + "2026-08-15T14:03:02Z ERROR IP address 10.20.0.17 is already allocated\n"
                                + "2026-08-15T14:03:03Z ERROR Provider task terminated after allocation error\n"),
                "the complete downloadable allocation log was not retained");

        var checkpoint = events.get(2);
        check(checkpoint.name().equals("Allocation checkpoint"), "checkpoint name was not retained");
        check(
                checkpoint.resourceName().equals("payments-vm"),
                "checkpoint resource name was not retained");
        check(
                checkpoint.resourceType().equals("Cloud.vSphere.Machine"),
                "checkpoint resource type was not retained");
        check(
                checkpoint.details().equals("Checkpoint recorded at the allocation timestamp."),
                "checkpoint details were not retained");
        check(
                checkpoint.timestamp().equals(Instant.parse("2026-08-15T14:03:00Z")),
                "checkpoint timestamp was not retained");
        check(!checkpoint.userEvent(), "checkpoint changed userEvent");
        check(!checkpoint.hasLogs(), "checkpoint event should not report logs");
        check(checkpoint.logs().isEmpty(), "checkpoint event must not have fetched log entries");
        check(
                checkpoint.downloadedLogContent().isEmpty(),
                "checkpoint event must not have downloaded log content");

        var cleanup = events.get(3);
        check(cleanup.name().equals("Cleanup"), "cleanup event name was not retained");
        check(cleanup.resourceName().equals("payments-vm"), "cleanup resource name was not retained");
        check(
                cleanup.resourceType().equals("Cloud.vSphere.Machine"),
                "cleanup resource type was not retained");
        check(
                cleanup.details().equals("Cleanup ran after the provider failure."),
                "cleanup details were not retained");
        check(
                cleanup.timestamp().equals(Instant.parse("2026-08-15T14:05:00Z")),
                "cleanup timestamp was not retained");
        check(!cleanup.userEvent(), "cleanup changed userEvent");
        check(cleanup.hasLogs(), "cleanup event lost hasLogs");
        check(
                cleanup.logs().equals(
                        List.of(
                                log("cleanup-log-1", 1, "2026-08-15T14:05:01Z",
                                        "Releasing partial allocation", false),
                                log("cleanup-log-2", 2, "2026-08-15T14:05:02Z",
                                        "Cleanup completed", true))),
                "cleanup log entries were not fully retained and sorted: " + cleanup.logs());
        check(
                cleanup.downloadedLogContent().equals(
                        "2026-08-15T14:05:01Z INFO Releasing partial allocation\n"
                                + "2026-08-15T14:05:02Z INFO Cleanup completed\n"),
                "the complete downloadable cleanup log was not retained");

        checkUnmodifiable(events, "diagnosis events");
        checkUnmodifiable(allocate.logs(), "event logs");

        try {
            client.diagnose("missing-request");
            throw new AssertionError("a non-2xx response must throw IOException");
        } catch (IOException expected) {
            check(expected.getMessage().contains("404"), "IOException must include HTTP status");
            check(expected.getMessage().contains("request not found"), "IOException must include response body");
        }

        try {
            client.diagnose("req-download-failure");
            throw new AssertionError("a non-2xx log download must throw IOException");
        } catch (IOException expected) {
            check(expected.getMessage().contains("503"), "download IOException must include HTTP status");
            check(
                    expected.getMessage().contains("download service unavailable"),
                    "download IOException must include response body");
        }

        System.out.println("DIAGNOSIS_OK");
        System.out.println(String.join(",", ids(events)));
        System.out.println(String.join(",", logIds(allocate.logs())));
    }

    private static List<String> ids(List<VcfAutomationDiagnostic.EventEvidence> events) {
        return events.stream().map(VcfAutomationDiagnostic.EventEvidence::id).toList();
    }

    private static List<String> logIds(List<VcfAutomationDiagnostic.LogEntry> logs) {
        return logs.stream().map(VcfAutomationDiagnostic.LogEntry::id).toList();
    }

    private static VcfAutomationDiagnostic.LogEntry log(
            String id, long rownum, String timestamp, String message, boolean eof) {
        return new VcfAutomationDiagnostic.LogEntry(
                id, rownum, Instant.parse(timestamp), message, eof);
    }

    @SuppressWarnings({"rawtypes", "unchecked"})
    private static void checkUnmodifiable(List<?> values, String label) {
        try {
            ((List) values).add(null);
            throw new AssertionError(label + " must be unmodifiable");
        } catch (UnsupportedOperationException expected) {
            // Expected.
        }
    }

    private static void check(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }
}
