using BillingPartition;

var tests = new (string Name, Func<Task> Run)[]
{
    ("repository contract requires account scope", RepositoryContractRequiresAccountScope),
    ("same-number invoices are isolated by account", SameNumberInvoicesAreIsolatedByAccount),
    ("an invoice in another account is reported missing", ForeignInvoiceIsReportedMissing),
    ("already-paid invoices remain idempotent", AlreadyPaidInvoiceIsIdempotent),
    ("missing invoices keep the not-found policy", MissingInvoiceKeepsNotFoundPolicy),
    ("new payments write the expected audit fields", NewPaymentWritesAuditFields)
};

var failures = new List<string>();
foreach (var test in tests)
{
    try
    {
        await test.Run();
        Console.WriteLine($"PASS {test.Name}");
    }
    catch (Exception exception)
    {
        failures.Add($"FAIL {test.Name}: {exception.Message}");
    }
}

foreach (var failure in failures)
{
    Console.Error.WriteLine(failure);
}

return failures.Count == 0 ? 0 : 1;

static Task RepositoryContractRequiresAccountScope()
{
    var methods = typeof(IInvoiceRepository).GetMethods();

    Equal(2, methods.Length, "repository method count");
    Equal(
        0,
        methods.Count(method => method.GetParameters().Length < 3),
        "unscoped repository method count");
    Equal(
        1,
        methods.Count(method =>
            method.Name == nameof(IInvoiceRepository.FindByNumberAsync) &&
            method.GetParameters() is var parameters &&
            parameters.Length == 3 &&
            parameters[0].ParameterType == typeof(string) &&
            parameters[1].ParameterType == typeof(string) &&
            parameters[2].ParameterType == typeof(CancellationToken)),
        "account-scoped lookup count");
    Equal(
        1,
        methods.Count(method =>
            method.Name == nameof(IInvoiceRepository.UpdateAsync) &&
            method.GetParameters() is var parameters &&
            parameters.Length == 3 &&
            parameters[0].ParameterType == typeof(string) &&
            parameters[1].ParameterType == typeof(Invoice) &&
            parameters[2].ParameterType == typeof(CancellationToken)),
        "account-scoped update count");

    return Task.CompletedTask;
}

static async Task SameNumberInvoicesAreIsolatedByAccount()
{
    var originalTime = At(2026, 1, 2, 8, 0);
    var paymentTime = At(2026, 2, 3, 9, 30);
    var repository = new RecordingInvoiceRepository(
        OpenInvoice("account-a", "INV-1042", "11111111-1111-1111-1111-111111111111", originalTime),
        OpenInvoice("account-b", "INV-1042", "22222222-2222-2222-2222-222222222222", originalTime));
    var service = new InvoicePaymentService(repository);

    var paid = await service.MarkPaidAsync(
        "account-b", "INV-1042", "user-7", paymentTime);

    Equal("account-b", paid.AccountId, "returned invoice account");
    InvoiceIsOpen(repository.Snapshot("account-a", "INV-1042"), originalTime);
    InvoiceIsPaid(repository.Snapshot("account-b", "INV-1042"), paymentTime, "user-7");
    SequenceEqual(
        new[] { "find:account-b:INV-1042", "update:account-b:INV-1042" },
        repository.Operations,
        "repository operations");
}

static async Task ForeignInvoiceIsReportedMissing()
{
    var originalTime = At(2026, 3, 1, 10, 0);
    var repository = new RecordingInvoiceRepository(
        OpenInvoice("account-a", "INV-2040", "33333333-3333-3333-3333-333333333333", originalTime));
    var service = new InvoicePaymentService(repository);

    var error = await ThrowsAsync<InvoiceNotFoundException>(() =>
        service.MarkPaidAsync("account-b", "INV-2040", "operator", At(2026, 3, 2, 10, 0)));

    Equal("account-b", error.AccountId, "not-found account");
    Equal("INV-2040", error.InvoiceNumber, "not-found invoice number");
    InvoiceIsOpen(repository.Snapshot("account-a", "INV-2040"), originalTime);
    SequenceEqual(
        new[] { "find:account-b:INV-2040" },
        repository.Operations,
        "repository operations");
}

static async Task AlreadyPaidInvoiceIsIdempotent()
{
    var paidAt = At(2026, 4, 5, 12, 15);
    var invoice = new Invoice(
        Guid.Parse("44444444-4444-4444-4444-444444444444"),
        "account-c",
        "INV-3001",
        InvoiceStatus.Paid,
        paidAt,
        "original-actor",
        paidAt);
    var repository = new RecordingInvoiceRepository(invoice);
    var service = new InvoicePaymentService(repository);

    var result = await service.MarkPaidAsync(
        "account-c", "INV-3001", "retrying-actor", At(2026, 5, 6, 13, 45));

    InvoiceIsPaid(result, paidAt, "original-actor");
    InvoiceIsPaid(repository.Snapshot("account-c", "INV-3001"), paidAt, "original-actor");
    SequenceEqual(
        new[] { "find:account-c:INV-3001" },
        repository.Operations,
        "repository operations");
}

static async Task MissingInvoiceKeepsNotFoundPolicy()
{
    var repository = new RecordingInvoiceRepository();
    var service = new InvoicePaymentService(repository);

    var error = await ThrowsAsync<InvoiceNotFoundException>(() =>
        service.MarkPaidAsync("account-d", "INV-404", "operator", At(2026, 6, 1, 7, 0)));

    Equal("account-d", error.AccountId, "not-found account");
    Equal("INV-404", error.InvoiceNumber, "not-found invoice number");
    SequenceEqual(
        new[] { "find:account-d:INV-404" },
        repository.Operations,
        "repository operations");
}

static async Task NewPaymentWritesAuditFields()
{
    var repository = new RecordingInvoiceRepository(
        OpenInvoice(
            "account-e",
            "INV-5000",
            "55555555-5555-5555-5555-555555555555",
            At(2026, 6, 2, 8, 0)));
    var service = new InvoicePaymentService(repository);
    var paymentTime = At(2026, 6, 3, 14, 20);

    await service.MarkPaidAsync("account-e", "INV-5000", "billing-job", paymentTime);

    InvoiceIsPaid(repository.Snapshot("account-e", "INV-5000"), paymentTime, "billing-job");
    SequenceEqual(
        new[] { "find:account-e:INV-5000", "update:account-e:INV-5000" },
        repository.Operations,
        "repository operations");
}

static Invoice OpenInvoice(
    string accountId,
    string number,
    string id,
    DateTimeOffset updatedAt) =>
    new(Guid.Parse(id), accountId, number, InvoiceStatus.Open, updatedAt, "seed");

static DateTimeOffset At(int year, int month, int day, int hour, int minute) =>
    new(year, month, day, hour, minute, 0, TimeSpan.Zero);

static void InvoiceIsOpen(Invoice invoice, DateTimeOffset originalTime)
{
    Equal(InvoiceStatus.Open, invoice.Status, "invoice status");
    Equal<DateTimeOffset?>(null, invoice.PaidAt, "paid-at audit value");
    Equal(originalTime, invoice.UpdatedAt, "updated-at audit value");
    Equal("seed", invoice.UpdatedBy, "updated-by audit value");
}

static void InvoiceIsPaid(Invoice invoice, DateTimeOffset paidAt, string actor)
{
    Equal(InvoiceStatus.Paid, invoice.Status, "invoice status");
    Equal<DateTimeOffset?>(paidAt, invoice.PaidAt, "paid-at audit value");
    Equal(paidAt, invoice.UpdatedAt, "updated-at audit value");
    Equal(actor, invoice.UpdatedBy, "updated-by audit value");
}

static void Equal<T>(T expected, T actual, string label)
{
    if (!EqualityComparer<T>.Default.Equals(expected, actual))
    {
        throw new InvalidOperationException(
            $"{label}: expected <{expected}>, actual <{actual}>");
    }
}

static void SequenceEqual(
    IReadOnlyList<string> expected,
    IReadOnlyList<string> actual,
    string label)
{
    if (!expected.SequenceEqual(actual, StringComparer.Ordinal))
    {
        throw new InvalidOperationException(
            $"{label}: expected [{string.Join(", ", expected)}], " +
            $"actual [{string.Join(", ", actual)}]");
    }
}

static async Task<TException> ThrowsAsync<TException>(Func<Task> action)
    where TException : Exception
{
    try
    {
        await action();
    }
    catch (TException exception)
    {
        return exception;
    }
    catch (Exception exception)
    {
        throw new InvalidOperationException(
            $"expected {typeof(TException).Name}, got {exception.GetType().Name}");
    }

    throw new InvalidOperationException(
        $"expected {typeof(TException).Name}, but no exception was thrown");
}

sealed class RecordingInvoiceRepository : IInvoiceRepository
{
    private readonly List<Invoice> invoices;

    public RecordingInvoiceRepository(params Invoice[] invoices)
    {
        this.invoices = invoices.Select(Clone).ToList();
    }

    public List<string> Operations { get; } = [];

    public Task<Invoice?> FindByNumberAsync(
        string invoiceNumber,
        CancellationToken cancellationToken = default)
    {
        cancellationToken.ThrowIfCancellationRequested();
        Operations.Add($"find:*:{invoiceNumber}");
        return Task.FromResult(CloneOrNull(invoices.FirstOrDefault(
            invoice => invoice.InvoiceNumber == invoiceNumber)));
    }

    public Task<Invoice?> FindByNumberAsync(
        string accountId,
        string invoiceNumber,
        CancellationToken cancellationToken = default)
    {
        cancellationToken.ThrowIfCancellationRequested();
        Operations.Add($"find:{accountId}:{invoiceNumber}");
        return Task.FromResult(CloneOrNull(invoices.FirstOrDefault(
            invoice => invoice.AccountId == accountId &&
                       invoice.InvoiceNumber == invoiceNumber)));
    }

    public Task UpdateAsync(
        Invoice invoice,
        CancellationToken cancellationToken = default)
    {
        cancellationToken.ThrowIfCancellationRequested();
        Operations.Add($"update:*:{invoice.InvoiceNumber}");
        Replace(invoices.FindIndex(stored => stored.Id == invoice.Id), invoice);
        return Task.CompletedTask;
    }

    public Task UpdateAsync(
        string accountId,
        Invoice invoice,
        CancellationToken cancellationToken = default)
    {
        cancellationToken.ThrowIfCancellationRequested();
        Operations.Add($"update:{accountId}:{invoice.InvoiceNumber}");

        if (invoice.AccountId != accountId)
        {
            throw new InvalidOperationException("The update crossed an account boundary.");
        }

        Replace(
            invoices.FindIndex(stored =>
                stored.AccountId == accountId && stored.Id == invoice.Id),
            invoice);
        return Task.CompletedTask;
    }

    public Invoice Snapshot(string accountId, string invoiceNumber)
    {
        var invoice = invoices.Single(stored =>
            stored.AccountId == accountId && stored.InvoiceNumber == invoiceNumber);
        return Clone(invoice);
    }

    private void Replace(int index, Invoice invoice)
    {
        if (index < 0)
        {
            throw new InvalidOperationException("The invoice to update does not exist.");
        }

        invoices[index] = Clone(invoice);
    }

    private static Invoice? CloneOrNull(Invoice? invoice) =>
        invoice is null ? null : Clone(invoice);

    private static Invoice Clone(Invoice invoice) =>
        new(
            invoice.Id,
            invoice.AccountId,
            invoice.InvoiceNumber,
            invoice.Status,
            invoice.UpdatedAt,
            invoice.UpdatedBy,
            invoice.PaidAt);
}
