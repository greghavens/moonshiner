namespace MobileOfflineOutbox;

public sealed class OutboxReconciler
{
    private readonly JsonFileOutboxStore _store;
    private readonly IEditTransport _transport;

    public OutboxReconciler(
        JsonFileOutboxStore store,
        IEditTransport transport)
    {
        _store = store ?? throw new ArgumentNullException(nameof(store));
        _transport = transport ?? throw new ArgumentNullException(nameof(transport));
    }

    public async Task<ReconcileResult> ReconcileAsync(
        CancellationToken cancellationToken = default)
    {
        int acknowledgedCount = 0;

        while (true)
        {
            cancellationToken.ThrowIfCancellationRequested();
            OutboxEntry? head = _store.Head();

            if (head is null)
            {
                return new ReconcileResult(
                    ReconcileStopReason.Drained,
                    acknowledgedCount);
            }

            if (head.State == OutboxEntryState.Conflict)
            {
                return new ReconcileResult(
                    ReconcileStopReason.Conflict,
                    acknowledgedCount);
            }

            PushResult result = await _transport
                .PushAsync(head, cancellationToken)
                .ConfigureAwait(false);

            _store.AcknowledgeHead(head.OperationId);
            acknowledgedCount++;

            if (result.Outcome == PushOutcome.Conflict)
            {
                return new ReconcileResult(
                    ReconcileStopReason.Conflict,
                    acknowledgedCount);
            }

            if (result.Outcome == PushOutcome.RetryLater)
            {
                return new ReconcileResult(
                    ReconcileStopReason.RetryLater,
                    acknowledgedCount);
            }
        }
    }
}
