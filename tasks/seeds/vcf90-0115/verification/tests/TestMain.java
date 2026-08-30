import java.net.URI;

public final class TestMain {
    public static void main(String[] args) throws Exception {
        if (args.length != 2) {
            throw new IllegalArgumentException("usage: TestMain BASE_URL PAGE_SIZE");
        }

        VcfInstallerClient client = new VcfInstallerClient(
                URI.create(args[0]), Integer.parseInt(args[1]));
        for (VcfInstallerClient.Task task : client.listAllTasks()) {
            System.out.printf(
                    "%s\t%s\t%s\t%s%n",
                    task.id(), task.creationTimestamp(), task.status(), task.name());
        }
    }
}
