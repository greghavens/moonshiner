namespace ParcelFlow;

internal sealed class NotificationRetryPolicy
{
    private const int MaximumAttempts = 3;

    public async Task DeliverAsync<TNotification>(
        INotificationHandler<TNotification> handler,
        TNotification notification,
        CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(handler);

        for (var attempt = 1; ; attempt++)
        {
            cancellationToken.ThrowIfCancellationRequested();

            try
            {
                await handler.HandleAsync(notification, cancellationToken).ConfigureAwait(false);
                return;
            }
            catch (TransientNotificationException) when (attempt < MaximumAttempts)
            {
                // Delivery is retried immediately; scheduling/backoff is owned by the transport.
            }
        }
    }
}
