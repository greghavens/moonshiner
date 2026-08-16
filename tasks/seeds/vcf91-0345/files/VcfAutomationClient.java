import java.io.IOException;
import java.net.URI;
import java.util.List;

/**
 * Minimal VCF Automation 9.1 Project Service client.
 *
 * Implement this class using only JDK APIs. Keep it as a single source file.
 */
public final class VcfAutomationClient {
    public record Project(String id, String name, String description) {}

    public VcfAutomationClient(URI baseUri, String authorization) {
        // TODO
    }

    public void rotateCredential(String authorization) {
        // TODO
    }

    public List<Project> listProjects() throws IOException, InterruptedException {
        return List.of();
    }
}
