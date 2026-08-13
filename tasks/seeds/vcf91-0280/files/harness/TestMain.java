import java.nio.file.Path;

/**
 * Drives {@link OpsAdapterClient} against {@link MockOpsServer} and prints one machine-readable
 * line per scenario.
 *
 * <p>Usage: {@code java TestMain <contract.json> <request-log.jsonl>}
 *
 * <p>This is test scaffolding. Do not modify it; the verifier checks its integrity.
 */
public final class TestMain {

    /** Opaque credential handed to the client; it must reach the wire verbatim. */
    private static final String AUTHORIZATION = "vRealizeOpsToken 4c1f0f5e-2b7a-4a01-9d3b-6e8f2a0c17d4::b3JzLWRlbW8";

    public static void main(String[] args) throws Exception {
        if (args.length != 2) {
            System.err.println("usage: TestMain <contract.json> <request-log.jsonl>");
            System.exit(2);
        }
        Path contract = Path.of(args[0]);
        Path log = Path.of(args[1]);

        try (MockOpsServer mock = new MockOpsServer(contract, log).start()) {
            OpsAdapterClient client = new OpsAdapterClient(mock.origin(), AUTHORIZATION);

            run(mock, "full_onboarding", () -> client.onboard(reachableSpec("Prod VC Adapter Instance")));

            run(mock, "minimal_onboarding", () -> client.onboard(
                    OpsAdapterClient.AdapterInstanceSpec.builder("Bare VC Adapter Instance", "VMWARE").build()));

            run(mock, "credential_without_fields", () -> client.onboard(
                    OpsAdapterClient.AdapterInstanceSpec.builder(
                                    "Credential-only VC Adapter Instance", "VMWARE")
                            .credential(OpsAdapterClient.Credential.builder(
                                    "Empty Principal Credential", "VMWARE", "PRINCIPALCREDENTIAL").build())
                            .build()));

            run(mock, "precheck_blocks_mutation", () -> client.onboard(
                    OpsAdapterClient.AdapterInstanceSpec.builder("Lab VC Adapter Instance", "VMWARE")
                            .description("A vCenter Adapter Instance")
                            .credential(principalCredential())
                            .addResourceIdentifier("AUTODISCOVERY", "true")
                            .addResourceIdentifier("VCURL", "vcenter-down.lab.local")
                            .build()));

            run(mock, "identifier_defaults_requested",
                    () -> client.onboard(reachableSpec("Edge VC Adapter Instance"), true));

            run(mock, "create_rejected",
                    () -> client.onboard(reachableSpec("Duplicate VC Adapter Instance")));
        }
    }

    private static OpsAdapterClient.AdapterInstanceSpec reachableSpec(String name) {
        return OpsAdapterClient.AdapterInstanceSpec.builder(name, "VMWARE")
                .description("A vCenter Adapter Instance")
                .collectorId("1")
                .collectorGroupId("11111111-1111-1111-1111-111111111111")
                .physicalDatacenterId("22222222-2222-2222-2222-222222222222")
                .monitoringInterval(0)
                .monitoringIntervalSeconds(300)
                .credential(principalCredential())
                .addResourceIdentifier("AUTODISCOVERY", "true")
                .addResourceIdentifier("PROCESSCHANGEEVENTS", "true")
                .addResourceIdentifier("VCURL", "vcenter-a.lab.local")
                .build();
    }

    private static OpsAdapterClient.Credential principalCredential() {
        return OpsAdapterClient.Credential.builder("Principal Credential", "VMWARE", "PRINCIPALCREDENTIAL")
                .addField("USER", "svc-vcfops@lab.local")
                .addField("PASSWORD", "s3cr3t")
                .build();
    }

    @FunctionalInterface
    private interface Scenario {
        OpsAdapterClient.OnboardResult call() throws Exception;
    }

    private static void run(MockOpsServer mock, String name, Scenario scenario) {
        mock.mark(name);
        OpsAdapterClient.OnboardResult result;
        try {
            result = scenario.call();
        } catch (Exception e) {
            System.out.println("ERROR scenario=" + name + " exception=" + e.getClass().getName());
            System.out.println("DETAIL scenario=" + name + " phase=exception value=" + String.valueOf(e.getMessage()));
            return;
        }
        if (result == null) {
            System.out.println("ERROR scenario=" + name + " exception=NullOnboardResult");
            return;
        }
        System.out.println("RESULT scenario=" + name
                + " precheckPassed=" + result.precheckPassed()
                + " precheckStatus=" + result.precheckStatus()
                + " created=" + result.created()
                + " createStatus=" + result.createStatus()
                + " adapterInstanceId=" + String.valueOf(result.adapterInstanceId()));
        if (result.precheckDetail() != null) {
            System.out.println("DETAIL scenario=" + name + " phase=precheck value=" + result.precheckDetail());
        }
        if (result.createDetail() != null) {
            System.out.println("DETAIL scenario=" + name + " phase=create value=" + result.createDetail());
        }
    }
}
