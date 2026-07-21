namespace SagaCompensation;

public enum SagaStatus
{
    Running,
    Compensating,
    Compensated
}

public enum SagaStepStatus
{
    Pending,
    Started,
    Succeeded,
    Failed,
    Compensating,
    Compensated
}

public sealed record SagaStepState(
    string Name,
    SagaStepStatus Status,
    string? CompensationId = null);

public sealed record SagaState(
    string Id,
    long Version,
    SagaStatus Status,
    IReadOnlyList<SagaStepState> Steps);

public enum CompensationRunResult
{
    WaitingForForwardOutcome,
    Completed
}

public interface ISagaStore
{
    void Create(string sagaId, IReadOnlyList<string> stepNames);

    SagaState Load(string sagaId);

    bool TryReplace(SagaState expected, SagaState replacement);
}

public interface IStepCompensator
{
    void Compensate(string sagaId, string stepName, string compensationId);
}
