/** Cluster name and ONTAP version, from GET /api/cluster?fields=name,version. */
public final class ClusterIdentity {
    public final String name;
    public final String versionFull;
    public final int generation;
    public final int major;
    public final int minor;

    public ClusterIdentity(String name, String versionFull, int generation, int major, int minor) {
        this.name = name;
        this.versionFull = versionFull;
        this.generation = generation;
        this.major = major;
        this.minor = minor;
    }
}
