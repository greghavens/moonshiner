import java.util.Map;

/**
 * Rotates the vSphere service-account secret of a VCF Automation cloud account.
 *
 * See README.md for the behaviour this client owes its caller and docs/contract.json for the
 * operations it is allowed to call.
 */
public final class CredentialRotator {

    /**
     * Rotates the password of a vSphere cloud account without stranding requests that are already
     * in flight against it.
     *
     * @param baseUrl        origin of the VCF Automation appliance, e.g. {@code http://127.0.0.1:8443}
     * @param bearerToken    access token to present as {@code Authorization: Bearer <token>}
     * @param cloudAccountId id of the vSphere cloud account whose secret is being rolled
     * @param newPassword    the secret the account should authenticate with afterwards
     * @return a summary with the keys {@code apiVersion}, {@code drainedRequestIds},
     *         {@code requestId} and {@code status}
     * @throws Exception if the rotation cannot be completed
     */
    public static Map<String, Object> rotate(String baseUrl, String bearerToken, String cloudAccountId,
                                             String newPassword) throws Exception {
        throw new UnsupportedOperationException("CredentialRotator.rotate is not implemented yet");
    }

    private CredentialRotator() {
    }
}
