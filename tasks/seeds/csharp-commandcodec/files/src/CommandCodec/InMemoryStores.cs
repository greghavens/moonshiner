namespace CommandCodec;

public sealed class InMemoryCommandQueue
{
    private readonly LinkedList<CommandEnvelope> _messages = [];

    public int Count => _messages.Count;

    public void Enqueue(CommandEnvelope envelope)
    {
        ArgumentNullException.ThrowIfNull(envelope);
        _messages.AddLast(envelope);
    }

    internal bool TryDequeue(out CommandEnvelope? envelope)
    {
        if (_messages.First is null)
        {
            envelope = null;
            return false;
        }

        envelope = _messages.First.Value;
        _messages.RemoveFirst();
        return true;
    }

    internal void ReturnToFront(CommandEnvelope envelope) => _messages.AddFirst(envelope);
}

public sealed class InMemoryDeadLetterStore
{
    private readonly Dictionary<string, DeadLetter> _letters = new(StringComparer.Ordinal);

    public int Count => _letters.Count;

    public IReadOnlyCollection<DeadLetter> All => _letters.Values.ToArray();

    public DeadLetter? Find(string messageId) =>
        _letters.TryGetValue(messageId, out var letter) ? letter : null;

    internal void Put(DeadLetter letter) => _letters[letter.Envelope.MessageId] = letter;

    internal bool TryTake(string messageId, out DeadLetter? letter)
    {
        if (!_letters.Remove(messageId, out var removed))
        {
            letter = null;
            return false;
        }

        letter = removed;
        return true;
    }
}

public sealed class InMemoryIdempotencyStore
{
    private readonly HashSet<string> _completed = new(StringComparer.Ordinal);

    public bool IsCompleted(string messageId) => _completed.Contains(messageId);

    internal void MarkCompleted(string messageId) => _completed.Add(messageId);
}
