namespace CommandCodec;

public interface ICommandExecutor
{
    ValueTask ExecuteAsync(ICommand command, CancellationToken cancellationToken);
}

public enum ProcessingStatus
{
    Empty,
    Processed,
    DeadLettered,
    AlreadyProcessed
}

public sealed record ProcessingResult(
    ProcessingStatus Status,
    string? MessageId = null,
    DeadLetter? DeadLetter = null);

public sealed class QueuedCommandProcessor
{
    private readonly InMemoryCommandQueue _queue;
    private readonly InMemoryDeadLetterStore _deadLetters;
    private readonly InMemoryIdempotencyStore _idempotency;
    private readonly CommandCodec _codec;
    private readonly ICommandExecutor _executor;

    public QueuedCommandProcessor(
        InMemoryCommandQueue queue,
        InMemoryDeadLetterStore deadLetters,
        InMemoryIdempotencyStore idempotency,
        CommandCodec codec,
        ICommandExecutor executor)
    {
        _queue = queue ?? throw new ArgumentNullException(nameof(queue));
        _deadLetters = deadLetters ?? throw new ArgumentNullException(nameof(deadLetters));
        _idempotency = idempotency ?? throw new ArgumentNullException(nameof(idempotency));
        _codec = codec ?? throw new ArgumentNullException(nameof(codec));
        _executor = executor ?? throw new ArgumentNullException(nameof(executor));
    }

    public async ValueTask<ProcessingResult> ProcessNextAsync(
        CancellationToken cancellationToken = default)
    {
        cancellationToken.ThrowIfCancellationRequested();

        if (!_queue.TryDequeue(out var envelope))
        {
            return new ProcessingResult(ProcessingStatus.Empty);
        }

        if (_idempotency.IsCompleted(envelope!.MessageId))
        {
            return new ProcessingResult(ProcessingStatus.AlreadyProcessed, envelope.MessageId);
        }

        try
        {
            var command = await _codec.DecodeAsync(envelope, cancellationToken).ConfigureAwait(false);
            await _executor.ExecuteAsync(command, cancellationToken).ConfigureAwait(false);
            cancellationToken.ThrowIfCancellationRequested();
            _idempotency.MarkCompleted(envelope.MessageId);
            return new ProcessingResult(ProcessingStatus.Processed, envelope.MessageId);
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
            _queue.ReturnToFront(envelope);
            throw;
        }
        catch (CommandDecodeException exception)
        {
            var letter = new DeadLetter(envelope, exception.Reason, exception.Message);
            _deadLetters.Put(letter);
            return new ProcessingResult(ProcessingStatus.DeadLettered, envelope.MessageId, letter);
        }
        catch (Exception exception)
        {
            var letter = new DeadLetter(
                envelope,
                DeadLetterReason.HandlerFailed,
                $"Handler failed for message '{envelope.MessageId}': {exception.Message}");
            _deadLetters.Put(letter);
            return new ProcessingResult(ProcessingStatus.DeadLettered, envelope.MessageId, letter);
        }
    }

    public bool ReplayDeadLetter(
        string messageId,
        CancellationToken cancellationToken = default)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(messageId);
        cancellationToken.ThrowIfCancellationRequested();

        if (!_deadLetters.TryTake(messageId, out var letter))
        {
            return false;
        }

        _queue.Enqueue(letter!.Envelope);
        return true;
    }
}
