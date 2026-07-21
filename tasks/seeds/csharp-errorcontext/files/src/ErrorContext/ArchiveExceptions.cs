namespace ErrorContext;

public sealed class ArchiveClientException : Exception
{
    public ArchiveClientException(string message)
        : base(message)
    {
    }
}

public sealed class ArchiveSubmissionException : Exception
{
    public const string FailureCode = "archive_transport_error";
    public const string UserSafeFailureMessage =
        "The document could not be archived. Try again later.";

    public ArchiveSubmissionException(
        string message,
        string errorCode,
        OperationDiagnosticContext context,
        string serializedContext,
        Exception? innerException = null)
        : base(message, innerException)
    {
        ErrorCode = errorCode;
        Context = context;
        SerializedContext = serializedContext;
    }

    public string ErrorCode { get; }

    public OperationDiagnosticContext Context { get; }

    public string SerializedContext { get; }
}
