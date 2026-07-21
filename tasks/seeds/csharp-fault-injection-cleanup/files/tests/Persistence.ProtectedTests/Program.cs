using Persistence;
using System.Reflection;

return ProtectedTests.RunAll();

internal static class ProtectedTests
{
    private static readonly PersistenceStep[] FaultableSteps =
    [
        PersistenceStep.Invoice,
        PersistenceStep.LineItems,
        PersistenceStep.Outbox,
        PersistenceStep.Commit
    ];

    public static int RunAll()
    {
        var failures = new List<string>();

        Run("public API remains unchanged", PublicApiRemainsUnchanged, failures);
        Run("successful write commits all records and closes the session", SuccessfulWrite, failures);

        foreach (var step in FaultableSteps)
        {
            var capturedStep = step;
            Run($"fault at {capturedStep} rolls back and closes the session",
                () => FaultRollsBackAndCleansUp(capturedStep), failures);
        }

        if (failures.Count == 0)
        {
            Console.WriteLine($"PASS: {FaultableSteps.Length + 2} protected tests");
            return 0;
        }

        foreach (var failure in failures)
        {
            Console.Error.WriteLine(failure);
        }

        Console.Error.WriteLine($"FAIL: {failures.Count} protected test(s)");
        return 1;
    }

    private static void PublicApiRemainsUnchanged()
    {
        var writerType = typeof(InvoiceWriter);
        Check.True(writerType.IsPublic && writerType.IsSealed,
            "InvoiceWriter must remain a public sealed class");

        var constructors = writerType.GetConstructors(BindingFlags.Public | BindingFlags.Instance);
        Check.Equal(1, constructors.Length, "InvoiceWriter should expose exactly one public constructor");
        Check.SequenceEqual([typeof(IInvoiceSessionFactory)],
            constructors[0].GetParameters().Select(parameter => parameter.ParameterType).ToArray(),
            "the InvoiceWriter constructor signature must not change");

        var methods = writerType
            .GetMethods(BindingFlags.Public | BindingFlags.Instance | BindingFlags.Static |
                        BindingFlags.DeclaredOnly)
            .Where(method => !method.IsSpecialName)
            .ToArray();
        Check.Equal(1, methods.Length, "InvoiceWriter should expose only Persist as a public method");
        Check.Equal("Persist", methods[0].Name, "the public writer method must remain Persist");
        Check.Equal(typeof(void), methods[0].ReturnType, "Persist must continue to return void");
        Check.SequenceEqual([typeof(Invoice)],
            methods[0].GetParameters().Select(parameter => parameter.ParameterType).ToArray(),
            "the Persist signature must not change");
    }

    private static void SuccessfulWrite()
    {
        var store = new FaultInjectingStore();
        var invoice = NewInvoice();

        new InvoiceWriter(store).Persist(invoice);

        Check.Equal(new DurableSnapshot(
            [invoice.Id],
            [(invoice.Id, "keyboard"), (invoice.Id, "cable")],
            [invoice.Id]), store.Snapshot, "the complete invoice should be durable");
        Check.Equal(1, store.CommitCount, "the successful transaction should commit once");
        Check.Equal(0, store.RollbackCount, "the successful transaction should not roll back");
        AssertCleanedUp(store);
    }

    private static void FaultRollsBackAndCleansUp(PersistenceStep step)
    {
        var store = new FaultInjectingStore(step);
        store.Seed("existing-invoice", "existing-item");
        var before = store.Snapshot;

        var thrown = Check.Throws<InjectedPersistenceException>(
            () => new InvoiceWriter(store).Persist(NewInvoice()),
            $"the {step} fault should reach the caller");

        Check.Same(store.InjectedFailure, thrown,
            "the original injected failure instance should reach the caller");
        Check.Equal(step, thrown.Step, "the original injected failure should be preserved");
        Check.Equal(before, store.Snapshot, "a failed transaction must not change durable state");
        Check.Equal(0, store.CommitCount, "a failed transaction must not be committed");
        Check.Equal(1, store.RollbackCount, "a failed transaction should be rolled back once");
        AssertCleanedUp(store);
    }

    private static void AssertCleanedUp(FaultInjectingStore store)
    {
        Check.Equal(0, store.ActiveSessionCount, "no persistence session should remain active");
        Check.Equal(1, store.DisposedSessionCount, "the opened session should be disposed exactly once");
    }

    private static Invoice NewInvoice() => new("invoice-42", 129.50m, ["keyboard", "cable"]);

    private static void Run(string name, Action test, ICollection<string> failures)
    {
        try
        {
            test();
        }
        catch (Exception error)
        {
            failures.Add($"{name}: {error.Message}");
        }
    }
}

internal enum PersistenceStep
{
    Invoice,
    LineItems,
    Outbox,
    Commit
}

internal sealed class InjectedPersistenceException(PersistenceStep step)
    : Exception($"Injected failure at {step}.")
{
    public PersistenceStep Step { get; } = step;
}

internal sealed record DurableSnapshot(
    IReadOnlyList<string> InvoiceIds,
    IReadOnlyList<(string InvoiceId, string Item)> LineItems,
    IReadOnlyList<string> OutboxInvoiceIds)
{
    public bool Equals(DurableSnapshot? other) =>
        other is not null &&
        InvoiceIds.SequenceEqual(other.InvoiceIds) &&
        LineItems.SequenceEqual(other.LineItems) &&
        OutboxInvoiceIds.SequenceEqual(other.OutboxInvoiceIds);

    public override int GetHashCode() => HashCode.Combine(
        InvoiceIds.Count,
        LineItems.Count,
        OutboxInvoiceIds.Count);
}

internal sealed class FaultInjectingStore(PersistenceStep? failOnceAt = null) : IInvoiceSessionFactory
{
    private readonly List<string> invoiceIds = [];
    private readonly List<(string InvoiceId, string Item)> lineItems = [];
    private readonly List<string> outboxInvoiceIds = [];
    private bool faultPending = failOnceAt is not null;

    public int ActiveSessionCount { get; private set; }

    public int DisposedSessionCount { get; private set; }

    public int CommitCount { get; private set; }

    public int RollbackCount { get; private set; }

    public InjectedPersistenceException? InjectedFailure { get; private set; }

    public DurableSnapshot Snapshot => new(
        invoiceIds.ToArray(),
        lineItems.ToArray(),
        outboxInvoiceIds.ToArray());

    public void Seed(string invoiceId, string item)
    {
        invoiceIds.Add(invoiceId);
        lineItems.Add((invoiceId, item));
        outboxInvoiceIds.Add(invoiceId);
    }

    public IInvoiceSession OpenSession()
    {
        ActiveSessionCount++;
        return new Session(this);
    }

    private void ThrowIfFaulted(PersistenceStep step)
    {
        if (faultPending && failOnceAt == step)
        {
            faultPending = false;
            var failure = new InjectedPersistenceException(step);
            InjectedFailure = failure;
            throw failure;
        }
    }

    private sealed class Session(FaultInjectingStore owner) : IInvoiceSession
    {
        private readonly List<string> pendingInvoiceIds = [];
        private readonly List<(string InvoiceId, string Item)> pendingLineItems = [];
        private readonly List<string> pendingOutboxInvoiceIds = [];
        private bool completed;
        private bool disposed;

        public void SaveInvoice(Invoice invoice)
        {
            EnsureActive();
            owner.ThrowIfFaulted(PersistenceStep.Invoice);
            pendingInvoiceIds.Add(invoice.Id);
        }

        public void SaveLineItems(string invoiceId, IReadOnlyList<string> items)
        {
            EnsureActive();
            owner.ThrowIfFaulted(PersistenceStep.LineItems);
            pendingLineItems.AddRange(items.Select(item => (invoiceId, item)));
        }

        public void SaveOutboxEvent(string invoiceId)
        {
            EnsureActive();
            owner.ThrowIfFaulted(PersistenceStep.Outbox);
            pendingOutboxInvoiceIds.Add(invoiceId);
        }

        public void Commit()
        {
            EnsureActive();
            owner.ThrowIfFaulted(PersistenceStep.Commit);
            owner.invoiceIds.AddRange(pendingInvoiceIds);
            owner.lineItems.AddRange(pendingLineItems);
            owner.outboxInvoiceIds.AddRange(pendingOutboxInvoiceIds);
            owner.CommitCount++;
            completed = true;
        }

        public void Rollback()
        {
            EnsureActive();
            pendingInvoiceIds.Clear();
            pendingLineItems.Clear();
            pendingOutboxInvoiceIds.Clear();
            owner.RollbackCount++;
            completed = true;
        }

        public void Dispose()
        {
            if (disposed)
            {
                return;
            }

            disposed = true;
            owner.ActiveSessionCount--;
            owner.DisposedSessionCount++;
        }

        private void EnsureActive()
        {
            if (completed || disposed)
            {
                throw new InvalidOperationException("The persistence session is no longer active.");
            }
        }
    }
}

internal static class Check
{
    public static void True(bool condition, string message)
    {
        if (!condition)
        {
            throw new InvalidOperationException(message);
        }
    }

    public static void Equal<T>(T expected, T actual, string message)
    {
        if (!EqualityComparer<T>.Default.Equals(expected, actual))
        {
            throw new InvalidOperationException($"{message}. Expected: {expected}; actual: {actual}.");
        }
    }

    public static void SequenceEqual<T>(
        IEnumerable<T> expected,
        IEnumerable<T> actual,
        string message)
    {
        if (!expected.SequenceEqual(actual))
        {
            throw new InvalidOperationException($"{message}.");
        }
    }

    public static void Same(object? expected, object? actual, string message)
    {
        if (!ReferenceEquals(expected, actual))
        {
            throw new InvalidOperationException($"{message}.");
        }
    }

    public static TException Throws<TException>(Action action, string message)
        where TException : Exception
    {
        try
        {
            action();
        }
        catch (TException error)
        {
            return error;
        }
        catch (Exception error)
        {
            throw new InvalidOperationException(
                $"{message}. Expected {typeof(TException).Name}, got {error.GetType().Name}.");
        }

        throw new InvalidOperationException($"{message}. No exception was thrown.");
    }
}
