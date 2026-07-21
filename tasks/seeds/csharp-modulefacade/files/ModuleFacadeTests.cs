using System.Reflection;
using Checkout.Modules.Orders;
using Microsoft.Extensions.DependencyInjection;

internal static class Program
{
    private static readonly (string Name, Func<Task> Run)[] Tests =
    [
        ("module boundary is the narrow facade", ModuleBoundaryIsNarrowFacade),
        ("each operation gets one fresh scope", EachOperationGetsOneFreshScope),
        ("optional discount works when absent or present", OptionalDiscountWorks),
        ("publication follows persistence while scope is alive", PublicationTimingIsCorrect),
        ("persistence failure propagates and disposes the operation scope", PersistenceFailureStillDisposesScope),
        ("failure still disposes the operation scope", FailureStillDisposesScope),
        ("existing input validation is preserved", ExistingInputValidationIsPreserved),
        ("pre-registered facade substitutes the integration boundary", FacadeCanBeSubstituted)
    ];

    public static async Task<int> Main()
    {
        var failures = 0;

        foreach (var (name, run) in Tests)
        {
            try
            {
                await run().ConfigureAwait(false);
                Console.WriteLine($"PASS {name}");
            }
            catch (Exception exception)
            {
                failures++;
                Console.WriteLine($"FAIL {name}");
                Console.WriteLine($"  {exception.GetType().Name}: {exception.Message}");
            }
        }

        Console.WriteLine($"{Tests.Length - failures}/{Tests.Length} acceptance tests passed");
        return failures == 0 ? 0 : 1;
    }

    private static Task ModuleBoundaryIsNarrowFacade()
    {
        var constructors = typeof(OrdersModule).GetConstructors(BindingFlags.Instance | BindingFlags.Public);
        Check.Equal(1, constructors.Length, "OrdersModule should have one public constructor");

        var parameters = constructors[0].GetParameters();
        Check.Equal(1, parameters.Length, "OrdersModule constructor should have one dependency");
        Check.Equal(typeof(IOrdersModuleFacade), parameters[0].ParameterType,
            "OrdersModule must depend on IOrdersModuleFacade");

        var capabilityMethods = typeof(IOrdersModuleFacade).GetMethods();
        Check.Equal(1, capabilityMethods.Length, "module facade must remain a single narrow capability");
        Check.Equal(nameof(IOrdersModuleFacade.PlaceAsync), capabilityMethods[0].Name,
            "module facade exposed an unrelated capability");
        Check.Equal(0, typeof(IOrdersModuleFacade).GetProperties().Length,
            "module facade must not expose a service container property");

        var leaksProvider = typeof(OrdersModule)
            .GetFields(BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic)
            .Any(field => typeof(IServiceProvider).IsAssignableFrom(field.FieldType));
        Check.False(leaksProvider, "OrdersModule must not retain an IServiceProvider");
        return Task.CompletedTask;
    }

    private static async Task EachOperationGetsOneFreshScope()
    {
        var trace = new Trace();
        await using var provider = BuildProvider(trace);
        var module = provider.GetRequiredService<OrdersModule>();

        var first = await module.PlaceAsync(new PlaceOrder("tea", 12m)).ConfigureAwait(false);
        var second = await module.PlaceAsync(new PlaceOrder("cake", 8m)).ConfigureAwait(false);

        Check.NotEqual(first.ScopeId, second.ScopeId, "consecutive operations reused a DI scope");
        Check.SequenceEqual(
            [
                $"save:{first.ScopeId}", $"publish:{first.ScopeId}", $"dispose:{first.ScopeId}",
                $"save:{second.ScopeId}", $"publish:{second.ScopeId}", $"dispose:{second.ScopeId}"
            ],
            trace.Operations,
            "required services were not scoped as one unit per operation");
    }

    private static async Task OptionalDiscountWorks()
    {
        var withoutTrace = new Trace();
        await using (var provider = BuildProvider(withoutTrace))
        {
            var receipt = await provider.GetRequiredService<OrdersModule>()
                .PlaceAsync(new PlaceOrder("beans", 20m))
                .ConfigureAwait(false);
            Check.Equal(20m, receipt.Total, "missing optional discount changed the price");
        }

        var withTrace = new Trace();
        await using (var provider = BuildProvider(withTrace, includeDiscount: true))
        {
            var receipt = await provider.GetRequiredService<OrdersModule>()
                .PlaceAsync(new PlaceOrder("beans", 20m))
                .ConfigureAwait(false);
            Check.Equal(15m, receipt.Total, "registered optional discount was not applied");
            Check.SequenceEqual([receipt.ScopeId], withTrace.DiscountScopes,
                "discount was not resolved from the operation scope");
        }
    }

    private static async Task PublicationTimingIsCorrect()
    {
        var trace = new Trace();
        trace.BlockPersistence = true;
        trace.BlockPublication = true;
        await using var provider = BuildProvider(trace);

        var operation = provider.GetRequiredService<OrdersModule>()
            .PlaceAsync(new PlaceOrder("coffee", 9m));

        Check.False(operation.IsCompleted, "operation returned before persistence completed");
        await trace.PersistenceStarted.Task.ConfigureAwait(false);
        Check.False(operation.IsCompleted, "operation returned before persistence completed");
        Check.SequenceEqual(["save:1"], trace.Operations,
            "publication started before persistence completed");

        trace.PersistenceCompletion.SetResult(true);

        Check.False(operation.IsCompleted, "operation returned before publication completed");
        await trace.PublicationStarted.Task.ConfigureAwait(false);
        Check.False(operation.IsCompleted, "operation returned before publication completed");
        Check.SequenceEqual(["save:1", "publish:1"], trace.Operations,
            "publication did not start after persistence completed");

        trace.PublicationCompletion.SetResult(true);
        var receipt = await operation.ConfigureAwait(false);

        Check.SequenceEqual(
            [$"save:{receipt.ScopeId}", $"publish:{receipt.ScopeId}", $"dispose:{receipt.ScopeId}"],
            trace.Operations,
            "publication must happen after save and before scope disposal");
        Check.Equal(1, trace.Published.Count, "exactly one event should be published");
        Check.Equal(receipt.Number, trace.Published[0].Number, "published a different order number");
        Check.Equal(receipt.Total, trace.Published[0].Total, "published a different total");
    }

    private static async Task PersistenceFailureStillDisposesScope()
    {
        var trace = new Trace();
        await using var provider = BuildProvider(trace, failPersistence: true);
        var module = provider.GetRequiredService<OrdersModule>();

        await Check.ThrowsAsync<InvalidOperationException>(
            () => module.PlaceAsync(new PlaceOrder("jam", 7m)),
            "persistence failure should propagate").ConfigureAwait(false);

        Check.SequenceEqual(["save:1", "dispose:1"], trace.Operations,
            "scope was not disposed after persistence failed or publication was attempted");
    }

    private static async Task FailureStillDisposesScope()
    {
        var trace = new Trace();
        await using var provider = BuildProvider(trace, failPublication: true);
        var module = provider.GetRequiredService<OrdersModule>();

        await Check.ThrowsAsync<InvalidOperationException>(
            () => module.PlaceAsync(new PlaceOrder("jam", 7m)),
            "publication failure should propagate").ConfigureAwait(false);

        Check.SequenceEqual(["save:1", "publish:1", "dispose:1"], trace.Operations,
            "scope was not disposed after publication failed");
    }

    private static async Task ExistingInputValidationIsPreserved()
    {
        var substitute = new SubstituteFacade();
        var services = new ServiceCollection();
        services.AddSingleton<IOrdersModuleFacade>(substitute);
        services.AddOrdersModule();

        await using var provider = services.BuildServiceProvider(
            new ServiceProviderOptions { ValidateScopes = true, ValidateOnBuild = true });
        var module = provider.GetRequiredService<OrdersModule>();

        await Check.ThrowsAsync<ArgumentNullException>(
            () => module.PlaceAsync(null!),
            "null command should be rejected").ConfigureAwait(false);
        await Check.ThrowsAsync<ArgumentException>(
            () => module.PlaceAsync(new PlaceOrder(" ", 1m)),
            "blank SKU should be rejected").ConfigureAwait(false);
        await Check.ThrowsAsync<ArgumentOutOfRangeException>(
            () => module.PlaceAsync(new PlaceOrder("tea", -1m)),
            "negative price should be rejected").ConfigureAwait(false);

        Check.Equal(0, substitute.Calls, "invalid commands crossed the module boundary");
    }

    private static async Task FacadeCanBeSubstituted()
    {
        var substitute = new SubstituteFacade();
        var services = new ServiceCollection();
        services.AddSingleton<IOrdersModuleFacade>(substitute);
        services.AddOrdersModule();

        await using var provider = services.BuildServiceProvider(
            new ServiceProviderOptions { ValidateScopes = true, ValidateOnBuild = true });
        var receipt = await provider.GetRequiredService<OrdersModule>()
            .PlaceAsync(new PlaceOrder("test-sku", 4m))
            .ConfigureAwait(false);

        Check.Equal(1, substitute.Calls, "OrdersModule ignored the registered facade substitute");
        Check.Equal(9001, receipt.Number, "substitute result did not cross the module boundary");
    }

    private static ServiceProvider BuildProvider(
        Trace trace,
        bool includeDiscount = false,
        bool failPersistence = false,
        bool failPublication = false)
    {
        var services = new ServiceCollection();
        services.AddOrdersModule();
        services.AddSingleton(trace);
        services.AddScoped(_ => new ScopeMarker(trace));
        services.AddScoped<IOrderNumberSequence, TestSequence>();
        services.AddScoped<IOrderStore>(provider => failPersistence
            ? new ThrowingStore(provider.GetRequiredService<ScopeMarker>(), trace)
            : new TestStore(provider.GetRequiredService<ScopeMarker>(), trace));
        services.AddScoped<IEventPublisher>(provider => failPublication
            ? new ThrowingPublisher(provider.GetRequiredService<ScopeMarker>(), trace)
            : new RecordingPublisher(provider.GetRequiredService<ScopeMarker>(), trace));

        if (includeDiscount)
        {
            services.AddScoped<IOrderDiscount, QuarterOffDiscount>();
        }

        return services.BuildServiceProvider(
            new ServiceProviderOptions { ValidateScopes = true, ValidateOnBuild = true });
    }

    private sealed class Trace
    {
        private int _nextScopeId;
        private int _nextOrderNumber = 100;

        public List<string> Operations { get; } = [];

        public HashSet<int> SavedScopes { get; } = [];

        public HashSet<int> DisposedScopes { get; } = [];

        public List<OrderPlaced> Stored { get; } = [];

        public List<OrderPlaced> Published { get; } = [];

        public List<int> DiscountScopes { get; } = [];

        public bool BlockPersistence { get; set; }

        public bool BlockPublication { get; set; }

        public TaskCompletionSource<bool> PersistenceStarted { get; } =
            new(TaskCreationOptions.RunContinuationsAsynchronously);

        public TaskCompletionSource<bool> PersistenceCompletion { get; } =
            new(TaskCreationOptions.RunContinuationsAsynchronously);

        public TaskCompletionSource<bool> PublicationStarted { get; } =
            new(TaskCreationOptions.RunContinuationsAsynchronously);

        public TaskCompletionSource<bool> PublicationCompletion { get; } =
            new(TaskCreationOptions.RunContinuationsAsynchronously);

        public int AllocateScopeId() => ++_nextScopeId;

        public int AllocateOrderNumber() => ++_nextOrderNumber;
    }

    private sealed class ScopeMarker(Trace trace) : IAsyncDisposable
    {
        public int Id { get; } = trace.AllocateScopeId();

        public ValueTask DisposeAsync()
        {
            trace.DisposedScopes.Add(Id);
            trace.Operations.Add($"dispose:{Id}");
            return ValueTask.CompletedTask;
        }
    }

    private sealed class TestSequence(ScopeMarker marker, Trace trace) : IOrderNumberSequence
    {
        public int ScopeId => marker.Id;

        public int Next() => trace.AllocateOrderNumber();
    }

    private sealed class TestStore(ScopeMarker marker, Trace trace) : IOrderStore
    {
        public int ScopeId => marker.Id;

        public Task SaveAsync(OrderPlaced order, CancellationToken cancellationToken = default)
        {
            cancellationToken.ThrowIfCancellationRequested();
            Check.Equal(ScopeId, order.ScopeId, "sequence and store came from different scopes");
            trace.SavedScopes.Add(ScopeId);
            trace.Stored.Add(order);
            trace.Operations.Add($"save:{ScopeId}");
            trace.PersistenceStarted.TrySetResult(true);
            return trace.BlockPersistence ? trace.PersistenceCompletion.Task : Task.CompletedTask;
        }
    }

    private sealed class ThrowingStore(ScopeMarker marker, Trace trace) : IOrderStore
    {
        public int ScopeId => marker.Id;

        public Task SaveAsync(OrderPlaced order, CancellationToken cancellationToken = default)
        {
            cancellationToken.ThrowIfCancellationRequested();
            Check.Equal(ScopeId, order.ScopeId, "sequence and store came from different scopes");
            trace.Operations.Add($"save:{ScopeId}");
            return Task.FromException(new InvalidOperationException("order store unavailable"));
        }
    }

    private sealed class QuarterOffDiscount(ScopeMarker marker, Trace trace) : IOrderDiscount
    {
        public decimal Apply(string sku, decimal unitPrice)
        {
            trace.DiscountScopes.Add(marker.Id);
            return unitPrice * 0.75m;
        }
    }

    private sealed class RecordingPublisher(ScopeMarker marker, Trace trace) : IEventPublisher
    {
        public Task PublishAsync<TEvent>(TEvent @event, CancellationToken cancellationToken = default)
        {
            cancellationToken.ThrowIfCancellationRequested();
            var placed = Check.IsType<OrderPlaced>(@event, "unexpected event type");
            Check.Equal(marker.Id, placed.ScopeId, "publisher came from a different scope");
            Check.True(trace.SavedScopes.Contains(placed.ScopeId), "event published before persistence completed");
            Check.True(ReferenceEquals(trace.Stored[^1], placed), "publisher received a different event instance");
            Check.False(trace.DisposedScopes.Contains(placed.ScopeId), "event published after scope disposal");
            trace.Published.Add(placed);
            trace.Operations.Add($"publish:{placed.ScopeId}");
            trace.PublicationStarted.TrySetResult(true);
            return trace.BlockPublication ? trace.PublicationCompletion.Task : Task.CompletedTask;
        }
    }

    private sealed class ThrowingPublisher(ScopeMarker marker, Trace trace) : IEventPublisher
    {
        public Task PublishAsync<TEvent>(TEvent @event, CancellationToken cancellationToken = default)
        {
            cancellationToken.ThrowIfCancellationRequested();
            var placed = Check.IsType<OrderPlaced>(@event, "unexpected event type");
            Check.Equal(marker.Id, placed.ScopeId, "publisher came from a different scope");
            Check.True(trace.SavedScopes.Contains(placed.ScopeId), "failure happened before persistence");
            Check.True(ReferenceEquals(trace.Stored[^1], placed), "publisher received a different event instance");
            Check.False(trace.DisposedScopes.Contains(placed.ScopeId), "scope disposed before publication attempt");
            trace.Operations.Add($"publish:{placed.ScopeId}");
            return Task.FromException(new InvalidOperationException("event bus unavailable"));
        }
    }

    private sealed class SubstituteFacade : IOrdersModuleFacade
    {
        public int Calls { get; private set; }

        public Task<OrderReceipt> PlaceAsync(
            PlaceOrder command,
            CancellationToken cancellationToken = default)
        {
            Calls++;
            return Task.FromResult(new OrderReceipt(9001, command.UnitPrice, 77));
        }
    }
}

internal static class Check
{
    public static void True(bool condition, string message)
    {
        if (!condition)
        {
            throw new AcceptanceException(message);
        }
    }

    public static void False(bool condition, string message) => True(!condition, message);

    public static void Equal<T>(T expected, T actual, string message)
        where T : notnull
    {
        if (!EqualityComparer<T>.Default.Equals(expected, actual))
        {
            throw new AcceptanceException($"{message}; expected <{expected}>, actual <{actual}>");
        }
    }

    public static void NotEqual<T>(T unexpected, T actual, string message)
        where T : notnull
    {
        if (EqualityComparer<T>.Default.Equals(unexpected, actual))
        {
            throw new AcceptanceException($"{message}; both were <{actual}>");
        }
    }

    public static void SequenceEqual<T>(IEnumerable<T> expected, IEnumerable<T> actual, string message)
    {
        if (!expected.SequenceEqual(actual))
        {
            throw new AcceptanceException(
                $"{message}; expected [{string.Join(", ", expected)}], actual [{string.Join(", ", actual)}]");
        }
    }

    public static TExpected IsType<TExpected>(object? value, string message)
    {
        if (value is not TExpected expected)
        {
            throw new AcceptanceException($"{message}; actual type was {value?.GetType().Name ?? "null"}");
        }

        return expected;
    }

    public static async Task ThrowsAsync<TException>(Func<Task> action, string message)
        where TException : Exception
    {
        try
        {
            await action().ConfigureAwait(false);
        }
        catch (TException)
        {
            return;
        }

        throw new AcceptanceException($"{message}; expected {typeof(TException).Name}");
    }
}

internal sealed class AcceptanceException(string message) : Exception(message);
