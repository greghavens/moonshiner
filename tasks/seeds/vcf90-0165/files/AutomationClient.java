import java.io.IOException;
import java.net.URI;

/** JDK-only client for the small VCF Automation contract used by this project. */
public final class AutomationClient {
    public record ProvisioningResult(String projectId, String deploymentId) {}

    public AutomationClient(URI baseUri, String refreshToken) {
        // TODO: initialize the HTTP client and authentication state.
    }

    public ProvisioningResult provision(
            String projectName,
            String projectDescription,
            String deploymentName,
            String deploymentDescription) throws IOException, InterruptedException {
        throw new UnsupportedOperationException("TODO: implement VCF Automation provisioning");
    }
}
