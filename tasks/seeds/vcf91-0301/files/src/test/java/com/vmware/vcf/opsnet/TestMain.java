package com.vmware.vcf.opsnet;

import com.vmware.vcf.opsnet.VcfOpsNetworksClient.BulkOperationReport;
import com.vmware.vcf.opsnet.VcfOpsNetworksClient.DataSourceEntity;
import com.vmware.vcf.opsnet.VcfOpsNetworksClient.FailedDataSource;
import com.vmware.vcf.opsnet.VcfOpsNetworksClient.FieldUpdate;

import java.util.List;

/**
 * Harness that drives {@link VcfOpsNetworksClient} through the bulk data source
 * scenario against a VCF Operations for Networks endpoint.
 *
 * <p>Reads the endpoint origin from the {@code VCFON_BASE_URL} environment variable,
 * runs the scenario, and prints one {@code RESULT <json>} line describing the terminal
 * bulk operation report.
 *
 * <p>This harness is fixed. Do not edit it - implement the client so that this runs.
 */
public final class TestMain {

    // --- scenario fixture -------------------------------------------------

    static final String USERNAME = "admin@vrni.local";
    static final String PASSWORD = "S3cure-Pass!2026";

    static final String SVC_USERNAME = "svc-integration@corp.example.com";
    static final String SVC_PASSWORD = "R0tate-Me-90d";
    static final String SVC_DOMAIN_TYPE = "LDAP";
    // Explicitly set to empty: it must still be serialized because empty is not unset.
    static final String SVC_DOMAIN_VALUE = "";

    static final String LOCAL_USERNAME = "local-automation";
    static final String LOCAL_PASSWORD = "L0cal-Pass!";

    static final String ACTION_TYPE = "UPDATE";

    static List<DataSourceEntity> dataSources() {
        return List.of(
                new DataSourceEntity("18230:963:993642895", List.of(
                        // action_on_field left unset
                        FieldUpdate.of("nickname", "edge-esx-01"),
                        new FieldUpdate("tags", "prod,dc-a", "ADD_TAGS"))),
                new DataSourceEntity("18230:963:993642896", List.of(
                        new FieldUpdate("tags", "legacy", "REMOVE_TAGS"))),
                new DataSourceEntity("18230:963:993642897", List.of(
                        // value is set, and set to the empty string
                        FieldUpdate.of("notes", ""))),
                new DataSourceEntity("18230:963:993642898", List.of(
                        new FieldUpdate("tags", "prod,dc-b", "OVERRIDE_TAGS"))));
    }

    public static void main(String[] args) {
        String baseUrl = System.getenv("VCFON_BASE_URL");
        if (baseUrl == null || baseUrl.isBlank()) {
            System.err.println("VCFON_BASE_URL is not set");
            System.exit(2);
        }

        if (args.length == 1 && "--timeout".equals(args[0])) {
            runTimeoutScenario(baseUrl);
            return;
        }

        VcfOpsNetworksClient client = new VcfOpsNetworksClient(baseUrl)
                .withPollIntervalMillis(60)
                .withPollTimeoutMillis(20_000);

        // 1. Authenticate with no domain. `domain` is unset for this call.
        String token = client.authenticate(USERNAME, PASSWORD, null, null);
        if (token == null || token.isBlank()) {
            System.err.println("authenticate returned no token");
            System.exit(3);
        }
        if (!token.equals(client.token())) {
            System.err.println("authenticate did not hold the token it returned");
            System.exit(7);
        }
        System.out.println("TOKEN " + token);

        // 2. Submit the asynchronous bulk data source operation.
        String requestId = client.submitBulkOperation(ACTION_TYPE, dataSources());
        if (requestId == null || requestId.isBlank()) {
            System.err.println("submitBulkOperation returned no request id");
            System.exit(4);
        }
        System.out.println("REQUEST_ID " + requestId);

        // 3. Poll it to a terminal state.
        BulkOperationReport report = client.awaitBulkOperation(requestId);
        if (report == null) {
            System.err.println("awaitBulkOperation returned null");
            System.exit(5);
        }
        System.out.println("RESULT " + toJson(report));

        // 4. Authenticate a service account whose domain IS set, so the same
        //    serialization path is exercised with the optional object present.
        String svcToken = client.authenticate(
                SVC_USERNAME, SVC_PASSWORD, SVC_DOMAIN_TYPE, SVC_DOMAIN_VALUE);
        if (svcToken == null || svcToken.isBlank()) {
            System.err.println("domain authenticate returned no token");
            System.exit(6);
        }
        if (!svcToken.equals(client.token())) {
            System.err.println("domain authenticate did not replace the held token");
            System.exit(8);
        }
        System.out.println("SVC_TOKEN " + svcToken);

        // 5. Exercise a partially set optional domain object. The object itself is
        //    present, while its unset `value` member must be absent.
        String localToken = client.authenticate(LOCAL_USERNAME, LOCAL_PASSWORD, "LOCAL", null);
        if (localToken == null || localToken.isBlank() || !localToken.equals(client.token())) {
            System.err.println("partial-domain authenticate did not return and hold its token");
            System.exit(9);
        }
        System.out.println("LOCAL_TOKEN " + localToken);
    }

    /** Run against the verifier's never-terminal mock to exercise the bounded wait. */
    static void runTimeoutScenario(String baseUrl) {
        VcfOpsNetworksClient client = new VcfOpsNetworksClient(baseUrl)
                .withPollIntervalMillis(20)
                .withPollTimeoutMillis(120);
        client.authenticate(USERNAME, PASSWORD, null, null);
        String requestId = client.submitBulkOperation(ACTION_TYPE, dataSources());
        try {
            client.awaitBulkOperation(requestId);
            System.err.println("awaitBulkOperation returned a nonterminal report");
            System.exit(10);
        } catch (VcfOpsNetworksClient.VcfOpsNetworksException expected) {
            System.out.println("TIMEOUT_OK");
        }
    }

    // --- output ------------------------------------------------------------
    // Serialized here, independently of the client, so the printed result reflects
    // what the client actually returned rather than how it builds request bodies.

    static String toJson(BulkOperationReport r) {
        StringBuilder sb = new StringBuilder();
        sb.append('{');
        sb.append("\"total_count\":").append(r.totalCount()).append(',');
        sb.append("\"success_count\":").append(r.successCount()).append(',');
        sb.append("\"failed_count\":").append(r.failedCount()).append(',');
        sb.append("\"successful_data_sources\":[");
        List<String> ok = r.successfulDataSources() == null ? List.of() : r.successfulDataSources();
        for (int i = 0; i < ok.size(); i++) {
            if (i > 0) {
                sb.append(',');
            }
            sb.append(quote(ok.get(i)));
        }
        sb.append("],\"failed_data_sources\":[");
        List<FailedDataSource> bad = r.failedDataSources() == null ? List.of() : r.failedDataSources();
        for (int i = 0; i < bad.size(); i++) {
            if (i > 0) {
                sb.append(',');
            }
            FailedDataSource f = bad.get(i);
            sb.append("{\"entity_id\":").append(quote(f.entityId()))
              .append(",\"reason\":").append(quote(f.reason())).append('}');
        }
        sb.append("]}");
        return sb.toString();
    }

    static String quote(String s) {
        if (s == null) {
            return "null";
        }
        StringBuilder sb = new StringBuilder("\"");
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

    private TestMain() {}
}
