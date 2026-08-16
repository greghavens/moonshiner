import java.io.IOException;
import java.net.URI;

/** A small VCF Automation client implemented only with the Java standard library. */
public final class VcfAutomationClient {
    public record DeploymentResult(String projectId, String catalogItemId, String deploymentId) {}

    public VcfAutomationClient(URI baseUri, String basicAuthorization, String refreshToken) {
        // TODO: initialize the client.
    }

    public DeploymentResult deploy(String projectName, String catalogItemName, String deploymentName)
            throws IOException, InterruptedException {
        throw new UnsupportedOperationException("TODO: implement the VCF Automation workflow");
    }
}
