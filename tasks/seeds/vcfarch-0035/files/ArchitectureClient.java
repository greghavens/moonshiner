/**
 * Emits the Project Northstar architecture artifacts.
 *
 * This is the only production source file in the exercise.  The acceptance
 * harness calls the two methods directly as well as exercising this CLI.
 */
public final class ArchitectureClient {
    private ArchitectureClient() {}

    public static String greenfield() {
        return "{}";
    }

    public static String migration(String estateInventoryJson) {
        return "{}";
    }

    public static void main(String[] args) throws Exception {
        if (args.length == 1 && args[0].equals("greenfield")) {
            System.out.println(greenfield());
            return;
        }
        if (args.length == 2 && args[0].equals("migration")) {
            String inventory = java.nio.file.Files.readString(java.nio.file.Path.of(args[1]));
            System.out.println(migration(inventory));
            return;
        }
        throw new IllegalArgumentException(
                "usage: ArchitectureClient greenfield | ArchitectureClient migration <estate.json>");
    }
}
