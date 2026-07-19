public final class LegacyInventoryTests {
    @PlatformContract.LegacyBefore
    void seedInventory() { TestProbe.record("legacy:before"); }

    @PlatformContract.LegacyAfter
    void clearInventory() { TestProbe.record("legacy:after"); }

    @PlatformContract.LegacyTest
    void availableSkuLoads() { TestProbe.record("legacy:available"); }

    @PlatformContract.LegacyTest(expected = IllegalArgumentException.class)
    void missingSkuKeepsExpectedFailureSemantics() {
        TestProbe.record("legacy:expected-body");
        throw new IllegalArgumentException("missing fixture sku");
    }
}

