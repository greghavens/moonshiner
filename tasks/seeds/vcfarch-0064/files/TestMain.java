public final class TestMain {
    private TestMain() {
    }

    public static void main(String[] args) {
        String artifact = ArchitectureClient.createArchitecture();
        if (artifact == null || artifact.isBlank()) {
            throw new IllegalStateException("ArchitectureClient returned no artifact");
        }
        System.out.print(artifact);
    }
}
