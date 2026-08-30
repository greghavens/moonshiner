package com.vmware.vcfops;

/**
 * Harness that exercises {@link VcfOperationsClient} against the loopback mock.
 *
 * <p>Run it through {@code harness/run_tests.sh}, which starts the mock, compiles
 * the client together with this file and passes the mock base URL as argv[0].
 *
 * <p>The reconcile call is issued twice with the same group name. A correct
 * client creates the group on the first pass and updates the very same group on
 * the second pass; it must not end up with two groups, and it must not fail.
 */
public final class TestMain {

    static final String USERNAME = "svc-fleet-automation";
    static final String PASSWORD = "vcf-ops-9.1-Str0ng!";
    static final String AUTH_SOURCE = "Local Users";

    static final String GROUP_NAME = "VCF Fleet - Noisy Production VMs";
    static final String ADAPTER_KIND_KEY = "Container";
    static final String RESOURCE_KIND_KEY = "Environment";
    static final boolean AUTO_RESOLVE = true;
    static final String RULE_ADAPTER_KIND = "VMWARE";
    static final String RULE_RESOURCE_KIND = "VirtualMachine";
    static final String NAME_CONTAINS = "prod-";
    static final String POLICY = "0f4c1f5e-2a8b-4d1c-9f77-6b3a2c5d8e91";

    private TestMain() {
    }

    public static void main(String[] args) throws Exception {
        if (args.length < 1) {
            throw new IllegalArgumentException("usage: TestMain <baseUrl>");
        }
        String baseUrl = args[0];
        VcfOperationsClient client = new VcfOperationsClient(baseUrl);

        // 1. Acquire a token without naming an auth source.
        String defaultToken = client.acquireToken(USERNAME, PASSWORD, null);
        require(defaultToken != null && !defaultToken.isEmpty(),
                "acquireToken(..., null) must return the token from the response");

        // 2. Acquire a token against a named auth source. The most recently
        //    acquired token becomes the client's current credential.
        String scopedToken = client.acquireToken(USERNAME, PASSWORD, AUTH_SOURCE);
        require(scopedToken != null && !scopedToken.isEmpty(),
                "acquireToken(..., \"" + AUTH_SOURCE + "\") must return the token from the response");
        require(!scopedToken.equals(defaultToken),
                "the two acquired tokens are distinct; the client must return what the server sent");

        // 3. Reconcile the custom group. Nothing exists yet, so this creates it.
        String firstId = client.ensureCustomGroup(GROUP_NAME, ADAPTER_KIND_KEY, RESOURCE_KIND_KEY,
                AUTO_RESOLVE, null, RULE_ADAPTER_KIND, RULE_RESOURCE_KIND, NAME_CONTAINS);
        require(firstId != null && !firstId.isEmpty(),
                "ensureCustomGroup must return the group identifier assigned by the server");
        require("created".equals(client.lastAction()),
                "lastAction() after the first pass must be \"created\", got: " + client.lastAction());

        // 4. Reconcile again with the same name. This must converge on the same
        //    group rather than creating a second one, and it may carry a policy.
        String secondId = client.ensureCustomGroup(GROUP_NAME, ADAPTER_KIND_KEY, RESOURCE_KIND_KEY,
                AUTO_RESOLVE, POLICY, RULE_ADAPTER_KIND, RULE_RESOURCE_KIND, NAME_CONTAINS);
        require(firstId.equals(secondId),
                "the retry must converge on group " + firstId + ", got " + secondId);
        require("updated".equals(client.lastAction()),
                "lastAction() after the retry must be \"updated\", got: " + client.lastAction());

        System.out.println("HARNESS_OK " + firstId);
    }

    private static void require(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }
}
