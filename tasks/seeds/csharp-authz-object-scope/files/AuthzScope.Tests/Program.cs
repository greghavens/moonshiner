using AuthzScope;

var tests = new (string Name, Func<Task> Run)[]
{
    ("owner receives the document and a grant audit", OwnerReceivesDocument),
    ("tenant outsider is forbidden before document lookup", OutsiderIsForbidden),
    ("missing document is reported and audited as not found", MissingDocumentIsNotFound),
    ("another member's document is masked as not found", ForeignDocumentIsMasked),
    ("case-different user is masked and audited as an ownership denial", CaseDifferentUserIsDenied),
    ("opaque user identifiers use ordinal comparison", OrdinalUserIdComparisonIsRequired)
};

var failures = 0;
foreach (var test in tests)
{
    try
    {
        await test.Run();
        Console.WriteLine($"PASS {test.Name}");
    }
    catch (Exception exception)
    {
        failures++;
        Console.Error.WriteLine($"FAIL {test.Name}: {exception.Message}");
    }
}

Console.WriteLine($"{tests.Length - failures} passed, {failures} failed");
return failures == 0 ? 0 : 1;

static async Task OwnerReceivesDocument()
{
    var fixture = Fixture.Create();
    var result = await fixture.Service.ReadAsync(
        Actor("alice", "tenant-a"),
        "tenant-a",
        "quarterly-plan");

    Check.Equal(ReadStatus.Found, result.Status, "owner response status");
    Check.Same(fixture.Document, result.Document, "returned document");
    Check.SingleAudit(
        fixture.Audit.Events,
        "alice",
        "tenant-a",
        "quarterly-plan",
        AuditDecision.Granted,
        AuditReason.AccessGranted);
}

static async Task OutsiderIsForbidden()
{
    var fixture = Fixture.Create();
    var result = await fixture.Service.ReadAsync(
        Actor("mallory", "tenant-b"),
        "tenant-a",
        "quarterly-plan");

    Check.Equal(ReadStatus.Forbidden, result.Status, "outsider response status");
    Check.Null(result.Document, "outsider response document");
    Check.Equal(0, fixture.Repository.LookupCount, "repository lookup count");
    Check.SingleAudit(
        fixture.Audit.Events,
        "mallory",
        "tenant-a",
        "quarterly-plan",
        AuditDecision.Denied,
        AuditReason.TenantMembershipRequired);
}

static async Task MissingDocumentIsNotFound()
{
    var fixture = Fixture.Create();
    var result = await fixture.Service.ReadAsync(
        Actor("alice", "tenant-a"),
        "tenant-a",
        "does-not-exist");

    Check.Equal(ReadStatus.NotFound, result.Status, "missing response status");
    Check.Null(result.Document, "missing response document");
    Check.SingleAudit(
        fixture.Audit.Events,
        "alice",
        "tenant-a",
        "does-not-exist",
        AuditDecision.NotFound,
        AuditReason.ResourceMissing);
}

static async Task ForeignDocumentIsMasked()
{
    var fixture = Fixture.Create();
    var result = await fixture.Service.ReadAsync(
        Actor("bob", "tenant-a"),
        "tenant-a",
        "quarterly-plan");

    Check.Equal(ReadStatus.NotFound, result.Status, "foreign-owner response status");
    Check.Null(result.Document, "foreign-owner response document");
    Check.SingleAudit(
        fixture.Audit.Events,
        "bob",
        "tenant-a",
        "quarterly-plan",
        AuditDecision.Denied,
        AuditReason.ResourceOwnerMismatch);
}

static async Task CaseDifferentUserIsDenied()
{
    var fixture = Fixture.Create();
    var result = await fixture.Service.ReadAsync(
        Actor("ALICE", "tenant-a"),
        "tenant-a",
        "quarterly-plan");

    Check.Equal(ReadStatus.NotFound, result.Status, "case-different response status");
    Check.Null(result.Document, "case-different response document");
    Check.SingleAudit(
        fixture.Audit.Events,
        "ALICE",
        "tenant-a",
        "quarterly-plan",
        AuditDecision.Denied,
        AuditReason.ResourceOwnerMismatch);
}

static async Task OrdinalUserIdComparisonIsRequired()
{
    var fixture = Fixture.Create();
    var opaqueUserId = "alice\0";
    var result = await fixture.Service.ReadAsync(
        Actor(opaqueUserId, "tenant-a"),
        "tenant-a",
        "quarterly-plan");

    Check.Equal(ReadStatus.NotFound, result.Status, "ordinal-comparison response status");
    Check.Null(result.Document, "ordinal-comparison response document");
    Check.SingleAudit(
        fixture.Audit.Events,
        opaqueUserId,
        "tenant-a",
        "quarterly-plan",
        AuditDecision.Denied,
        AuditReason.ResourceOwnerMismatch);
}

static AccessActor Actor(string userId, params string[] tenantIds) =>
    new(userId, new HashSet<string>(tenantIds, StringComparer.Ordinal));

internal sealed class Fixture
{
    private Fixture(
        DocumentRecord document,
        RecordingRepository repository,
        RecordingAuditSink audit,
        DocumentAccessService service)
    {
        Document = document;
        Repository = repository;
        Audit = audit;
        Service = service;
    }

    public DocumentRecord Document { get; }
    public RecordingRepository Repository { get; }
    public RecordingAuditSink Audit { get; }
    public DocumentAccessService Service { get; }

    public static Fixture Create()
    {
        var document = new DocumentRecord(
            "quarterly-plan",
            "tenant-a",
            "alice",
            "confidential roadmap");
        var repository = new RecordingRepository(document);
        var audit = new RecordingAuditSink();
        return new Fixture(
            document,
            repository,
            audit,
            new DocumentAccessService(repository, audit));
    }
}

internal sealed class RecordingRepository(params DocumentRecord[] documents) : IDocumentRepository
{
    public int LookupCount { get; private set; }

    public Task<DocumentRecord?> FindAsync(
        string tenantId,
        string documentId,
        CancellationToken cancellationToken)
    {
        cancellationToken.ThrowIfCancellationRequested();
        LookupCount++;
        return Task.FromResult<DocumentRecord?>(
            documents.SingleOrDefault(document =>
                StringComparer.Ordinal.Equals(document.TenantId, tenantId) &&
                StringComparer.Ordinal.Equals(document.Id, documentId)));
    }
}

internal sealed class RecordingAuditSink : IDocumentAuditSink
{
    public List<DocumentAuditEvent> Events { get; } = [];

    public Task RecordAsync(DocumentAuditEvent auditEvent, CancellationToken cancellationToken)
    {
        cancellationToken.ThrowIfCancellationRequested();
        Events.Add(auditEvent);
        return Task.CompletedTask;
    }
}

internal static class Check
{
    public static void Equal<T>(T expected, T actual, string label)
        where T : notnull
    {
        if (!EqualityComparer<T>.Default.Equals(expected, actual))
        {
            throw new InvalidOperationException(
                $"{label}: expected <{expected}>, actual <{actual}>");
        }
    }

    public static void Null(object? actual, string label)
    {
        if (actual is not null)
        {
            throw new InvalidOperationException($"{label}: expected <null>, actual <{actual}>");
        }
    }

    public static void Same(object expected, object? actual, string label)
    {
        if (!ReferenceEquals(expected, actual))
        {
            throw new InvalidOperationException($"{label}: expected the repository instance");
        }
    }

    public static void SingleAudit(
        IReadOnlyList<DocumentAuditEvent> events,
        string actorUserId,
        string tenantId,
        string documentId,
        AuditDecision decision,
        AuditReason reason)
    {
        Equal(1, events.Count, "audit event count");
        var auditEvent = events[0];
        Equal(actorUserId, auditEvent.ActorUserId, "audit actor");
        Equal(tenantId, auditEvent.TenantId, "audit tenant");
        Equal(documentId, auditEvent.DocumentId, "audit document");
        Equal(decision, auditEvent.Decision, "audit decision");
        Equal(reason, auditEvent.Reason, "audit reason");
    }
}
