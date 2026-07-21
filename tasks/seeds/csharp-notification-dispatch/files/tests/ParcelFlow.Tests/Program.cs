using System.Reflection;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.DependencyInjection.Extensions;
using ParcelFlow;

var tests = new (string Name, Func<Task> Body)[]
{
    ("existing dependency-injection registrations are preserved", ExistingRegistrationsArePreserved),
    ("notifications run only after commit", NotificationsRunOnlyAfterCommit),
    ("handlers run sequentially in registration order", HandlersRunInRegistrationOrder),
    ("transient failures retry only the current handler", TransientFailuresRetryCurrentHandler),
    ("the retry budget remains three attempts", RetryBudgetRemainsThreeAttempts),
    ("terminal handler failures leave the release committed", TerminalFailuresLeaveReleaseCommitted),
    ("notification orchestration is internal and centralized", NotificationOrchestrationIsCentralized),
    ("the dispatcher resolves generic handlers from the current scope", DispatcherUsesCurrentScope)
};

var failures = new List<string>();
foreach (var (name, body) in tests)
{
    try
    {
        await body();
        Console.WriteLine($"PASS {name}");
    }
    catch (Exception exception)
    {
        failures.Add(name);
        Console.Error.WriteLine(
            $"FAIL {name}: {exception.GetType().Name}: {exception.Message}");
    }
}

if (failures.Count != 0)
{
    Console.Error.WriteLine(
        $"{failures.Count} test(s) failed: {string.Join(", ", failures)}");
    return 1;
}

Console.WriteLine($"All {tests.Length} tests passed.");
return 0;

static Task ExistingRegistrationsArePreserved()
{
    var services = new ServiceCollection();
    services.AddParcelFlow();

    var handlerDescriptors = services
        .Where(descriptor =>
            descriptor.ServiceType == typeof(INotificationHandler<ShipmentReleased>))
        .ToArray();

    Equal(2, handlerDescriptors.Length, "the built-in handler registration count");
    SequenceEqual(
        new Type[] { typeof(ReleaseAuditHandler), typeof(CustomerUpdateHandler) },
        handlerDescriptors.Select(descriptor => descriptor.ImplementationType!),
        "handler implementation order");
    True(
        handlerDescriptors.All(descriptor =>
            descriptor.Lifetime == ServiceLifetime.Scoped),
        "both existing handlers must remain scoped");

    var retryPolicyType = typeof(IShipmentReleaseService).Assembly.GetType(
        "ParcelFlow.NotificationRetryPolicy")
        ?? throw new TestFailureException("the existing retry policy type is missing");
    var retryDescriptor = SingleDescriptor(services, retryPolicyType);
    Equal(ServiceLifetime.Singleton, retryDescriptor.Lifetime, "retry policy lifetime");
    Equal(retryPolicyType, retryDescriptor.ImplementationType, "retry policy implementation");

    var releaseDescriptor = SingleDescriptor(services, typeof(IShipmentReleaseService));
    Equal(ServiceLifetime.Scoped, releaseDescriptor.Lifetime, "release service lifetime");
    Equal(
        "ParcelFlow.ShipmentReleaseService",
        releaseDescriptor.ImplementationType?.FullName,
        "release service implementation");
    SequenceEqual(
        new[]
        {
            typeof(INotificationHandler<ShipmentReleased>),
            typeof(INotificationHandler<ShipmentReleased>),
            retryPolicyType,
            typeof(IShipmentReleaseService)
        },
        services
            .Where(descriptor =>
                descriptor.ServiceType == typeof(INotificationHandler<ShipmentReleased>) ||
                descriptor.ServiceType == retryPolicyType ||
                descriptor.ServiceType == typeof(IShipmentReleaseService))
            .Select(descriptor => descriptor.ServiceType),
        "existing registration order");

    return Task.CompletedTask;
}

static async Task NotificationsRunOnlyAfterCommit()
{
    var trace = new List<string>();
    var transaction = new RecordingTransaction(trace);
    var repository = new RecordingRepository(transaction, trace);
    var handler = new ScriptedHandler("audit", transaction, trace);

    using var provider = BuildProvider(transaction, repository, handler);
    using var scope = provider.CreateScope();
    await scope.ServiceProvider
        .GetRequiredService<IShipmentReleaseService>()
        .ReleaseAsync("shipment-101");

    SequenceEqual(
        new[]
        {
            "transaction:begin",
            "repository:release:shipment-101",
            "transaction:commit",
            "handler:audit:attempt:1:transaction:closed"
        },
        trace,
        "post-commit trace");
    SequenceEqual(new[] { "shipment-101" }, repository.Released, "committed releases");
}

static async Task HandlersRunInRegistrationOrder()
{
    var trace = new List<string>();
    var transaction = new RecordingTransaction(trace);
    var repository = new RecordingRepository(transaction, trace);
    var first = new ScriptedHandler("inventory", transaction, trace);
    var second = new ScriptedHandler("audit", transaction, trace);
    var third = new ScriptedHandler("customer", transaction, trace);

    using var provider = BuildProvider(transaction, repository, first, second, third);
    using var scope = provider.CreateScope();
    await scope.ServiceProvider
        .GetRequiredService<IShipmentReleaseService>()
        .ReleaseAsync("shipment-202");

    SequenceEqual(
        new[]
        {
            "transaction:begin",
            "repository:release:shipment-202",
            "transaction:commit",
            "handler:inventory:attempt:1:transaction:closed",
            "handler:audit:attempt:1:transaction:closed",
            "handler:customer:attempt:1:transaction:closed"
        },
        trace,
        "sequential handler trace");
}

static async Task TransientFailuresRetryCurrentHandler()
{
    var trace = new List<string>();
    var transaction = new RecordingTransaction(trace);
    var repository = new RecordingRepository(transaction, trace);
    var first = new ScriptedHandler(
        "inventory",
        transaction,
        trace,
        transientFailures: 2);
    var flaky = new ScriptedHandler(
        "audit",
        transaction,
        trace,
        transientFailures: 2);
    var last = new ScriptedHandler("customer", transaction, trace);

    using var provider = BuildProvider(transaction, repository, first, flaky, last);
    using var scope = provider.CreateScope();
    await scope.ServiceProvider
        .GetRequiredService<IShipmentReleaseService>()
        .ReleaseAsync("shipment-303");

    SequenceEqual(
        new[]
        {
            "transaction:begin",
            "repository:release:shipment-303",
            "transaction:commit",
            "handler:inventory:attempt:1:transaction:closed",
            "handler:inventory:attempt:2:transaction:closed",
            "handler:inventory:attempt:3:transaction:closed",
            "handler:audit:attempt:1:transaction:closed",
            "handler:audit:attempt:2:transaction:closed",
            "handler:audit:attempt:3:transaction:closed",
            "handler:customer:attempt:1:transaction:closed"
        },
        trace,
        "retry trace");
    Equal(1, transaction.ExecutionCount, "the transaction must not be retried");
    Equal(1, repository.Released.Count, "the release must be committed once");
}

static async Task RetryBudgetRemainsThreeAttempts()
{
    var trace = new List<string>();
    var transaction = new RecordingTransaction(trace);
    var repository = new RecordingRepository(transaction, trace);
    var exhausted = new ScriptedHandler(
        "webhook",
        transaction,
        trace,
        transientFailures: 3);

    using var provider = BuildProvider(transaction, repository, exhausted);
    using var scope = provider.CreateScope();
    await ThrowsAsync<TransientNotificationException>(
        () => scope.ServiceProvider
            .GetRequiredService<IShipmentReleaseService>()
            .ReleaseAsync("shipment-404"),
        "an exhausted transient failure must surface");

    Equal(3, exhausted.Attempts, "transient delivery attempt count");
    Equal(1, transaction.ExecutionCount, "retry exhaustion must not replay the transaction");
    SequenceEqual(new[] { "shipment-404" }, repository.Released, "committed releases");
}

static async Task TerminalFailuresLeaveReleaseCommitted()
{
    var trace = new List<string>();
    var transaction = new RecordingTransaction(trace);
    var repository = new RecordingRepository(transaction, trace);
    var terminal = new ScriptedHandler(
        "audit",
        transaction,
        trace,
        terminalFailure: true);
    var downstream = new ScriptedHandler("customer", transaction, trace);

    using var provider = BuildProvider(transaction, repository, terminal, downstream);
    using var scope = provider.CreateScope();
    await ThrowsAsync<InvalidOperationException>(
        () => scope.ServiceProvider
            .GetRequiredService<IShipmentReleaseService>()
            .ReleaseAsync("shipment-505"),
        "a terminal handler failure must surface");

    Equal(1, terminal.Attempts, "terminal failures must not be retried");
    Equal(0, downstream.Attempts, "later handlers must not overtake a failure");
    SequenceEqual(new[] { "shipment-505" }, repository.Released, "committed releases");
    False(transaction.IsActive, "the transaction must already be closed");
}

static Task NotificationOrchestrationIsCentralized()
{
    var dispatcherContract = GetDispatcherContract();

    True(
        dispatcherContract.IsInterface ||
            (dispatcherContract.IsClass && dispatcherContract.IsAbstract),
        "the dispatcher contract must be an interface or abstract class");
    False(dispatcherContract.IsPublic, "the dispatcher contract must stay internal");
    SingleDispatcherMethod(dispatcherContract);

    var services = new ServiceCollection();
    var trace = new List<string>();
    var transaction = new RecordingTransaction(trace);
    var repository = new RecordingRepository(transaction, trace);
    services.AddSingleton<IShipmentTransaction>(transaction);
    services.AddSingleton<IShipmentRepository>(repository);
    services.AddParcelFlow();
    var dispatcherDescriptor = SingleDescriptor(services, dispatcherContract);
    Equal(ServiceLifetime.Scoped, dispatcherDescriptor.Lifetime, "dispatcher lifetime");

    using var provider = services.BuildServiceProvider(new ServiceProviderOptions
    {
        ValidateOnBuild = true,
        ValidateScopes = true
    });
    using var scope = provider.CreateScope();
    var dispatcherImplementation = scope.ServiceProvider
        .GetRequiredService(dispatcherContract)
        .GetType();
    False(dispatcherImplementation.IsPublic, "the dispatcher implementation must stay internal");
    True(
        dispatcherContract.IsAssignableFrom(dispatcherImplementation),
        "the dispatcher implementation must implement its contract");

    return Task.CompletedTask;
}

static async Task DispatcherUsesCurrentScope()
{
    var dispatcherContract = GetDispatcherContract();
    var dispatchMethod = SingleDispatcherMethod(dispatcherContract)
        .MakeGenericMethod(typeof(DispatchProbe));
    var trace = new List<string>();
    var transaction = new RecordingTransaction(trace);
    var repository = new RecordingRepository(transaction, trace);
    var log = new DispatchProbeLog();
    var services = new ServiceCollection();
    services.AddSingleton<IShipmentTransaction>(transaction);
    services.AddSingleton<IShipmentRepository>(repository);
    services.AddSingleton(log);
    services.AddScoped<INotificationHandler<DispatchProbe>, ScopedProbeHandler>();
    services.AddParcelFlow();

    using var provider = services.BuildServiceProvider(new ServiceProviderOptions
    {
        ValidateOnBuild = true,
        ValidateScopes = true
    });

    INotificationHandler<DispatchProbe> firstHandler;
    using (var firstScope = provider.CreateScope())
    {
        firstHandler = firstScope.ServiceProvider
            .GetRequiredService<INotificationHandler<DispatchProbe>>();
        await InvokeDispatchAsync(
            firstScope.ServiceProvider,
            dispatcherContract,
            dispatchMethod,
            new DispatchProbe("first"));
    }

    INotificationHandler<DispatchProbe> secondHandler;
    using (var secondScope = provider.CreateScope())
    {
        secondHandler = secondScope.ServiceProvider
            .GetRequiredService<INotificationHandler<DispatchProbe>>();
        await InvokeDispatchAsync(
            secondScope.ServiceProvider,
            dispatcherContract,
            dispatchMethod,
            new DispatchProbe("second"));
    }

    Equal(2, log.Deliveries.Count, "generic probe delivery count");
    True(
        ReferenceEquals(firstHandler, log.Deliveries[0].Handler),
        "the first dispatch must use the handler from its current scope");
    True(
        ReferenceEquals(secondHandler, log.Deliveries[1].Handler),
        "the second dispatch must use the handler from its current scope");
    False(
        ReferenceEquals(firstHandler, secondHandler),
        "separate scopes must receive separate scoped handlers");
    SequenceEqual(
        new[] { "first", "second" },
        log.Deliveries.Select(delivery => delivery.Notification.Name),
        "generic probe notifications");
}

static Type GetDispatcherContract()
{
    var releaseImplementation = typeof(IShipmentReleaseService).Assembly
        .GetType("ParcelFlow.ShipmentReleaseService")
        ?? throw new TestFailureException("the shipment release implementation is missing");
    var releaseConstructors = releaseImplementation
        .GetConstructors(BindingFlags.Instance | BindingFlags.Public);
    var matchingConstructors = releaseConstructors
        .Where(constructor =>
        {
            var parameterTypes = constructor
                .GetParameters()
                .Select(parameter => parameter.ParameterType)
                .ToArray();
            return parameterTypes.Length == 3 &&
                parameterTypes.Count(type => type == typeof(IShipmentTransaction)) == 1 &&
                parameterTypes.Count(type => type == typeof(IShipmentRepository)) == 1;
        })
        .ToArray();
    Equal(1, matchingConstructors.Length, "dispatcher-based release constructor count");
    var releaseConstructor = matchingConstructors[0];
    var dependencies = releaseConstructor
        .GetParameters()
        .Select(parameter => parameter.ParameterType)
        .ToArray();
    return dependencies.Single(type =>
        type != typeof(IShipmentTransaction) &&
        type != typeof(IShipmentRepository));
}

static MethodInfo SingleDispatcherMethod(Type dispatcherContract)
{
    var methods = dispatcherContract
        .GetMethods(BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic)
        .Where(method =>
        {
            if (!method.IsGenericMethodDefinition ||
                method.GetGenericArguments().Length != 1 ||
                method.ReturnType != typeof(Task) &&
                method.ReturnType != typeof(ValueTask))
            {
                return false;
            }

            var genericNotification = method.GetGenericArguments()[0];
            var parameters = method.GetParameters();
            return parameters.Length == 2 &&
                parameters[0].ParameterType == genericNotification &&
                parameters[1].ParameterType == typeof(CancellationToken);
        })
        .ToArray();
    Equal(1, methods.Length, "generic dispatcher method count");
    return methods[0];
}

static async Task InvokeDispatchAsync(
    IServiceProvider serviceProvider,
    Type dispatcherContract,
    MethodInfo dispatchMethod,
    DispatchProbe notification)
{
    var dispatcher = serviceProvider.GetRequiredService(dispatcherContract);
    var dispatchResult = dispatchMethod.Invoke(
        dispatcher,
        new object[] { notification, CancellationToken.None });
    if (dispatchResult is Task dispatchTask)
    {
        await dispatchTask;
        return;
    }

    if (dispatchResult is ValueTask dispatchValueTask)
    {
        await dispatchValueTask;
        return;
    }

    throw new TestFailureException("the dispatcher method did not return an awaitable result");
}

static ServiceProvider BuildProvider(
    RecordingTransaction transaction,
    RecordingRepository repository,
    params INotificationHandler<ShipmentReleased>[] handlers)
{
    var services = new ServiceCollection();
    services.AddSingleton<IShipmentTransaction>(transaction);
    services.AddSingleton<IShipmentRepository>(repository);
    services.AddParcelFlow();
    services.RemoveAll<INotificationHandler<ShipmentReleased>>();

    foreach (var handler in handlers)
    {
        services.AddSingleton(handler);
    }

    return services.BuildServiceProvider(new ServiceProviderOptions
    {
        ValidateOnBuild = true,
        ValidateScopes = true
    });
}

static ServiceDescriptor SingleDescriptor(
    IEnumerable<ServiceDescriptor> services,
    Type serviceType)
{
    var matches = services
        .Where(descriptor => descriptor.ServiceType == serviceType)
        .ToArray();
    Equal(1, matches.Length, $"registration count for {serviceType.Name}");
    return matches[0];
}

static void Equal<T>(T expected, T actual, string message)
{
    if (!EqualityComparer<T>.Default.Equals(expected, actual))
    {
        throw new TestFailureException(
            $"{message}: expected '{expected}', got '{actual}'");
    }
}

static void SequenceEqual<T>(
    IEnumerable<T> expected,
    IEnumerable<T> actual,
    string message)
{
    var expectedArray = expected.ToArray();
    var actualArray = actual.ToArray();
    if (!expectedArray.SequenceEqual(actualArray))
    {
        throw new TestFailureException(
            $"{message}: expected [{string.Join(", ", expectedArray)}], " +
            $"got [{string.Join(", ", actualArray)}]");
    }
}

static void True(bool condition, string message)
{
    if (!condition)
    {
        throw new TestFailureException(message);
    }
}

static void False(bool condition, string message) => True(!condition, message);

static async Task<TException> ThrowsAsync<TException>(
    Func<Task> action,
    string message)
    where TException : Exception
{
    try
    {
        await action();
    }
    catch (TException exception)
    {
        return exception;
    }
    catch (Exception exception)
    {
        throw new TestFailureException(
            $"{message}: expected {typeof(TException).Name}, " +
            $"got {exception.GetType().Name}");
    }

    throw new TestFailureException(
        $"{message}: expected {typeof(TException).Name}, no exception was thrown");
}

sealed class RecordingTransaction : IShipmentTransaction
{
    private readonly List<string> _trace;

    public RecordingTransaction(List<string> trace)
    {
        _trace = trace;
    }

    public bool IsActive { get; private set; }

    public int ExecutionCount { get; private set; }

    public async Task ExecuteAsync(
        Func<CancellationToken, Task> operation,
        CancellationToken cancellationToken)
    {
        if (IsActive)
        {
            throw new InvalidOperationException("Nested transactions are not supported.");
        }

        ExecutionCount++;
        IsActive = true;
        _trace.Add("transaction:begin");

        try
        {
            await operation(cancellationToken);
            IsActive = false;
            _trace.Add("transaction:commit");
        }
        catch
        {
            IsActive = false;
            _trace.Add("transaction:rollback");
            throw;
        }
    }
}

sealed class RecordingRepository : IShipmentRepository
{
    private readonly RecordingTransaction _transaction;
    private readonly List<string> _trace;

    public RecordingRepository(
        RecordingTransaction transaction,
        List<string> trace)
    {
        _transaction = transaction;
        _trace = trace;
    }

    public List<string> Released { get; } = new();

    public Task MarkReleasedAsync(
        string shipmentId,
        CancellationToken cancellationToken)
    {
        cancellationToken.ThrowIfCancellationRequested();
        if (!_transaction.IsActive)
        {
            throw new TestFailureException("the repository write must run in the transaction");
        }

        Released.Add(shipmentId);
        _trace.Add($"repository:release:{shipmentId}");
        return Task.CompletedTask;
    }
}

sealed class ScriptedHandler : INotificationHandler<ShipmentReleased>
{
    private readonly string _name;
    private readonly RecordingTransaction _transaction;
    private readonly List<string> _trace;
    private readonly bool _terminalFailure;
    private int _transientFailures;

    public ScriptedHandler(
        string name,
        RecordingTransaction transaction,
        List<string> trace,
        int transientFailures = 0,
        bool terminalFailure = false)
    {
        _name = name;
        _transaction = transaction;
        _trace = trace;
        _transientFailures = transientFailures;
        _terminalFailure = terminalFailure;
    }

    public int Attempts { get; private set; }

    public Task HandleAsync(
        ShipmentReleased notification,
        CancellationToken cancellationToken)
    {
        cancellationToken.ThrowIfCancellationRequested();
        Attempts++;
        var transactionState = _transaction.IsActive ? "active" : "closed";
        _trace.Add(
            $"handler:{_name}:attempt:{Attempts}:transaction:{transactionState}");

        if (_transaction.IsActive)
        {
            throw new TestFailureException("notification delivery started before commit");
        }

        if (_terminalFailure)
        {
            throw new InvalidOperationException($"{_name} rejected the notification");
        }

        if (_transientFailures > 0)
        {
            _transientFailures--;
            throw new TransientNotificationException(
                $"{_name} was temporarily unavailable");
        }

        return Task.CompletedTask;
    }
}

sealed record DispatchProbe(string Name);

sealed class DispatchProbeLog
{
    public List<(ScopedProbeHandler Handler, DispatchProbe Notification)> Deliveries { get; } = new();
}

sealed class ScopedProbeHandler : INotificationHandler<DispatchProbe>
{
    private readonly DispatchProbeLog _log;

    public ScopedProbeHandler(DispatchProbeLog log)
    {
        _log = log;
    }

    public Task HandleAsync(
        DispatchProbe notification,
        CancellationToken cancellationToken)
    {
        cancellationToken.ThrowIfCancellationRequested();
        _log.Deliveries.Add((this, notification));
        return Task.CompletedTask;
    }
}

sealed class TestFailureException : Exception
{
    public TestFailureException(string message)
        : base(message)
    {
    }
}
