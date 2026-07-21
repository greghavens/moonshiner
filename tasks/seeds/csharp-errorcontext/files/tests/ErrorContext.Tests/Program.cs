using System.Text.Json;
using ErrorContext;

var tests = new (string Name, Action Body)[]
{
    ("successful submissions are unchanged", SuccessfulSubmissionsAreUnchanged),
    ("translation carries minimal operation context", TranslationCarriesMinimalOperationContext),
    ("translation preserves the cause and a safe message", TranslationPreservesCauseAndSafeMessage),
    ("failure emits one useful operator event", FailureEmitsOneUsefulOperatorEvent)
};

var failures = new List<string>();
foreach (var (name, body) in tests)
{
    try
    {
        body();
        Console.WriteLine($"PASS {name}");
    }
    catch (Exception exception)
    {
        failures.Add(name);
        Console.Error.WriteLine($"FAIL {name}: {exception.GetType().Name}: {exception.Message}");
    }
}

if (failures.Count != 0)
{
    Console.Error.WriteLine($"{failures.Count} test(s) failed: {string.Join(", ", failures)}");
    return 1;
}

Console.WriteLine($"All {tests.Length} tests passed.");
return 0;

static void SuccessfulSubmissionsAreUnchanged()
{
    var client = new RecordingArchiveClient();
    var log = new RecordingOperatorLog();
    var service = new ArchiveSubmissionService(client, log);
    var request = Request();

    service.Submit(request);

    Equal(1, client.Requests.Count, "the client call count");
    Same(request, client.Requests[0], "the original request must be forwarded");
    Equal(0, log.Failures.Count, "successful work must not create failure events");
}

static void TranslationCarriesMinimalOperationContext()
{
    var (exception, _, _) = CaptureFailure();

    Equal(ArchiveSubmissionService.OperationName, exception.Context.Operation, "operation name");
    Equal("archive-1042", exception.Context.ArchiveId, "archive ID");
    Equal("corr-7f91", exception.Context.CorrelationId, "correlation ID");

    using var document = JsonDocument.Parse(exception.SerializedContext);
    var root = document.RootElement;
    var names = root.EnumerateObject().Select(property => property.Name).Order().ToArray();
    SequenceEqual(
        new[] { "ArchiveId", "CorrelationId", "Operation" },
        names,
        "serialized context must contain only allowlisted fields");
    Equal(ArchiveSubmissionService.OperationName, root.GetProperty("Operation").GetString(), "serialized operation");
    Equal("archive-1042", root.GetProperty("ArchiveId").GetString(), "serialized archive ID");
    Equal("corr-7f91", root.GetProperty("CorrelationId").GetString(), "serialized correlation ID");

    DoesNotContain(exception.SerializedContext, "Payload", "the payload container must not be serialized");
    DoesNotContain(exception.SerializedContext, "DocumentBody", "document field names are private");
    DoesNotContain(exception.SerializedContext, "BearerToken", "credential field names are private");
    DoesNotContain(exception.SerializedContext, "CustomerNote", "customer field names are private");
    DoesNotContain(exception.SerializedContext, "PRIVATE-DOCUMENT-CONTENTS", "document contents are private");
    DoesNotContain(exception.SerializedContext, "secret-bearer-token", "credentials are private");
    DoesNotContain(exception.SerializedContext, "medical appointment", "customer notes are private");
}

static void TranslationPreservesCauseAndSafeMessage()
{
    var (exception, source, _) = CaptureFailure();

    Equal(ArchiveSubmissionException.FailureCode, exception.ErrorCode, "stable failure code");
    Equal(
        ArchiveSubmissionException.UserSafeFailureMessage,
        exception.Message,
        "the public message must be fixed and user-safe");
    Same(source, exception.InnerException, "the transport exception must remain the inner cause");
    DoesNotContain(exception.Message, "storage-node-3", "upstream details must not reach users");
    DoesNotContain(exception.Message, "private upstream response", "upstream response must not reach users");
}

static void FailureEmitsOneUsefulOperatorEvent()
{
    var (exception, _, log) = CaptureFailure();

    Equal(1, log.Failures.Count, "a translated failure must be logged exactly once");
    var failure = log.Failures[0];
    Same(exception, failure.Exception, "operators should receive the translated exception with its cause chain");
    Equal(exception.ErrorCode, failure.ErrorCode, "logged failure code");
    Equal(exception.Context, failure.Context, "the log must use the exception's context");
    Equal(exception.SerializedContext, failure.SerializedContext, "the log must use the same minimized serialization");
    Equal("corr-7f91", failure.Context.CorrelationId, "logged correlation ID");
}

static (ArchiveSubmissionException Exception, ArchiveClientException Source, RecordingOperatorLog Log)
    CaptureFailure()
{
    var source = new ArchiveClientException(
        "storage-node-3 returned a private upstream response");
    var client = new RecordingArchiveClient(source);
    var log = new RecordingOperatorLog();
    var service = new ArchiveSubmissionService(client, log);

    var exception = Throws<ArchiveSubmissionException>(
        () => service.Submit(Request()),
        "transport failures must be translated");
    return (exception, source, log);
}

static ArchiveRequest Request() => new(
    ArchiveId: "archive-1042",
    CorrelationId: "corr-7f91",
    Payload: new ArchivePayload(
        DocumentBody: "PRIVATE-DOCUMENT-CONTENTS",
        BearerToken: "secret-bearer-token",
        CustomerNote: "medical appointment"));

static void Equal<T>(T expected, T actual, string message)
{
    if (!EqualityComparer<T>.Default.Equals(expected, actual))
    {
        throw new TestFailureException($"{message}: expected '{expected}', got '{actual}'");
    }
}

static void Same(object? expected, object? actual, string message)
{
    if (!ReferenceEquals(expected, actual))
    {
        throw new TestFailureException(message);
    }
}

static void SequenceEqual<T>(IEnumerable<T> expected, IEnumerable<T> actual, string message)
{
    if (!expected.SequenceEqual(actual))
    {
        throw new TestFailureException(
            $"{message}: expected [{string.Join(", ", expected)}], got [{string.Join(", ", actual)}]");
    }
}

static void DoesNotContain(string value, string forbidden, string message)
{
    if (value.Contains(forbidden, StringComparison.Ordinal))
    {
        throw new TestFailureException($"{message}: found '{forbidden}'");
    }
}

static TException Throws<TException>(Action action, string message)
    where TException : Exception
{
    try
    {
        action();
    }
    catch (TException exception)
    {
        return exception;
    }
    catch (Exception exception)
    {
        throw new TestFailureException(
            $"{message}: expected {typeof(TException).Name}, got {exception.GetType().Name}");
    }

    throw new TestFailureException($"{message}: no exception was thrown");
}

sealed class RecordingArchiveClient : IArchiveClient
{
    private readonly ArchiveClientException? _failure;

    public RecordingArchiveClient(ArchiveClientException? failure = null)
    {
        _failure = failure;
    }

    public List<ArchiveRequest> Requests { get; } = new();

    public void Store(ArchiveRequest request)
    {
        Requests.Add(request);
        if (_failure is not null)
        {
            throw _failure;
        }
    }
}

sealed class RecordingOperatorLog : IOperatorLog
{
    public List<OperatorFailure> Failures { get; } = new();

    public void WriteFailure(OperatorFailure failure) => Failures.Add(failure);
}

sealed class TestFailureException : Exception
{
    public TestFailureException(string message)
        : base(message)
    {
    }
}
