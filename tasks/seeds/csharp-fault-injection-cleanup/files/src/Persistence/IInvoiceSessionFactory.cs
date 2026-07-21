namespace Persistence;

public interface IInvoiceSessionFactory
{
    IInvoiceSession OpenSession();
}
