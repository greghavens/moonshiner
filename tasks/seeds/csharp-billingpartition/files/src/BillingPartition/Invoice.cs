namespace BillingPartition;

public enum InvoiceStatus
{
    Open,
    Paid
}

public sealed class Invoice
{
    public Invoice(
        Guid id,
        string accountId,
        string invoiceNumber,
        InvoiceStatus status,
        DateTimeOffset updatedAt,
        string updatedBy,
        DateTimeOffset? paidAt = null)
    {
        Id = id;
        AccountId = accountId;
        InvoiceNumber = invoiceNumber;
        Status = status;
        UpdatedAt = updatedAt;
        UpdatedBy = updatedBy;
        PaidAt = paidAt;
    }

    public Guid Id { get; }

    public string AccountId { get; }

    public string InvoiceNumber { get; }

    public InvoiceStatus Status { get; private set; }

    public DateTimeOffset UpdatedAt { get; private set; }

    public string UpdatedBy { get; private set; }

    public DateTimeOffset? PaidAt { get; private set; }

    public void MarkPaid(string actor, DateTimeOffset occurredAt)
    {
        if (Status == InvoiceStatus.Paid)
        {
            return;
        }

        Status = InvoiceStatus.Paid;
        PaidAt = occurredAt;
        UpdatedAt = occurredAt;
        UpdatedBy = actor;
    }
}
