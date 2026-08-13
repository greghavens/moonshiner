package com.broadcom.vcf.lab;

import java.util.List;

/**
 * Golden-image fan-out client for the vSphere Automation API on VMware Cloud Foundation 9.0.
 *
 * <p>Given the name of a source virtual machine and a batch of clones to make from it, the client
 * establishes a session, resolves the source virtual machine's identifier and then submits one
 * clone per entry in the batch, releasing the session when it is done.
 *
 * <p>The wire contract for every operation this client is allowed to use is in
 * {@code docs/contract.json}, transcribed from the vSphere Automation API specification shipped
 * with VCF 9.0. This class is the only file the fan-out is implemented in.
 */
public final class VcenterSessionClient implements AutoCloseable {

    /** One clone to create from the source virtual machine. */
    public static final class CloneRequest {

        /** Name to give the new virtual machine. Always supplied. */
        public final String name;

        /**
         * Inventory folder to place the clone in, or {@code null} when the caller has no opinion
         * and the source virtual machine's own folder should be used.
         */
        public final String placementFolder;

        /**
         * Whether to power the clone on after cloning, or {@code null} when the caller has no
         * opinion and the server's own default should apply.
         */
        public final Boolean powerOn;

        public CloneRequest(String name, String placementFolder, Boolean powerOn) {
            this.name = name;
            this.placementFolder = placementFolder;
            this.powerOn = powerOn;
        }
    }

    private final String baseUrl;
    private final String username;
    private final String password;

    /**
     * @param baseUrl  the API base, including the path prefix the specification's {@code servers}
     *                 entry fixes, for example {@code https://vcenter.example.com/api}
     * @param username principal to authenticate as
     * @param password that principal's password
     */
    public VcenterSessionClient(String baseUrl, String username, String password) {
        this.baseUrl = baseUrl.endsWith("/") ? baseUrl.substring(0, baseUrl.length() - 1) : baseUrl;
        this.username = username;
        this.password = password;
    }

    /**
     * Clones {@code sourceVmName} once per entry in {@code clones}.
     *
     * @param sourceVmName name of the virtual machine to clone from
     * @param clones       the batch, processed in list order
     * @return the identifier of each newly created virtual machine, in the same order as
     *         {@code clones}
     * @throws Exception if the batch cannot be completed
     */
    public List<String> cloneFanOut(String sourceVmName, List<CloneRequest> clones) throws Exception {
        throw new UnsupportedOperationException("cloneFanOut is not implemented yet");
    }

    /** Releases the session this client currently holds, if it holds one. */
    @Override
    public void close() throws Exception {
        // nothing held yet
    }
}
