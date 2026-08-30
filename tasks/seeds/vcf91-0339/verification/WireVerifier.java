import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

/** Small, deterministic guard that keeps the protected mock pinned to its docs. */
final class WireVerifier {
    private WireVerifier() {
    }

    static void verifyProtectedContract() throws IOException {
        String contract = Files.readString(Path.of("docs", "contract.json"), StandardCharsets.UTF_8);
        require(contract.contains("\"kind\": \"reference-documentation\""),
                "contract must identify reference-documentation provenance");
        require(contract.contains("not derived from a published specification"),
                "contract must disclaim a published specification source");
        require(count(contract, "\"operationId\":") == 3,
                "contract must name exactly three operations");
        requireOperation(contract, "requestCatalogItemInstances", "POST",
                "/catalog/api/items/{id}/request");
        requireOperation(contract, "getDeploymentById", "GET",
                "/deployment/api/deployments/{deploymentId}");
        requireOperation(contract, "getDeployments", "GET",
                "/deployment/api/deployments");
        require(contract.contains("\"non_terminal_statuses\": [\"CREATE_INPROGRESS\"]"),
                "polling non-terminal status drifted");
        require(contract.contains("\"terminal_statuses\": [\"CREATE_SUCCESSFUL\", \"CREATE_FAILED\"]"),
                "polling terminal statuses drifted");
        require(contract.contains("\"client_output_order\": [\"name ASC\", \"id ASC\"]"),
                "local collection ordering drifted");

        String sources = Files.readString(
                Path.of("docs", "official_sources.json"), StandardCharsets.UTF_8);
        require(count(sources, "\"operation\":") == 3,
                "official_sources must have one record per operation");
        require(count(sources, "\"fetched_date\": \"2026-08-15\"") == 4,
                "every source and the provenance record need the fetched date");
        require(sources.contains("developer.broadcom.com/xapis/vm-apps-org-catalog/latest/catalog/api/items/id/request/post/"),
                "catalog request reference URL missing");
        require(sources.contains("developer.broadcom.com/xapis/vm-apps-org-deployment/latest/deployment/api/deployments/deploymentId/get/"),
                "deployment detail reference URL missing");
        require(sources.contains("developer.broadcom.com/xapis/vm-apps-org-deployment/latest/deployment/api/deployments/get/"),
                "deployment collection reference URL missing");
    }

    private static void requireOperation(String document, String operationId, String method, String path) {
        int operation = document.indexOf("\"operationId\": \"" + operationId + "\"");
        require(operation >= 0, "missing operation " + operationId);
        int next = document.indexOf("\"operationId\":", operation + 1);
        String block = document.substring(operation, next < 0 ? document.length() : next);
        require(block.contains("\"method\": \"" + method + "\""),
                operationId + " method drifted");
        require(block.contains("\"path\": \"" + path + "\""),
                operationId + " path drifted");
    }

    private static int count(String document, String needle) {
        int count = 0;
        int offset = 0;
        while ((offset = document.indexOf(needle, offset)) >= 0) {
            count++;
            offset += needle.length();
        }
        return count;
    }

    static void require(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }
}
