public final class LegacyExpectedFailureCounterexample {
    @PlatformContract.LegacyBefore
    void before() { TestProbe.record("counterexample:before"); }

    @PlatformContract.LegacyAfter
    void after() { TestProbe.record("counterexample:after"); }

    @PlatformContract.LegacyTest(expected = IllegalStateException.class)
    void expectedExceptionThatNeverArrives() { TestProbe.record("counterexample:body"); }
}

