namespace Persistence;

public interface IInvoiceSession : IDisposable
{
    void SaveInvoice(Invoice invoice);

    void SaveLineItems(string invoiceId, IReadOnlyList<string> lineItems);

    void SaveOutboxEvent(string invoiceId);

    void Commit();

    void Rollback();
}
