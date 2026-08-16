import java.io.IOException;
import java.net.URI;
import java.util.List;

/** A small VCF Automation deployment resource-action client. */
public final class VcfAutomationClient {
    public record Resource(String id, String name, String type) {}

    public record ActionRequest(String id, String actionId, String status) {}

    public static final class PrecheckFailedException extends IOException {
        public PrecheckFailedException(String message) {
            super(message);
        }

        public PrecheckFailedException(String message, Throwable cause) {
            super(message, cause);
        }
    }

    public VcfAutomationClient(URI baseUri, String bearerToken) {
        throw new UnsupportedOperationException("TODO");
    }

    public List<Resource> listDeploymentResources(String deploymentId)
            throws IOException, InterruptedException {
        throw new UnsupportedOperationException("TODO");
    }

    public ActionRequest submitResourceActionIfAvailable(
            String deploymentId,
            String resourceId,
            String actionId,
            String reason) throws IOException, InterruptedException {
        throw new UnsupportedOperationException("TODO");
    }
}
