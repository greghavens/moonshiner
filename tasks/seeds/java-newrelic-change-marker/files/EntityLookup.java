import java.io.IOException;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Resolves exactly one entity GUID through actor.entitySearch. The search
 * string is assembled from validated parts only; the GraphQL document is a
 * constant and user input travels exclusively through variables.
 */
public final class EntityLookup {

    public static final class EntityNotFoundException extends RuntimeException {
        public EntityNotFoundException(String message) {
            super(message);
        }
    }

    public static final class AmbiguousEntityException extends RuntimeException {
        public AmbiguousEntityException(String message) {
            super(message);
        }
    }

    static final String SEARCH_DOCUMENT = """
        query($query: String!) {
          actor {
            entitySearch(query: $query) {
              results {
                entities {
                  guid
                  name
                  entityType
                  domain
                }
                nextCursor
              }
            }
          }
        }""";

    private final NerdGraphClient client;

    public EntityLookup(NerdGraphClient client) {
        this.client = client;
    }

    /**
     * Returns the GUID of the single entity matching name and domain.
     * Zero matches raise EntityNotFoundException; more than one match (or
     * an unexhausted listing) raises AmbiguousEntityException.
     */
    public String resolveSingleGuid(String name, String domain)
            throws IOException, InterruptedException {
        validateTerm(name, "entity name");
        validateTerm(domain, "entity domain");
        Map<String, Object> variables = new LinkedHashMap<>();
        variables.put("query", "name = '" + name + "' AND domain = '" + domain + "'");
        Map<String, Object> envelope = client.execute(SEARCH_DOCUMENT, variables);
        Map<String, Object> results = dig(envelope, "data", "actor", "entitySearch", "results");
        if (results == null) {
            throw new EntityNotFoundException("entity search returned no results block");
        }
        @SuppressWarnings("unchecked")
        List<Map<String, Object>> entities = (List<Map<String, Object>>) results.get("entities");
        Object nextCursor = results.get("nextCursor");
        if (entities == null || entities.isEmpty()) {
            throw new EntityNotFoundException(
                "no entity named '" + name + "' in domain " + domain);
        }
        if (entities.size() > 1 || nextCursor != null) {
            throw new AmbiguousEntityException(
                "entity name '" + name + "' in domain " + domain
                    + " matches more than one entity");
        }
        return (String) entities.get(0).get("guid");
    }

    private static void validateTerm(String value, String what) {
        if (value == null || value.isEmpty()) {
            throw new IllegalArgumentException(what + " must not be empty");
        }
        if (value.indexOf('\'') >= 0 || value.indexOf('\\') >= 0) {
            throw new IllegalArgumentException(
                what + " must not contain quotes or backslashes");
        }
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> dig(Map<String, Object> doc, String... keys) {
        Object cur = doc;
        for (String key : keys) {
            if (!(cur instanceof Map)) {
                return null;
            }
            cur = ((Map<String, Object>) cur).get(key);
        }
        return cur instanceof Map ? (Map<String, Object>) cur : null;
    }
}
