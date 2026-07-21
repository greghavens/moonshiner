using SagaCompensation;

var tests = new (string Name, Action Body)[]
{
    ("late forward success is resolved before compensation", LateSuccessIsResolvedFirst),
    ("an unresolved step only blocks itself and earlier steps", AmbiguousStepIsABarrier),
    ("restart reuses the durable compensation id", RestartReusesCompensationId),
    ("overlapping recovery workers share one compensation id", OverlappingWorkersShareCompensationId),
    ("definite failure releases earlier compensation", DefiniteFailureReleasesEarlierStep),
    ("successful steps compensate once in reverse order", SuccessfulStepsCompensateInReverseOrder),
    ("completed compensation is restart safe", CompletedCompensationIsRestartSafe)
};

int failed = 0;
foreach ((string name, Action body) in tests)
{
    try
    {
        body();
        Console.WriteLine($"PASS {name}");
    }
    catch (Exception exception)
    {
        failed++;
        Console.Error.WriteLine($"FAIL {name}: {exception.Message}");
    }
}

Console.WriteLine($"{tests.Length - failed}/{tests.Length} tests passed");
return failed == 0 ? 0 : 1;

static void LateSuccessIsResolvedFirst()
{
    var store = new InMemorySagaStore();
    var compensator = new RecordingIdempotentCompensator();
    var coordinator = new SagaCoordinator(store, compensator);

    coordinator.StartSaga("order-late", "reserve-inventory", "charge-payment", "send-receipt");
    coordinator.MarkStepStarted("order-late", "reserve-inventory");
    coordinator.RecordStepSucceeded("order-late", "reserve-inventory");
    coordinator.MarkStepStarted("order-late", "charge-payment");
    coordinator.BeginCompensation("order-late");

    CompensationRunResult waiting = coordinator.ResumeCompensation("order-late");

    Check.Equal(CompensationRunResult.WaitingForForwardOutcome, waiting);
    Check.SequenceEqual(Array.Empty<string>(), compensator.AppliedSteps);
    Check.Equal(SagaStatus.Compensating, store.Load("order-late").Status);

    coordinator.RecordStepSucceeded("order-late", "charge-payment");
    CompensationRunResult completed = coordinator.ResumeCompensation("order-late");

    Check.Equal(CompensationRunResult.Completed, completed);
    Check.SequenceEqual(
        new[] { "charge-payment", "reserve-inventory" },
        compensator.AppliedSteps);
    Check.Equal(SagaStatus.Compensated, store.Load("order-late").Status);
}

static void RestartReusesCompensationId()
{
    var store = new InMemorySagaStore();
    var compensator = new RecordingIdempotentCompensator
    {
        FailAfterNextNewApplication = true
    };
    var firstProcess = new SagaCoordinator(store, compensator);

    firstProcess.StartSaga("order-restart", "reserve-inventory", "charge-payment");
    firstProcess.MarkStepStarted("order-restart", "reserve-inventory");
    firstProcess.RecordStepSucceeded("order-restart", "reserve-inventory");
    firstProcess.BeginCompensation("order-restart");

    Check.Throws<CompensatorUnavailableException>(
        () => firstProcess.ResumeCompensation("order-restart"));

    SagaStepState interrupted = store.Load("order-restart").Steps[0];
    Check.Equal(SagaStepStatus.Compensating, interrupted.Status);
    Check.NotNullOrWhiteSpace(interrupted.CompensationId);
    Check.Equal(1, compensator.AppliedSteps.Count);

    var restartedProcess = new SagaCoordinator(store, compensator);
    CompensationRunResult result = restartedProcess.ResumeCompensation("order-restart");

    Check.Equal(CompensationRunResult.Completed, result);
    Check.Equal(2, compensator.InvocationIds.Count);
    Check.Equal(compensator.InvocationIds[0], compensator.InvocationIds[1]);
    Check.SequenceEqual(new[] { "reserve-inventory" }, compensator.AppliedSteps);
    Check.Equal(SagaStepStatus.Compensated, store.Load("order-restart").Steps[0].Status);
}

static void AmbiguousStepIsABarrier()
{
    var store = new InMemorySagaStore();
    var compensator = new RecordingIdempotentCompensator();
    var coordinator = new SagaCoordinator(store, compensator);

    coordinator.StartSaga("order-barrier", "reserve-inventory", "charge-payment", "send-receipt");
    Succeed(coordinator, "order-barrier", "reserve-inventory");
    coordinator.MarkStepStarted("order-barrier", "charge-payment");
    Succeed(coordinator, "order-barrier", "send-receipt");
    coordinator.BeginCompensation("order-barrier");

    CompensationRunResult waiting = coordinator.ResumeCompensation("order-barrier");

    Check.Equal(CompensationRunResult.WaitingForForwardOutcome, waiting);
    Check.SequenceEqual(new[] { "send-receipt" }, compensator.AppliedSteps);
    SagaState interrupted = store.Load("order-barrier");
    Check.Equal(SagaStatus.Compensating, interrupted.Status);
    Check.Equal(SagaStepStatus.Succeeded, interrupted.Steps[0].Status);
    Check.Equal(SagaStepStatus.Started, interrupted.Steps[1].Status);
    Check.Equal(SagaStepStatus.Compensated, interrupted.Steps[2].Status);

    coordinator.RecordStepFailed("order-barrier", "charge-payment");
    CompensationRunResult completed = coordinator.ResumeCompensation("order-barrier");

    Check.Equal(CompensationRunResult.Completed, completed);
    Check.SequenceEqual(
        new[] { "send-receipt", "reserve-inventory" },
        compensator.AppliedSteps);
    Check.Equal(SagaStatus.Compensated, store.Load("order-barrier").Status);
}

static void DefiniteFailureReleasesEarlierStep()
{
    var store = new InMemorySagaStore();
    var compensator = new RecordingIdempotentCompensator();
    var coordinator = new SagaCoordinator(store, compensator);

    coordinator.StartSaga("order-failed", "reserve-inventory", "charge-payment");
    coordinator.MarkStepStarted("order-failed", "reserve-inventory");
    coordinator.RecordStepSucceeded("order-failed", "reserve-inventory");
    coordinator.MarkStepStarted("order-failed", "charge-payment");
    coordinator.BeginCompensation("order-failed");
    coordinator.RecordStepFailed("order-failed", "charge-payment");

    CompensationRunResult result = coordinator.ResumeCompensation("order-failed");

    Check.Equal(CompensationRunResult.Completed, result);
    Check.SequenceEqual(new[] { "reserve-inventory" }, compensator.AppliedSteps);
    Check.Equal(SagaStatus.Compensated, store.Load("order-failed").Status);
}

static void OverlappingWorkersShareCompensationId()
{
    var store = new InMemorySagaStore();
    var compensator = new RecordingIdempotentCompensator();
    var firstWorker = new SagaCoordinator(store, compensator);
    var secondWorker = new SagaCoordinator(store, compensator);

    firstWorker.StartSaga("order-overlap", "reserve-inventory");
    Succeed(firstWorker, "order-overlap", "reserve-inventory");
    firstWorker.BeginCompensation("order-overlap");
    compensator.AfterNextNewApplication =
        () => secondWorker.ResumeCompensation("order-overlap");

    CompensationRunResult result = firstWorker.ResumeCompensation("order-overlap");

    Check.Equal(CompensationRunResult.Completed, result);
    Check.Equal(2, compensator.InvocationIds.Count);
    Check.Equal(compensator.InvocationIds[0], compensator.InvocationIds[1]);
    Check.SequenceEqual(new[] { "reserve-inventory" }, compensator.AppliedSteps);
    Check.Equal(SagaStatus.Compensated, store.Load("order-overlap").Status);
}

static void SuccessfulStepsCompensateInReverseOrder()
{
    var store = new InMemorySagaStore();
    var compensator = new RecordingIdempotentCompensator();
    var coordinator = new SagaCoordinator(store, compensator);

    coordinator.StartSaga("order-reverse", "reserve-inventory", "charge-payment", "book-shipment");
    Succeed(coordinator, "order-reverse", "reserve-inventory");
    Succeed(coordinator, "order-reverse", "charge-payment");
    Succeed(coordinator, "order-reverse", "book-shipment");
    coordinator.BeginCompensation("order-reverse");

    coordinator.ResumeCompensation("order-reverse");

    Check.SequenceEqual(
        new[] { "book-shipment", "charge-payment", "reserve-inventory" },
        compensator.AppliedSteps);
    SagaState final = store.Load("order-reverse");
    Check.Equal(SagaStatus.Compensated, final.Status);
    Check.True(final.Steps.All(step => step.Status == SagaStepStatus.Compensated));
}

static void CompletedCompensationIsRestartSafe()
{
    var store = new InMemorySagaStore();
    var compensator = new RecordingIdempotentCompensator();
    var firstProcess = new SagaCoordinator(store, compensator);

    firstProcess.StartSaga("order-complete", "reserve-inventory");
    Succeed(firstProcess, "order-complete", "reserve-inventory");
    firstProcess.BeginCompensation("order-complete");
    firstProcess.ResumeCompensation("order-complete");

    var restartedProcess = new SagaCoordinator(store, compensator);
    CompensationRunResult result = restartedProcess.ResumeCompensation("order-complete");

    Check.Equal(CompensationRunResult.Completed, result);
    Check.Equal(1, compensator.InvocationIds.Count);
    Check.Equal(1, compensator.AppliedSteps.Count);
}

static void Succeed(SagaCoordinator coordinator, string sagaId, string stepName)
{
    coordinator.MarkStepStarted(sagaId, stepName);
    coordinator.RecordStepSucceeded(sagaId, stepName);
}

internal sealed class RecordingIdempotentCompensator : IStepCompensator
{
    private readonly HashSet<string> _appliedIds = new(StringComparer.Ordinal);

    public bool FailAfterNextNewApplication { get; set; }

    public Action? AfterNextNewApplication { get; set; }

    public List<string> InvocationIds { get; } = new();

    public List<string> AppliedSteps { get; } = new();

    public void Compensate(string sagaId, string stepName, string compensationId)
    {
        InvocationIds.Add(compensationId);
        if (_appliedIds.Add(compensationId))
        {
            AppliedSteps.Add(stepName);
            Action? callback = AfterNextNewApplication;
            AfterNextNewApplication = null;
            callback?.Invoke();

            if (FailAfterNextNewApplication)
            {
                FailAfterNextNewApplication = false;
                throw new CompensatorUnavailableException();
            }
        }
    }
}

internal sealed class CompensatorUnavailableException : Exception
{
}

internal static class Check
{
    public static void Equal<T>(T expected, T actual)
    {
        if (!EqualityComparer<T>.Default.Equals(expected, actual))
        {
            throw new InvalidOperationException($"Expected <{expected}> but found <{actual}>.");
        }
    }

    public static void SequenceEqual<T>(IEnumerable<T> expected, IEnumerable<T> actual)
    {
        if (!expected.SequenceEqual(actual))
        {
            throw new InvalidOperationException(
                $"Expected [{string.Join(", ", expected)}] but found [{string.Join(", ", actual)}].");
        }
    }

    public static void NotNullOrWhiteSpace(string? value)
    {
        if (string.IsNullOrWhiteSpace(value))
        {
            throw new InvalidOperationException("Expected a durable compensation id.");
        }
    }

    public static void True(bool condition)
    {
        if (!condition)
        {
            throw new InvalidOperationException("Expected condition to be true.");
        }
    }

    public static void Throws<TException>(Action action)
        where TException : Exception
    {
        try
        {
            action();
        }
        catch (TException)
        {
            return;
        }

        throw new InvalidOperationException($"Expected {typeof(TException).Name}.");
    }
}
