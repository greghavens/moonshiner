import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.time.Duration;
import java.util.List;

/**
 * Small dependency-free client for the VCF 9.1 SDDC Manager credential
 * operations selected in docs/contract.json.
 */
public final class SddcManagerClient {
    public record PasswordChange(
            String resourceType,
            String resourceName,
            String resourceId,
            String username,
            String password,
            String credentialType,
            String accountType) {
    }

    public record StepResult(
            String resourceKey,
            String taskId,
            String status,
            String errorCode,
            String errorMessage) {
    }

    public record ChangeReport(List<StepResult> steps) {
        public ChangeReport {
            steps = List.copyOf(steps);
        }

        public boolean successful() {
            return !steps.isEmpty()
                    && steps.stream().allMatch(step -> "SUCCESSFUL".equals(step.status()));
        }
    }

    public static final class ApiException extends IOException {
        private final int statusCode;
        private final String responseBody;

        public ApiException(int statusCode, String responseBody) {
            super("SDDC Manager returned HTTP " + statusCode);
            this.statusCode = statusCode;
            this.responseBody = responseBody;
        }

        public int statusCode() {
            return statusCode;
        }

        public String responseBody() {
            return responseBody;
        }
    }

    public SddcManagerClient(
            URI baseUri,
            String bearerToken,
            Duration pollDelay,
            int maxPollAttempts) {
        throw new UnsupportedOperationException("TODO");
    }

    public ChangeReport updatePasswordsSequentially(List<PasswordChange> changes)
            throws IOException, InterruptedException {
        throw new UnsupportedOperationException("TODO");
    }
}
