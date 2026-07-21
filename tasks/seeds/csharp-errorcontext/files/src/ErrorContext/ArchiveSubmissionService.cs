using System.Text.Json;

namespace ErrorContext;

public sealed class ArchiveSubmissionService
{
    public const string OperationName = "archive.submit";

    private readonly IArchiveClient _client;
    private readonly IOperatorLog _operatorLog;

    public ArchiveSubmissionService(IArchiveClient client, IOperatorLog operatorLog)
    {
        _client = client;
        _operatorLog = operatorLog;
    }

    public void Submit(ArchiveRequest request)
    {
        ArgumentNullException.ThrowIfNull(request);

        try
        {
            _client.Store(request);
        }
        catch (ArchiveClientException exception)
        {
            var serializedRequest = JsonSerializer.Serialize(request);
            var context = new OperationDiagnosticContext(
                OperationName,
                request.ArchiveId,
                CorrelationId: string.Empty);

            _operatorLog.WriteFailure(new OperatorFailure(
                context,
                ArchiveSubmissionException.FailureCode,
                serializedRequest,
                exception));

            var translated = new ArchiveSubmissionException(
                $"Archive submission failed: {exception.Message}",
                ArchiveSubmissionException.FailureCode,
                context,
                serializedRequest);

            _operatorLog.WriteFailure(new OperatorFailure(
                context,
                translated.ErrorCode,
                serializedRequest,
                translated));

            throw translated;
        }
    }
}
