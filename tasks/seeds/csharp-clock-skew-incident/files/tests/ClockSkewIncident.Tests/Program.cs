using ClockSkewIncident;

internal static class Program
{
    private static readonly DateTimeOffset LocalObservedAt = At(10, 0);

    private static int Main()
    {
        var tests = new (string Name, Action Run)[]
        {
            ("ahead producer expires in the remote domain", AheadProducerExpiresInRemoteDomain),
            ("behind producer remains active in the remote domain", BehindProducerRemainsActiveInRemoteDomain),
            ("deadline boundary is inclusive after translation", DeadlineBoundaryIsInclusiveAfterTranslation),
            ("audit timestamps preserve their original domains", AuditTimestampsPreserveOriginalDomains),
            ("local age remains monotonic", LocalAgeRemainsMonotonic),
            ("remote deadline keeps precedence", RemoteDeadlineKeepsPrecedence)
        };

        foreach (var test in tests)
        {
            try
            {
                test.Run();
                Console.WriteLine($"PASS {test.Name}");
            }
            catch (Exception exception)
            {
                Console.Error.WriteLine($"FAIL {test.Name}");
                Console.Error.WriteLine(exception);
                return 1;
            }
        }

        return 0;
    }

    private static void AheadProducerExpiresInRemoteDomain()
    {
        var localNow = At(10, 2);
        var clock = new FakeClock(localNow, timestamp: TimeSpan.FromMinutes(1).Ticks);
        var skew = FromLog(producerTime: At(10, 5), consumerTime: LocalObservedAt);
        var tracked = Track(
            issuedAt: At(10, 0),
            expiresAt: At(10, 6),
            maximumLocalAge: TimeSpan.FromHours(1));

        var decision = new LeaseExpiryDecider(clock, skew).Evaluate(tracked);

        Equal(
            LeaseExpiryStatus.RemoteDeadlineElapsed,
            decision.Status,
            "Local 10:02 is remote 10:07 when the producer is five minutes ahead.");
    }

    private static void BehindProducerRemainsActiveInRemoteDomain()
    {
        var localNow = At(10, 2);
        var clock = new FakeClock(localNow, timestamp: TimeSpan.FromMinutes(1).Ticks);
        var skew = FromLog(producerTime: At(9, 55), consumerTime: LocalObservedAt);
        var tracked = Track(
            issuedAt: At(9, 54),
            expiresAt: At(9, 58),
            maximumLocalAge: TimeSpan.FromHours(1));

        var decision = new LeaseExpiryDecider(clock, skew).Evaluate(tracked);

        Equal(
            LeaseExpiryStatus.Active,
            decision.Status,
            "Local 10:02 is only remote 09:57 when the producer is five minutes behind.");
    }

    private static void DeadlineBoundaryIsInclusiveAfterTranslation()
    {
        var clock = new FakeClock(At(10, 1), timestamp: 0);
        var skew = FromLog(producerTime: At(10, 4), consumerTime: LocalObservedAt);
        var tracked = Track(
            issuedAt: At(10, 0),
            expiresAt: At(10, 5),
            maximumLocalAge: TimeSpan.FromHours(1));

        var decision = new LeaseExpiryDecider(clock, skew).Evaluate(tracked);

        Equal(LeaseExpiryStatus.RemoteDeadlineElapsed, decision.Status,
            "A translated remote now equal to the deadline must be expired.");
    }

    private static void AuditTimestampsPreserveOriginalDomains()
    {
        var localNow = At(10, 2);
        var issuedAt = At(10, 0);
        var expiresAt = At(10, 6);
        var elapsed = TimeSpan.FromSeconds(37);
        var clock = new FakeClock(localNow, elapsed.Ticks);
        var skew = FromLog(producerTime: At(10, 5), consumerTime: LocalObservedAt);
        var tracked = Track(issuedAt, expiresAt, TimeSpan.FromHours(1));

        var decision = new LeaseExpiryDecider(clock, skew).Evaluate(tracked);

        Equal(issuedAt, decision.Audit.RemoteIssuedAtUtc,
            "The authority's issue timestamp is immutable audit evidence.");
        Equal(expiresAt, decision.Audit.RemoteExpiresAtUtc,
            "The authority's expiry timestamp must not be rewritten for skew.");
        Equal(localNow, decision.Audit.EvaluatedAtLocalUtc,
            "Evaluation audit time remains the unadjusted injected local wall time.");
        Equal(elapsed, decision.Audit.LocalElapsed,
            "The audit duration must come from the monotonic clock.");
    }

    private static void LocalAgeRemainsMonotonic()
    {
        var start = 9_000L;
        var clock = new FakeClock(At(10, 2), start + TimeSpan.FromMinutes(4).Ticks);
        var skew = FromLog(producerTime: LocalObservedAt, consumerTime: LocalObservedAt);
        var tracked = Track(
            issuedAt: At(9, 59),
            expiresAt: At(11, 30),
            maximumLocalAge: TimeSpan.FromMinutes(5),
            receivedTimestamp: start);
        var decider = new LeaseExpiryDecider(clock, skew);

        Equal(LeaseExpiryStatus.Active, decider.Evaluate(tracked).Status,
            "Four monotonic minutes must remain inside the five-minute local limit.");

        clock.UtcNow = At(4, 0);
        clock.Timestamp = start + TimeSpan.FromMinutes(5).Ticks;
        var expired = decider.Evaluate(tracked);

        Equal(LeaseExpiryStatus.LocalAgeElapsed, expired.Status,
            "A wall-clock jump must not affect the local elapsed-time limit.");
        Equal(TimeSpan.FromMinutes(5), expired.Audit.LocalElapsed,
            "The monotonic boundary must be retained in audit evidence.");
    }

    private static void RemoteDeadlineKeepsPrecedence()
    {
        var start = 42L;
        var clock = new FakeClock(At(10, 2), start + TimeSpan.FromMinutes(20).Ticks);
        var skew = FromLog(producerTime: At(10, 5), consumerTime: LocalObservedAt);
        var tracked = Track(
            issuedAt: At(10, 0),
            expiresAt: At(10, 6),
            maximumLocalAge: TimeSpan.FromMinutes(10),
            receivedTimestamp: start);

        var decision = new LeaseExpiryDecider(clock, skew).Evaluate(tracked);

        Equal(LeaseExpiryStatus.RemoteDeadlineElapsed, decision.Status,
            "When both limits elapsed, the established remote-deadline status wins.");
    }

    private static DistributedLogClockSkew FromLog(
        DateTimeOffset producerTime,
        DateTimeOffset consumerTime) =>
        new(new DistributedLogTimestamps(producerTime, consumerTime));

    private static TrackedLease Track(
        DateTimeOffset issuedAt,
        DateTimeOffset expiresAt,
        TimeSpan maximumLocalAge,
        long receivedTimestamp = 0) =>
        new(
            new RemoteLease("lease-incident", issuedAt, expiresAt, maximumLocalAge),
            receivedTimestamp);

    private static DateTimeOffset At(int hour, int minute) =>
        new(2031, 6, 12, hour, minute, 0, TimeSpan.Zero);

    private static void Equal<T>(T expected, T actual, string message)
    {
        if (!EqualityComparer<T>.Default.Equals(expected, actual))
        {
            throw new TestFailureException(
                $"{message} Expected: {expected}; actual: {actual}.");
        }
    }

    private sealed class FakeClock : ISystemClock
    {
        public FakeClock(DateTimeOffset utcNow, long timestamp)
        {
            UtcNow = utcNow;
            Timestamp = timestamp;
        }

        public DateTimeOffset UtcNow { get; set; }

        public long Timestamp { get; set; }

        public long GetTimestamp() => Timestamp;

        public TimeSpan GetElapsedTime(long startingTimestamp) =>
            TimeSpan.FromTicks(checked(Timestamp - startingTimestamp));
    }

    private sealed class TestFailureException : Exception
    {
        public TestFailureException(string message)
            : base(message)
        {
        }
    }
}
