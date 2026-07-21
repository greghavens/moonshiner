namespace ErrorContext;

public sealed record ArchivePayload(
    string DocumentBody,
    string BearerToken,
    string CustomerNote);

public sealed record ArchiveRequest(
    string ArchiveId,
    string CorrelationId,
    ArchivePayload Payload);

// This is the complete allowlist for context that may cross the service boundary.
public sealed record OperationDiagnosticContext(
    string Operation,
    string ArchiveId,
    string CorrelationId);

public interface IArchiveClient
{
    void Store(ArchiveRequest request);
}

public interface IOperatorLog
{
    void WriteFailure(OperatorFailure failure);
}

public sealed record OperatorFailure(
    OperationDiagnosticContext Context,
    string ErrorCode,
    string SerializedContext,
    Exception Exception);
