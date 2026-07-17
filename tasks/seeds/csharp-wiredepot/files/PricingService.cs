namespace WireDepot;

/// <summary>
/// Pricing for wholesale accounts. Contract accounts get their negotiated
/// basis-point discount off list, applied exactly once per line when an
/// order is totalled. Shares round half up; all money is integer cents.
/// </summary>
public sealed class PricingService
{
    private readonly Dictionary<string, CatalogItem> _catalog;
    private readonly Dictionary<string, Customer> _customers;

    public PricingService(IEnumerable<CatalogItem> catalog, IEnumerable<Customer> customers)
    {
        _catalog = new Dictionary<string, CatalogItem>(StringComparer.OrdinalIgnoreCase);
        foreach (var item in catalog)
            _catalog.Add(item.Sku, item);
        _customers = new Dictionary<string, Customer>(StringComparer.Ordinal);
        foreach (var customer in customers)
            _customers.Add(customer.Id, customer);
    }

    /// <summary>Share of <paramref name="amountCents"/> at <paramref name="bps"/> basis points, rounded half up.</summary>
    public static long BpsShare(long amountCents, int bps) => (amountCents * bps + 5_000L) / 10_000L;

    /// <summary>Catalog lookup; SKU matching is case-insensitive by exact characters.</summary>
    public bool TryFindItem(string sku, out CatalogItem item) => _catalog.TryGetValue(sku, out item!);

    public Customer GetCustomer(string id) =>
        _customers.TryGetValue(id, out var customer)
            ? customer
            : throw new KeyNotFoundException($"unknown customer: {id}");

    /// <summary>Unit price for a contract account after its negotiated discount.</summary>
    public long ContractUnitCents(Customer customer, CatalogItem item) =>
        item.ListUnitCents - BpsShare(item.ListUnitCents, customer.ContractBps);

    /// <summary>
    /// Invoice total for an order: per-line totals with the account's
    /// contract discount taken on each line.
    /// </summary>
    public long OrderTotalCents(Order order)
    {
        var customer = GetCustomer(order.CustomerId);
        long total = 0;
        foreach (var line in order.Lines)
        {
            long lineTotal = line.Quantity * line.UnitCents;
            if (customer.ContractBps > 0)
                lineTotal -= BpsShare(lineTotal, customer.ContractBps);
            total += lineTotal;
        }
        return total;
    }
}
