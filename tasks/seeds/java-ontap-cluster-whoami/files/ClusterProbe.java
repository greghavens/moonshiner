import java.util.Map;

/** Reads cluster identity through the stable /api/cluster resource. */
public final class ClusterProbe {

    public static final String CLUSTER_QUERY = "/api/cluster?fields=name,version";

    private ClusterProbe() {
    }

    public static ClusterIdentity probe(OntapRestClient client) throws Exception {
        OntapRestClient.Response response = client.get(CLUSTER_QUERY);
        if (response.status != 200) {
            throw OntapRestClient.apiError(response);
        }
        Map<String, Object> doc = Json.object(Json.parse(response.body));
        Map<String, Object> version = Json.object(doc.get("version"));
        return new ClusterIdentity(
                (String) doc.get("name"),
                (String) version.get("full"),
                ((Number) version.get("generation")).intValue(),
                ((Number) version.get("major")).intValue(),
                ((Number) version.get("minor")).intValue());
    }
}
