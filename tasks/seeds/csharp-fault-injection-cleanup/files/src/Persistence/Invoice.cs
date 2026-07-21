namespace Persistence;

public sealed record Invoice(string Id, decimal Total, IReadOnlyList<string> LineItems);
