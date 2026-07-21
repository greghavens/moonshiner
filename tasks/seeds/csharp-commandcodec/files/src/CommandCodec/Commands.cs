namespace CommandCodec;

public interface ICommand;

public sealed record CommandEnvelope(
    string MessageId,
    string Discriminator,
    int Version,
    string Payload);

public enum DeadLetterReason
{
    UnknownDiscriminator,
    UnsupportedVersion,
    MalformedPayload,
    ValidationFailed,
    HandlerFailed
}

public sealed record DeadLetter(
    CommandEnvelope Envelope,
    DeadLetterReason Reason,
    string Detail);

public sealed class CommandDecodeException : Exception
{
    public CommandDecodeException(
        DeadLetterReason reason,
        string message,
        Exception? innerException = null)
        : base(message, innerException)
    {
        Reason = reason;
    }

    public DeadLetterReason Reason { get; }
}
