namespace BillingPartition;

public sealed class InvoiceNotFoundException : Exception
{
    public InvoiceNotFoundException(string accountId, string invoiceNumber)
        : base($"Invoice '{invoiceNumber}' was not found in account '{accountId}'.")
    {
        AccountId = accountId;
        InvoiceNumber = invoiceNumber;
    }

    public string AccountId { get; }

    public string InvoiceNumber { get; }
}
