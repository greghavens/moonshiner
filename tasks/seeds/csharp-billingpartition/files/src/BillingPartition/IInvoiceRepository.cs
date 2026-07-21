namespace BillingPartition;

public interface IInvoiceRepository
{
    Task<Invoice?> FindByNumberAsync(
        string invoiceNumber,
        CancellationToken cancellationToken = default);

    Task<Invoice?> FindByNumberAsync(
        string accountId,
        string invoiceNumber,
        CancellationToken cancellationToken = default);

    Task UpdateAsync(
        Invoice invoice,
        CancellationToken cancellationToken = default);

    Task UpdateAsync(
        string accountId,
        Invoice invoice,
        CancellationToken cancellationToken = default);
}
