namespace Persistence;

public sealed class InvoiceWriter(IInvoiceSessionFactory sessionFactory)
{
    public void Persist(Invoice invoice)
    {
        ArgumentNullException.ThrowIfNull(invoice);

        using var session = sessionFactory.OpenSession();

        try
        {
            session.SaveInvoice(invoice);
            session.SaveLineItems(invoice.Id, invoice.LineItems);
            session.SaveOutboxEvent(invoice.Id);
            session.Commit();
        }
        catch
        {
            session.Commit();
            throw;
        }
    }
}
