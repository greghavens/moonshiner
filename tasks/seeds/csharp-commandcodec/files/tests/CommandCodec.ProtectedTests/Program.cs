using CommandCodec;

var tests = new (string Name, Func<Task> Run)[]
{
    ("registered discriminator and decoder control decoded command", RegisteredDiscriminatorControlsType),
    ("legacy versions use their own validators", LegacyVersionsUseOwnValidators),
    ("dead letters retain envelope and registry detail", DeadLettersRetainDetail),
    ("cancellation retains queued command", CancellationRetainsCommand),
    ("replay after registration upgrade is idempotent", ReplayAfterUpgradeIsIdempotent)
};

var failed = 0;
foreach (var test in tests)
{
    try
    {
        await test.Run();
        Console.WriteLine($"PASS {test.Name}");
    }
    catch (Exception exception)
    {
        failed++;
        Console.Error.WriteLine($"FAIL {test.Name}");
        Console.Error.WriteLine(exception);
    }
}

Console.WriteLine($"{tests.Length - failed}/{tests.Length} protected tests passed");
return failed == 0 ? 0 : 1;

static async Task RegisteredDiscriminatorControlsType()
{
    var registry = new CommandTypeRegistry();
    var decoderCalls = 0;
    registry.Register<ReserveStock>(
        "inventory.reserve",
        1,
        decoder: element =>
        {
            decoderCalls++;
            return new ReserveStock(
                $"decoded:{element.GetProperty("sku").GetString()}",
                element.GetProperty("quantity").GetInt32() + 1);
        });
    var fixture = Fixture.Create(registry);
    fixture.Queue.Enqueue(new CommandEnvelope(
        "m-100",
        "inventory.reserve",
        1,
        "{\"sku\":\"ABC-1\",\"quantity\":2}"));

    var result = await fixture.Processor.ProcessNextAsync();

    Equal(ProcessingStatus.Processed, result.Status);
    var command = Single(fixture.Executor.Commands);
    var reserve = IsType<ReserveStock>(command);
    Equal("decoded:ABC-1", reserve.Sku);
    Equal(3, reserve.Quantity);
    Equal(1, decoderCalls);
}

static async Task LegacyVersionsUseOwnValidators()
{
    var registry = new CommandTypeRegistry();
    var v1Validations = 0;
    var v2Validations = 0;
    registry.Register<SubmitOrderV1>(
        "orders.submit",
        1,
        validator: (command, _) =>
        {
            v1Validations++;
            if (string.IsNullOrWhiteSpace(command.OrderNumber))
            {
                throw new InvalidOperationException("orderNumber is required");
            }

            return ValueTask.CompletedTask;
        });
    registry.Register<SubmitOrderV2>(
        "orders.submit",
        2,
        validator: (command, _) =>
        {
            v2Validations++;
            if (command.AmountInCents <= 0)
            {
                throw new InvalidOperationException("amountInCents must be positive");
            }

            return ValueTask.CompletedTask;
        });

    var fixture = Fixture.Create(registry);
    fixture.Queue.Enqueue(new CommandEnvelope(
        "old-1",
        "orders.submit",
        1,
        "{\"orderNumber\":\"LEGACY-9\"}"));
    fixture.Queue.Enqueue(new CommandEnvelope(
        "new-1",
        "orders.submit",
        2,
        "{\"orderNumber\":\"NEW-9\",\"amountInCents\":0}"));

    var legacy = await fixture.Processor.ProcessNextAsync();
    var current = await fixture.Processor.ProcessNextAsync();

    Equal(ProcessingStatus.Processed, legacy.Status);
    Equal(ProcessingStatus.DeadLettered, current.Status);
    Equal(DeadLetterReason.ValidationFailed, current.DeadLetter?.Reason);
    Contains("amountInCents must be positive", current.DeadLetter?.Detail);
    Equal(1, v1Validations);
    Equal(1, v2Validations);
    Equal(1, fixture.Executor.Commands.Count);
}

static async Task DeadLettersRetainDetail()
{
    var registry = new CommandTypeRegistry();
    var fixture = Fixture.Create(registry);
    var original = new CommandEnvelope(
        "unknown-7",
        "billing.capture",
        4,
        "{\"invoiceId\":\"INV-7\"}");
    fixture.Queue.Enqueue(original);

    var result = await fixture.Processor.ProcessNextAsync();

    Equal(ProcessingStatus.DeadLettered, result.Status);
    Equal(DeadLetterReason.UnknownDiscriminator, result.DeadLetter?.Reason);
    Equal(original, result.DeadLetter?.Envelope);
    Contains("billing.capture", result.DeadLetter?.Detail);
    Equal(original, fixture.DeadLetters.Find("unknown-7")?.Envelope);

    registry.Register<CapturePayment>("billing.capture", 1);
    registry.Register<CapturePayment>("billing.capture", 3);
    var unsupported = new CommandEnvelope(
        "unsupported-4",
        "billing.capture",
        4,
        "{\"invoiceId\":\"INV-8\"}");
    fixture.Queue.Enqueue(unsupported);

    var unsupportedResult = await fixture.Processor.ProcessNextAsync();

    Equal(ProcessingStatus.DeadLettered, unsupportedResult.Status);
    Equal(DeadLetterReason.UnsupportedVersion, unsupportedResult.DeadLetter?.Reason);
    Equal(unsupported, unsupportedResult.DeadLetter?.Envelope);
    Contains("billing.capture", unsupportedResult.DeadLetter?.Detail);
    Contains("version 4", unsupportedResult.DeadLetter?.Detail);
    Contains("1, 3", unsupportedResult.DeadLetter?.Detail);
    Equal(unsupported, fixture.DeadLetters.Find("unsupported-4")?.Envelope);

    registry.Register<CapturePayment>("billing.capture", 4);
    var malformed = new CommandEnvelope(
        "malformed-4",
        "billing.capture",
        4,
        "{\"invoiceId\":");
    fixture.Queue.Enqueue(malformed);

    var malformedResult = await fixture.Processor.ProcessNextAsync();

    Equal(ProcessingStatus.DeadLettered, malformedResult.Status);
    Equal(DeadLetterReason.MalformedPayload, malformedResult.DeadLetter?.Reason);
    Equal(malformed, malformedResult.DeadLetter?.Envelope);
    Contains(
        "Malformed payload for discriminator 'billing.capture' version 4:",
        malformedResult.DeadLetter?.Detail);
    Equal(malformed, fixture.DeadLetters.Find("malformed-4")?.Envelope);
}

static async Task CancellationRetainsCommand()
{
    using var cancellation = new CancellationTokenSource();
    var registry = new CommandTypeRegistry();
    registry.Register<ReserveStock>(
        "inventory.reserve",
        1,
        validator: (_, token) =>
        {
            cancellation.Cancel();
            token.ThrowIfCancellationRequested();
            return ValueTask.CompletedTask;
        });
    var fixture = Fixture.Create(registry);
    fixture.Queue.Enqueue(new CommandEnvelope(
        "cancel-1",
        "inventory.reserve",
        1,
        "{\"sku\":\"WAIT\",\"quantity\":1}"));

    await ThrowsAsync<OperationCanceledException>(
        async () => await fixture.Processor.ProcessNextAsync(cancellation.Token));

    Equal(1, fixture.Queue.Count);
    Equal(0, fixture.DeadLetters.Count);
    Equal(0, fixture.Executor.Commands.Count);
    False(fixture.Idempotency.IsCompleted("cancel-1"));
}

static async Task ReplayAfterUpgradeIsIdempotent()
{
    var registry = new CommandTypeRegistry();
    var fixture = Fixture.Create(registry);
    var envelope = new CommandEnvelope(
        "upgrade-1",
        "shipping.dispatch",
        1,
        "{\"shipmentId\":\"S-44\"}");
    fixture.Queue.Enqueue(envelope);

    var beforeUpgrade = await fixture.Processor.ProcessNextAsync();
    Equal(ProcessingStatus.DeadLettered, beforeUpgrade.Status);

    registry.Register<DispatchShipment>("shipping.dispatch", 1);
    True(fixture.Processor.ReplayDeadLetter("upgrade-1"));
    False(fixture.Processor.ReplayDeadLetter("upgrade-1"));

    var replayed = await fixture.Processor.ProcessNextAsync();
    Equal(ProcessingStatus.Processed, replayed.Status);
    Equal(1, fixture.Executor.Commands.Count);
    True(fixture.Idempotency.IsCompleted("upgrade-1"));

    fixture.Queue.Enqueue(envelope);
    var duplicate = await fixture.Processor.ProcessNextAsync();
    Equal(ProcessingStatus.AlreadyProcessed, duplicate.Status);
    Equal(1, fixture.Executor.Commands.Count);
}

static T Single<T>(IReadOnlyList<T> values)
{
    Equal(1, values.Count);
    return values[0];
}

static T IsType<T>(object value)
{
    if (value is T typed)
    {
        return typed;
    }

    throw new InvalidOperationException($"Expected {typeof(T).Name}, got {value.GetType().Name}.");
}

static void Equal<T>(T expected, T actual)
{
    if (!EqualityComparer<T>.Default.Equals(expected, actual))
    {
        throw new InvalidOperationException($"Expected <{expected}>, got <{actual}>.");
    }
}

static void Contains(string expected, string? actual)
{
    if (actual is null || !actual.Contains(expected, StringComparison.Ordinal))
    {
        throw new InvalidOperationException($"Expected <{actual}> to contain <{expected}>.");
    }
}

static void True(bool value)
{
    if (!value)
    {
        throw new InvalidOperationException("Expected true, got false.");
    }
}

static void False(bool value)
{
    if (value)
    {
        throw new InvalidOperationException("Expected false, got true.");
    }
}

static async Task ThrowsAsync<TException>(Func<Task> action)
    where TException : Exception
{
    try
    {
        await action();
    }
    catch (TException)
    {
        return;
    }

    throw new InvalidOperationException($"Expected {typeof(TException).Name}.");
}

internal sealed record ReserveStock(string Sku, int Quantity) : ICommand;

internal sealed record SubmitOrderV1(string OrderNumber) : ICommand;

internal sealed record SubmitOrderV2(string OrderNumber, long AmountInCents) : ICommand;

internal sealed record DispatchShipment(string ShipmentId) : ICommand;

internal sealed record CapturePayment(string InvoiceId) : ICommand;

internal sealed class RecordingExecutor : ICommandExecutor
{
    public List<ICommand> Commands { get; } = [];

    public ValueTask ExecuteAsync(ICommand command, CancellationToken cancellationToken)
    {
        cancellationToken.ThrowIfCancellationRequested();
        Commands.Add(command);
        return ValueTask.CompletedTask;
    }
}

internal sealed record Fixture(
    InMemoryCommandQueue Queue,
    InMemoryDeadLetterStore DeadLetters,
    InMemoryIdempotencyStore Idempotency,
    RecordingExecutor Executor,
    QueuedCommandProcessor Processor)
{
    public static Fixture Create(CommandTypeRegistry registry)
    {
        var queue = new InMemoryCommandQueue();
        var deadLetters = new InMemoryDeadLetterStore();
        var idempotency = new InMemoryIdempotencyStore();
        var executor = new RecordingExecutor();
        var processor = new QueuedCommandProcessor(
            queue,
            deadLetters,
            idempotency,
            new CommandCodec.CommandCodec(registry),
            executor);
        return new Fixture(queue, deadLetters, idempotency, executor, processor);
    }
}
