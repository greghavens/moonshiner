# JUnit Platform migration contract (protected local excerpt)

The current launcher discovers tests through engines.  Jupiter discovery owns
`@Test`, `@ParameterizedTest` plus every `@ValueSource` invocation, and
per-invocation `@BeforeEach` / `@AfterEach` lifecycle.  Selecting a class must
not silently reduce a parameterized method to one case.

JUnit 4 tests remain discoverable during a mixed-suite migration by
registering a Vintage-compatible engine.  For each legacy test the engine creates a fresh
instance, runs `@LegacyBefore`, invokes the test, and runs `@LegacyAfter` even when the body throws.
A legacy `expected` declaration passes only when the
body throws an assignable exception; returning normally is a failure.  An
unexpected exception remains a failure with its original type visible.

The move removes the old standalone-runner entry point.  The accepted suite is
one platform plan containing both current and legacy classes and the engines
that own their discovery semantics.
