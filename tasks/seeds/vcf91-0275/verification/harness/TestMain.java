import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.List;

/**
 * Protected harness. Drives VcfOpsReportClient against the loopback mock and records
 * what came back. It performs no HTTP of its own and makes no assertions - the wire
 * assertions live in harness/verify_wire.py, which reads the mock's request log.
 *
 * Usage: java TestMain <baseUrl> <outputJsonPath>
 */
public final class TestMain {

    private static final String USERNAME = "svc-report";
    private static final String PASSWORD = "R3port!Pass";
    private static final String RESOURCE_ID = "be82d29c-d82d-4d8c-8d9b-7f69d45b1c5f";

    private static final String DEF_COMPLETES = "97417a6d-708d-4b12-9142-484b5a0df4dc";
    private static final String DEF_FAILS = "1c0b9c1e-8f4a-4f52-9d6a-2b7c5e3a91fd";
    private static final String DEF_STUCK = "5f2d7a34-6b19-4c88-a0e3-9d41f7b26c50";

    public static void main(String[] args) throws IOException {
        if (args.length != 2) {
            System.err.println("usage: TestMain <baseUrl> <outputJsonPath>");
            System.exit(2);
        }
        String baseUrl = args[0];
        Path out = Paths.get(args[1]);

        List<String> records = new ArrayList<>();
        records.add(run("minimal", scenarioMinimal(baseUrl)));
        records.add(run("full-optionals", scenarioFullOptionals(baseUrl)));
        records.add(run("terminal-failure", scenarioTerminalFailure(baseUrl)));
        records.add(run("poll-budget-exhausted", scenarioPollBudgetExhausted(baseUrl)));
        records.add(run("http-error", scenarioHttpError(baseUrl)));

        StringBuilder sb = new StringBuilder();
        sb.append("{\"scenarios\":[");
        for (int i = 0; i < records.size(); i++) {
            if (i > 0) {
                sb.append(',');
            }
            sb.append(records.get(i));
        }
        sb.append("]}");
        Path parent = out.toAbsolutePath().getParent();
        if (parent != null) {
            Files.createDirectories(parent);
        }
        Files.write(out, sb.toString().getBytes(StandardCharsets.UTF_8));
        System.out.println("TestMain wrote " + out.toAbsolutePath());
    }

    // --- scenarios ------------------------------------------------------------

    private static VcfOpsReportClient.Request base(String baseUrl, String definitionId) {
        VcfOpsReportClient.Request r = new VcfOpsReportClient.Request();
        r.baseUrl = baseUrl;
        r.username = USERNAME;
        r.password = PASSWORD;
        r.authSource = null;
        r.resourceId = RESOURCE_ID;
        r.reportDefinitionId = definitionId;
        r.traversalSpec = null;
        r.downloadFormat = null;
        r.maxPolls = 10;
        r.pollIntervalMillis = 25L;
        return r;
    }

    /** Every optional field left unset: nothing optional may appear on the wire. */
    private static VcfOpsReportClient.Request scenarioMinimal(String baseUrl) {
        return base(baseUrl, DEF_COMPLETES);
    }

    /** Every optional field is supplied, including a false Boolean and escaped text. */
    private static VcfOpsReportClient.Request scenarioFullOptionals(String baseUrl) {
        VcfOpsReportClient.Request r = base(baseUrl, DEF_COMPLETES);
        r.authSource = "Imported LDAP Server";
        r.downloadFormat = "CSV";
        VcfOpsReportClient.TraversalSpec ts = new VcfOpsReportClient.TraversalSpec();
        ts.name = "vSphere Hosts and Clusters";
        ts.description = "All \"production\" hosts\nand clusters";
        ts.rootAdapterKindKey = "VMWARE";
        ts.rootResourceKindKey = "";
        ts.adapterInstanceAssociation = Boolean.FALSE;
        r.traversalSpec = ts;
        return r;
    }

    /** Reaches a terminal FAILED; the report must not be downloaded. */
    private static VcfOpsReportClient.Request scenarioTerminalFailure(String baseUrl) {
        return base(baseUrl, DEF_FAILS);
    }

    /** Never reaches a terminal status; polling must stop at maxPolls. */
    private static VcfOpsReportClient.Request scenarioPollBudgetExhausted(String baseUrl) {
        VcfOpsReportClient.Request r = base(baseUrl, DEF_STUCK);
        r.maxPolls = 4;
        return r;
    }

    /** Authentication failure must surface as an IOException naming the HTTP status. */
    private static VcfOpsReportClient.Request scenarioHttpError(String baseUrl) {
        VcfOpsReportClient.Request r = base(baseUrl, DEF_COMPLETES);
        r.password = "definitely-wrong";
        return r;
    }

    // --- driver ---------------------------------------------------------------

    private static String run(String name, VcfOpsReportClient.Request request) {
        StringBuilder sb = new StringBuilder();
        sb.append('{').append(json("name", name));
        try {
            VcfOpsReportClient.Result result = VcfOpsReportClient.generateReport(request);
            sb.append(',').append("\"threw\":false");
            if (result == null) {
                sb.append(',').append("\"result\":null");
            } else {
                sb.append(',').append("\"result\":{");
                sb.append(json("reportId", result.reportId)).append(',');
                sb.append(json("finalStatus", result.finalStatus)).append(',');
                sb.append("\"pollCount\":").append(result.pollCount).append(',');
                sb.append(json("downloadBody", result.downloadBody));
                sb.append('}');
            }
        } catch (Throwable t) {
            sb.append(',').append("\"threw\":true");
            sb.append(',').append("\"result\":null");
            sb.append(',').append(json("exceptionClass", t.getClass().getName()));
            sb.append(',').append(json("exceptionMessage", String.valueOf(t.getMessage())));
        }
        sb.append('}');
        return sb.toString();
    }

    // --- tiny JSON writer -----------------------------------------------------

    private static String json(String key, String value) {
        return quote(key) + ":" + (value == null ? "null" : quote(value));
    }

    private static String quote(String s) {
        StringBuilder sb = new StringBuilder("\"");
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '"':
                    sb.append("\\\"");
                    break;
                case '\\':
                    sb.append("\\\\");
                    break;
                case '\n':
                    sb.append("\\n");
                    break;
                case '\r':
                    sb.append("\\r");
                    break;
                case '\t':
                    sb.append("\\t");
                    break;
                default:
                    if (c < 0x20) {
                        sb.append(String.format("\\u%04x", (int) c));
                    } else {
                        sb.append(c);
                    }
            }
        }
        return sb.append('"').toString();
    }

    private TestMain() {
    }
}
