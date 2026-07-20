package com.moonshiner.ledger;

import com.moonshiner.ledger.LedgerAmountMigration.PersistedAmount;

import java.util.ArrayList;
import java.util.List;

public final class LedgerAmountMigrationTest {
    private LedgerAmountMigrationTest() {
    }

    public static void main(String[] args) {
        List<String> failures = new ArrayList<String>();

        run(failures, "migrates canonical minor units", new CheckedTest() {
            @Override
            public void run() {
                assertMigrates("0", "0.00");
                assertMigrates("1", "0.01");
                assertMigrates("12345", "123.45");
                assertMigrates("-7", "-0.07");
                assertMigrates("-1200", "-12.00");
            }
        });

        run(failures, "handles signed 64-bit boundaries", new CheckedTest() {
            @Override
            public void run() {
                assertMigrates("9223372036854775807", "92233720368547758.07");
                assertMigrates("-9223372036854775808", "-92233720368547758.08");
                assertEquals(new PersistedAmount(2, "92233720368547758.07"),
                        LedgerAmountMigration.migrate(
                                new PersistedAmount(2, "92233720368547758.07")));
                assertEquals(new PersistedAmount(2, "-92233720368547758.08"),
                        LedgerAmountMigration.migrate(
                                new PersistedAmount(2, "-92233720368547758.08")));
                expectIllegalArgument(new CheckedTest() {
                    @Override
                    public void run() {
                        LedgerAmountMigration.migrate(
                                new PersistedAmount(1, "9223372036854775808"));
                    }
                });
                expectIllegalArgument(new CheckedTest() {
                    @Override
                    public void run() {
                        LedgerAmountMigration.migrate(
                                new PersistedAmount(1, "-9223372036854775809"));
                    }
                });
                expectIllegalArgument(new CheckedTest() {
                    @Override
                    public void run() {
                        LedgerAmountMigration.toMinorUnits("92233720368547758.08");
                    }
                });
                expectIllegalArgument(new CheckedTest() {
                    @Override
                    public void run() {
                        LedgerAmountMigration.toMinorUnits("-92233720368547758.09");
                    }
                });
                expectIllegalArgument(new CheckedTest() {
                    @Override
                    public void run() {
                        LedgerAmountMigration.migrate(
                                new PersistedAmount(2, "92233720368547758.08"));
                    }
                });
                expectIllegalArgument(new CheckedTest() {
                    @Override
                    public void run() {
                        LedgerAmountMigration.migrate(
                                new PersistedAmount(2, "-92233720368547758.09"));
                    }
                });
            }
        });

        run(failures, "mapping is exactly reversible", new CheckedTest() {
            @Override
            public void run() {
                long[] values = {
                    Long.MIN_VALUE, -10001L, -1L, 0L, 1L, 10001L, Long.MAX_VALUE
                };
                for (long value : values) {
                    String decimal = LedgerAmountMigration.fromMinorUnits(value);
                    assertEquals(value, LedgerAmountMigration.toMinorUnits(decimal));
                }
            }
        });

        run(failures, "does not convert version 2 rows twice", new CheckedTest() {
            @Override
            public void run() {
                PersistedAmount alreadyMigrated = new PersistedAmount(2, "100.00");
                assertEquals(alreadyMigrated, LedgerAmountMigration.migrate(alreadyMigrated));

                PersistedAmount cents = new PersistedAmount(1, "12345");
                PersistedAmount decimal = new PersistedAmount(2, "123.45");
                assertEquals(new PersistedAmount(2, "123.45"),
                        LedgerAmountMigration.migrate(cents));
                assertEquals(decimal, LedgerAmountMigration.migrate(decimal));

                PersistedAmount once = LedgerAmountMigration.migrate(cents);
                assertEquals(once, LedgerAmountMigration.migrate(once));

                PersistedAmount negativeFraction = new PersistedAmount(2, "-0.01");
                assertEquals(negativeFraction,
                        LedgerAmountMigration.migrate(negativeFraction));
            }
        });

        run(failures, "rejects malformed and ambiguous representations", new CheckedTest() {
            @Override
            public void run() {
                String[] invalidMinorUnits = {
                    null, "", " ", "+1", "01", "-0", "1.00", "1e2", " 1"
                };
                for (final String value : invalidMinorUnits) {
                    expectIllegalArgument(new CheckedTest() {
                        @Override
                        public void run() {
                            LedgerAmountMigration.migrate(new PersistedAmount(1, value));
                        }
                    });
                }

                String[] invalidDecimals = {
                    null, "", "1", "1.2", "1.000", "+1.00", "01.00", "-0.00",
                    ".50", "1e2", " 1.00"
                };
                for (final String value : invalidDecimals) {
                    expectIllegalArgument(new CheckedTest() {
                        @Override
                        public void run() {
                            LedgerAmountMigration.migrate(new PersistedAmount(2, value));
                        }
                    });
                    expectIllegalArgument(new CheckedTest() {
                        @Override
                        public void run() {
                            LedgerAmountMigration.toMinorUnits(value);
                        }
                    });
                }

                expectIllegalArgument(new CheckedTest() {
                    @Override
                    public void run() {
                        LedgerAmountMigration.migrate(new PersistedAmount(3, "100"));
                    }
                });
                expectIllegalArgument(new CheckedTest() {
                    @Override
                    public void run() {
                        LedgerAmountMigration.migrate(new PersistedAmount(0, "100"));
                    }
                });
            }
        });

        run(failures, "rejects a null persisted row", new CheckedTest() {
            @Override
            public void run() {
                expectNullPointer(new CheckedTest() {
                    @Override
                    public void run() {
                        LedgerAmountMigration.migrate(null);
                    }
                });
            }
        });

        if (!failures.isEmpty()) {
            for (String failure : failures) {
                System.err.println(failure);
            }
            throw new AssertionError(failures.size() + " test(s) failed");
        }

        System.out.println("All ledger amount migration tests passed.");
    }

    private static void assertMigrates(String storedMinorUnits, String expectedDecimal) {
        PersistedAmount actual = LedgerAmountMigration.migrate(
                new PersistedAmount(1, storedMinorUnits));
        assertEquals(new PersistedAmount(2, expectedDecimal), actual);
    }

    private static void run(List<String> failures, String name, CheckedTest test) {
        try {
            test.run();
        } catch (Throwable failure) {
            failures.add(name + ": " + failure);
        }
    }

    private static void expectIllegalArgument(CheckedTest test) {
        try {
            test.run();
            throw new AssertionError("Expected IllegalArgumentException");
        } catch (IllegalArgumentException expected) {
            // Expected.
        }
    }

    private static void expectNullPointer(CheckedTest test) {
        try {
            test.run();
            throw new AssertionError("Expected NullPointerException");
        } catch (NullPointerException expected) {
            // Expected.
        }
    }

    private static void assertEquals(long expected, long actual) {
        if (expected != actual) {
            throw new AssertionError("expected <" + expected + "> but was <" + actual + ">");
        }
    }

    private static void assertEquals(Object expected, Object actual) {
        if (expected == null ? actual != null : !expected.equals(actual)) {
            throw new AssertionError("expected <" + expected + "> but was <" + actual + ">");
        }
    }

    private interface CheckedTest {
        void run();
    }
}
