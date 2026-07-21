namespace AuthzScope;

public sealed record AccessActor(string UserId, IReadOnlySet<string> TenantIds)
{
    public bool IsMemberOf(string tenantId) => TenantIds.Contains(tenantId);
}

public sealed record DocumentRecord(
    string Id,
    string TenantId,
    string OwnerUserId,
    string Contents);

public enum ReadStatus
{
    Found,
    NotFound,
    Forbidden
}

public sealed record ReadResult(ReadStatus Status, DocumentRecord? Document)
{
    public static ReadResult Found(DocumentRecord document) => new(ReadStatus.Found, document);
    public static ReadResult NotFound() => new(ReadStatus.NotFound, null);
    public static ReadResult Forbidden() => new(ReadStatus.Forbidden, null);
}

public enum AuditDecision
{
    Granted,
    Denied,
    NotFound
}

public enum AuditReason
{
    AccessGranted,
    TenantMembershipRequired,
    ResourceMissing,
    ResourceOwnerMismatch
}

public sealed record DocumentAuditEvent(
    string ActorUserId,
    string TenantId,
    string DocumentId,
    AuditDecision Decision,
    AuditReason Reason);

public interface IDocumentRepository
{
    Task<DocumentRecord?> FindAsync(
        string tenantId,
        string documentId,
        CancellationToken cancellationToken);
}

public interface IDocumentAuditSink
{
    Task RecordAsync(DocumentAuditEvent auditEvent, CancellationToken cancellationToken);
}

public sealed class DocumentAccessService
{
    private readonly IDocumentRepository _documents;
    private readonly IDocumentAuditSink _audit;

    public DocumentAccessService(IDocumentRepository documents, IDocumentAuditSink audit)
    {
        _documents = documents;
        _audit = audit;
    }

    public async Task<ReadResult> ReadAsync(
        AccessActor actor,
        string tenantId,
        string documentId,
        CancellationToken cancellationToken = default)
    {
        if (!actor.IsMemberOf(tenantId))
        {
            await RecordAsync(
                actor,
                tenantId,
                documentId,
                AuditDecision.Denied,
                AuditReason.TenantMembershipRequired,
                cancellationToken);

            return ReadResult.Forbidden();
        }

        var document = await _documents.FindAsync(tenantId, documentId, cancellationToken);
        if (document is null)
        {
            await RecordAsync(
                actor,
                tenantId,
                documentId,
                AuditDecision.NotFound,
                AuditReason.ResourceMissing,
                cancellationToken);

            return ReadResult.NotFound();
        }

        await RecordAsync(
            actor,
            tenantId,
            documentId,
            AuditDecision.Granted,
            AuditReason.AccessGranted,
            cancellationToken);

        return ReadResult.Found(document);
    }

    private Task RecordAsync(
        AccessActor actor,
        string tenantId,
        string documentId,
        AuditDecision decision,
        AuditReason reason,
        CancellationToken cancellationToken) =>
        _audit.RecordAsync(
            new DocumentAuditEvent(
                actor.UserId,
                tenantId,
                documentId,
                decision,
                reason),
            cancellationToken);
}
