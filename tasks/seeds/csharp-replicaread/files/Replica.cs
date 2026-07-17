namespace ReplicaRead;

/// <summary>
/// One read replica of the config store. An implementation completes the
/// returned task when the replica answers, faults it when the replica errors,
/// and honors the token by completing the task as canceled. The transport
/// owns all waiting — callers only ever observe the task.
/// </summary>
public interface IReplica
{
    string Name { get; }

    Task<string> ReadAsync(string key, CancellationToken ct);
}
