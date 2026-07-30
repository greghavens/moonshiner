import java.io.IOException;
import java.time.Duration;

/**
 * Complete this dependency-free VCF 9.1 vCenter Automation client.
 */
public final class VcenterCloneClient {
    public record CloneOutcome(
            String taskId,
            String virtualMachineId,
            int polls) {
    }

    public static final class VcenterApiException extends IOException {
        private final int statusCode;
        private final String responseBody;

        public VcenterApiException(int statusCode, String responseBody) {
            super("vCenter API returned HTTP " + statusCode);
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

    @FunctionalInterface
    public interface Sleeper {
        void sleep(Duration duration) throws InterruptedException;
    }

    public VcenterCloneClient(
            String apiRoot,
            String sessionId,
            Duration requestTimeout,
            Duration pollInterval,
            int maxPolls) {
        throw new UnsupportedOperationException("TODO");
    }

    public VcenterCloneClient(
            String apiRoot,
            String sessionId,
            Duration requestTimeout,
            Duration pollInterval,
            int maxPolls,
            Sleeper sleeper) {
        throw new UnsupportedOperationException("TODO");
    }

    public CloneOutcome cloneAndWait(String source, String name)
            throws IOException, InterruptedException {
        throw new UnsupportedOperationException("TODO");
    }
}
