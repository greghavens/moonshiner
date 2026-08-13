import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Drives {@link SnapserviceSweepClient} against the loopback mock appliance and records what
 * happened. The verifier reads {@code build/requests.jsonl} and {@code build/sweep-result.json}.
 */
public final class TestMain {

    private static final String NAME_PREFIX = "vcf91-sweep";

    public static void main(String[] args) throws Exception {
        Path root = Path.of(args.length > 0 ? args[0] : ".").toAbsolutePath().normalize();
        Path requestLog = root.resolve("build/requests.jsonl");
        Path resultFile = root.resolve("build/sweep-result.json");
        Files.createDirectories(root.resolve("build"));
        Files.deleteIfExists(requestLog);
        Files.deleteIfExists(resultFile);

        Map<String, SnapserviceSweepClient.RetentionPeriod> retention = new LinkedHashMap<>();
        retention.put("pg-1002", new SnapserviceSweepClient.RetentionPeriod("HOUR", 12));
        retention.put("pg-1005", new SnapserviceSweepClient.RetentionPeriod("DAY", 30));

        MockSnapserviceServer appliance = new MockSnapserviceServer(root.resolve("docs/contract.json"));
        Throwable failure = null;
        SnapserviceSweepClient.SweepResult result = null;
        try (SnapserviceSweepClient client = new SnapserviceSweepClient(
                appliance.baseUrl(), MockSnapserviceServer.USERNAME, MockSnapserviceServer.PASSWORD)) {
            result = client.sweepCluster(MockSnapserviceServer.CLUSTER, NAME_PREFIX, retention);
        } catch (Throwable thrown) {
            failure = thrown;
        } finally {
            appliance.writeLog(requestLog);
            appliance.close();
        }

        Files.writeString(resultFile, render(result, failure), StandardCharsets.UTF_8);
        if (failure != null) {
            System.err.println("sweepCluster failed: " + failure);
            failure.printStackTrace();
            System.exit(1);
        }
        System.out.println("sweep completed with " + result.entries.size() + " protection groups");
    }

    private static String render(SnapserviceSweepClient.SweepResult result, Throwable failure) {
        StringBuilder out = new StringBuilder("{\"failure\":");
        out.append(failure == null ? "null" : Json.string(failure.toString()));
        out.append(",\"name_prefix\":").append(Json.string(NAME_PREFIX));
        out.append(",\"sessions_created\":").append(result == null ? -1 : result.sessionsCreated);
        out.append(",\"entries\":[");
        if (result != null) {
            for (int index = 0; index < result.entries.size(); index++) {
                SnapserviceSweepClient.SweepEntry entry = result.entries.get(index);
                if (index > 0) {
                    out.append(',');
                }
                out.append("{\"protection_group\":").append(nullable(entry.protectionGroup))
                        .append(",\"snapshot_name\":").append(nullable(entry.snapshotName))
                        .append(",\"task_id\":").append(nullable(entry.taskId))
                        .append(",\"status\":").append(nullable(entry.status))
                        .append('}');
            }
        }
        return out.append("]}").toString();
    }

    private static String nullable(String value) {
        return value == null ? "null" : Json.string(value);
    }
}
