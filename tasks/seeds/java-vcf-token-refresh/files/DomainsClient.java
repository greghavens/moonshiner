import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/**
 * Read side of GET /v1/domains: unwraps PageOfDomain and keeps the fields the
 * inventory jobs care about, API resource ids untouched.
 */
public final class DomainsClient {

    /** One element of PageOfDomain, projected. */
    public record Domain(String id, String name, String type, String status) {
    }

    private final VcfTransport transport;

    public DomainsClient(VcfTransport transport) {
        this.transport = transport;
    }

    public List<Domain> listDomains() {
        Map<String, Object> page = Json.object(transport.get("/v1/domains"));
        List<Domain> out = new ArrayList<>();
        for (Object element : Json.array(page.get("elements"))) {
            Map<String, Object> domain = Json.object(element);
            out.add(new Domain(
                    (String) domain.get("id"),
                    (String) domain.get("name"),
                    (String) domain.get("type"),
                    (String) domain.get("status")));
        }
        return out;
    }
}
