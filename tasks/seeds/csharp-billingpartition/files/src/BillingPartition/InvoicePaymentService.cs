namespace BillingPartition;

public sealed class InvoicePaymentService(IInvoiceRepository repository)
{
    public async Task<Invoice> MarkPaidAsync(
        string accountId,
        string invoiceNumber,
        string actor,
        DateTimeOffset occurredAt,
        CancellationToken cancellationToken = default)
    {
        var invoice = await repository.FindByNumberAsync(
            invoiceNumber,
            cancellationToken);

        if (invoice is null)
        {
            throw new InvoiceNotFoundException(accountId, invoiceNumber);
        }

        if (invoice.Status == InvoiceStatus.Paid)
        {
            return invoice;
        }

        invoice.MarkPaid(actor, occurredAt);
        await repository.UpdateAsync(invoice, cancellationToken);

        return invoice;
    }
}
