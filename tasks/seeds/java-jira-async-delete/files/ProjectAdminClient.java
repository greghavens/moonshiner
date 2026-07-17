import java.net.http.HttpResponse;
import java.util.Map;

/**
 * Admin-side client for Jira Cloud project housekeeping. Reads project state
 * and archives projects; archival is the first stage of our retention
 * pipeline and has been in production for a while.
 */
public final class ProjectAdminClient {
    /** The slice of the v3 project resource this tooling cares about. */
    public record ProjectInfo(String id, String key, String name, boolean archived, boolean deleted) {}

    private final JiraAdminHttp http;

    public ProjectAdminClient(String baseUrl, String email, String apiToken) {
        this.http = new JiraAdminHttp(baseUrl, email, apiToken);
    }

    JiraAdminHttp http() {
        return http;
    }

    /** Fetches a project by ID or key, mapping the archival/trash flags. */
    public ProjectInfo getProject(String projectIdOrKey) {
        HttpResponse<String> resp = http.send("GET", "/rest/api/3/project/" + projectIdOrKey, null);
        if (resp.statusCode() != 200) {
            throw JiraApiException.of(resp.statusCode(), resp.body());
        }
        Map<String, Object> project = Json.object(Json.parse(resp.body()));
        return new ProjectInfo(
                String.valueOf(project.get("id")),
                (String) project.get("key"),
                (String) project.get("name"),
                Boolean.TRUE.equals(project.get("archived")),
                Boolean.TRUE.equals(project.get("deleted")));
    }

    /** Archives a project; Jira answers 204 with no body when it worked. */
    public void archiveProject(String projectIdOrKey) {
        HttpResponse<String> resp =
                http.send("POST", "/rest/api/3/project/" + projectIdOrKey + "/archive", null);
        if (resp.statusCode() != 204) {
            throw JiraApiException.of(resp.statusCode(), resp.body());
        }
    }
}
