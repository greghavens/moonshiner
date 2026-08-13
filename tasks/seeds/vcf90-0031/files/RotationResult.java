import java.util.List;

/**
 * What one credential rotation run produced. The harness reads these fields directly, so the shape
 * is fixed here rather than inside the client.
 *
 * @param taskId                  the {@code id} of the credentials task returned by
 *                                {@code updateOrRotatePasswords}
 * @param status                  the terminal {@code status} the credentials task settled on
 * @param credentialIds           the ids of the credentials that were submitted for rotation,
 *                                sorted ascending
 * @param accessTokenRefreshCount how many times the client exchanged the refresh token for a new
 *                                access token during the run
 */
public record RotationResult(String taskId,
                             String status,
                             List<String> credentialIds,
                             int accessTokenRefreshCount) {

    public RotationResult {
        credentialIds = credentialIds == null ? List.of() : List.copyOf(credentialIds);
    }
}
