import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Protected harness. Drives {@code VcfOpsClient} against the loopback mock and dumps every
 * raw response to an evidence file. Do not edit: verify/run_verify.py checks its checksum.
 *
 * <p>Phase 1 walks the incident with optional arguments left null. Phase 2 supplies those
 * arguments and also repeats all-unsupplied calls with empty lists, so that "omitted when unset"
 * can be told apart from "never sent at all".
 *
 * <p>Usage: {@code java TestMain <baseUrl> <evidenceOutPath>}
 * where baseUrl looks like {@code http://127.0.0.1:PORT/suite-api}.
 */
public final class TestMain {

    private static final String USER = "svc-diag";
    private static final String PASS = "R3d-Herring!2026";
    private static final String DATASTORE = "wld01-vsan-ds01";

    private static final String UUID_AFTER_KEY =
            "\"%s\"\\s*:\\s*\"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
                    + "-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\"";

    public static void main(String[] args) throws Exception {
        if (args.length != 2) {
            System.err.println("usage: TestMain <baseUrl> <evidenceOutPath>");
            System.exit(2);
        }
        String baseUrl = args[0];
        Path out = Path.of(args[1]);
        Map<String, String> evidence = new LinkedHashMap<>();

        String datastoreId;
        String alertId;
        String taskId;

        // ---- Phase 1: diagnostic walk, no optional arguments supplied ------------------
        try (VcfOpsClient client = new VcfOpsClient(baseUrl)) {
            client.acquireToken(USER, PASS, null);

            String resources = client.getMatchingResources(List.of(DATASTORE), null);
            evidence.put("resources", resources);
            datastoreId = first(resources, "identifier");
            require(datastoreId != null, "no resource identifier in getMatchingResources response");

            String alerts = client.queryAlert(List.of(datastoreId), true, List.of("CRITICAL", "IMMEDIATE"));
            evidence.put("alerts", alerts);
            alertId = first(alerts, "alertId");
            require(alertId != null, "no alertId in queryAlert response");

            String contributing = client.getAlertContributingSymptoms(List.of(alertId));
            evidence.put("contributingSymptoms", contributing);

            String symptoms = client.getSymptoms(List.of(datastoreId), true, null);
            evidence.put("symptoms", symptoms);

            String tasks = client.getTasksStatus(List.of("ERROR"), null);
            evidence.put("tasks", tasks);
            taskId = first(tasks, "taskId");
            require(taskId != null, "no taskId in getTasksStatus response");

            client.releaseToken();
        }

        // ---- Phase 2: same operations, optional arguments supplied ---------------------
        try (VcfOpsClient client = new VcfOpsClient(baseUrl)) {
            client.acquireToken(USER, PASS, "local");
            evidence.put("phase2Resources", client.getMatchingResources(List.of(DATASTORE), List.of("Datastore")));
            evidence.put("phase2ResourcesNoFilters", client.getMatchingResources(List.of(), List.of()));
            evidence.put("phase2AlertsNoCriticality", client.queryAlert(List.of(datastoreId), false, List.of()));
            evidence.put("phase2AlertsNoResource", client.queryAlert(List.of(), true, List.of("CRITICAL")));
            evidence.put("phase2Contributing", client.getAlertContributingSymptoms(List.of(alertId, alertId)));
            evidence.put("phase2Symptoms", client.getSymptoms(List.of(datastoreId), false, true));
            evidence.put("phase2SymptomsNoFilters", client.getSymptoms(List.of(), null, null));
            evidence.put("phase2Tasks", client.getTasksStatus(List.of("ERROR"), List.of(taskId)));
            evidence.put("phase2TasksNoFilters", client.getTasksStatus(List.of(), List.of()));
            client.releaseToken();
        }

        evidence.put("datastoreId", datastoreId);
        evidence.put("alertId", alertId);
        evidence.put("notificationTaskId", taskId);

        writeEvidence(out, evidence);
        System.out.println("TestMain: OK, evidence written to " + out.toAbsolutePath());
    }

    private static String first(String json, String key) {
        Matcher m = Pattern.compile(String.format(UUID_AFTER_KEY, key)).matcher(json == null ? "" : json);
        return m.find() ? m.group(1) : null;
    }

    private static void require(boolean cond, String msg) {
        if (!cond) {
            throw new IllegalStateException("harness assertion failed: " + msg);
        }
    }

    private static void writeEvidence(Path out, Map<String, String> evidence) throws IOException {
        if (out.getParent() != null) {
            Files.createDirectories(out.getParent());
        }
        List<String> parts = new ArrayList<>();
        for (Map.Entry<String, String> e : evidence.entrySet()) {
            parts.add("  " + quote(e.getKey()) + ": " + quote(e.getValue()));
        }
        String doc = "{\n" + String.join(",\n", parts) + "\n}\n";
        Files.writeString(out, doc, StandardCharsets.UTF_8);
    }

    private static String quote(String s) {
        if (s == null) {
            return "null";
        }
        StringBuilder sb = new StringBuilder(s.length() + 16).append('"');
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '"' -> sb.append("\\\"");
                case '\\' -> sb.append("\\\\");
                case '\n' -> sb.append("\\n");
                case '\r' -> sb.append("\\r");
                case '\t' -> sb.append("\\t");
                default -> {
                    if (c < 0x20) {
                        sb.append(String.format("\\u%04x", (int) c));
                    } else {
                        sb.append(c);
                    }
                }
            }
        }
        return sb.append('"').toString();
    }

    private TestMain() {
    }
}
