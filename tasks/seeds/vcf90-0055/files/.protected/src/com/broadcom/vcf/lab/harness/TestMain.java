package com.broadcom.vcf.lab.harness;

import com.broadcom.vcf.lab.VcenterSessionClient;
import com.broadcom.vcf.lab.VcenterSessionClient.CloneRequest;

import java.nio.file.Path;
import java.util.List;

/**
 * Drives {@link VcenterSessionClient} against a loopback {@link MockVcenter} and hands the
 * resulting request log to {@link ContractVerifier}. Exits 0 when every check passes.
 *
 * <p>Part of the protected harness: do not modify.
 */
public final class TestMain {

    private static final Path CONTRACT = Path.of("docs", "contract.json");
    private static final Path SOURCES = Path.of("docs", "official_sources.json");

    /**
     * The batch. The mix is deliberate: some clones pin a folder, some pin a power state, one pins
     * both and one pins neither, and one asks for power_on=false so that "the caller set it to
     * false" stays distinguishable from "the caller did not set it".
     */
    private static final List<CloneRequest> BATCH = List.of(
            new CloneRequest("golden-rhel9-clone-01", "group-v810", Boolean.TRUE),
            new CloneRequest("golden-rhel9-clone-02", null, null),
            new CloneRequest("golden-rhel9-clone-03", null, Boolean.FALSE),
            new CloneRequest("golden-rhel9-clone-04", "group-v811", null),
            new CloneRequest("golden-rhel9-clone-05", null, Boolean.TRUE));

    public static void main(String[] args) throws Exception {
        try (MockVcenter mock = new MockVcenter(CONTRACT)) {
            System.out.println("mock vCenter listening on " + mock.baseUrl());
            System.out.println("the first session token is good for " + MockVcenter.FIRST_TOKEN_BUDGET
                    + " authenticated requests");
            System.out.println();

            List<String> returned = null;
            Throwable runFailure = null;
            Throwable closeFailure = null;

            VcenterSessionClient client = new VcenterSessionClient(
                    mock.baseUrl(), MockVcenter.USERNAME, MockVcenter.PASSWORD);
            try {
                returned = client.cloneFanOut(MockVcenter.SOURCE_VM_NAME, BATCH);
            } catch (Throwable t) {
                runFailure = t;
            }
            try {
                client.close();
            } catch (Throwable t) {
                closeFailure = t;
            }

            ContractVerifier verifier = new ContractVerifier(
                    CONTRACT, SOURCES, mock, BATCH, returned, runFailure, closeFailure);
            boolean passed = verifier.run();

            System.out.println();
            System.out.println(passed ? "PASS" : "FAIL");
            if (!passed) System.exit(1);
        }
    }
}
