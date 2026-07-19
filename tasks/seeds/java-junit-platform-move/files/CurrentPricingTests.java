public final class CurrentPricingTests {
    @PlatformContract.BeforeEach
    void openTransaction() { TestProbe.record("current:before"); }

    @PlatformContract.AfterEach
    void closeTransaction() { TestProbe.record("current:after"); }

    @PlatformContract.Test
    void basePrice() { TestProbe.record("current:base"); }

    @PlatformContract.ParameterizedTest
    @PlatformContract.ValueSource(ints = {2, 4, 8})
    void discountTier(int tier) {
        TestProbe.record("current:tier:" + tier);
        if (tier % 2 != 0) throw new AssertionError("fixture tier must be even");
    }
}

