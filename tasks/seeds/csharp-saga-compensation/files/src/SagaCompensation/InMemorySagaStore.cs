namespace SagaCompensation;

public sealed class InMemorySagaStore : ISagaStore
{
    private readonly object _gate = new();
    private readonly Dictionary<string, SagaState> _sagas =
        new(StringComparer.Ordinal);

    public void Create(string sagaId, IReadOnlyList<string> stepNames)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(sagaId);
        ArgumentNullException.ThrowIfNull(stepNames);

        if (stepNames.Count == 0 ||
            stepNames.Any(string.IsNullOrWhiteSpace) ||
            stepNames.Distinct(StringComparer.Ordinal).Count() != stepNames.Count)
        {
            throw new ArgumentException(
                "A saga requires at least one uniquely named step.",
                nameof(stepNames));
        }

        var saga = new SagaState(
            sagaId,
            Version: 0,
            SagaStatus.Running,
            stepNames.Select(name =>
                    new SagaStepState(name, SagaStepStatus.Pending))
                .ToArray());

        lock (_gate)
        {
            if (!_sagas.TryAdd(sagaId, Clone(saga)))
            {
                throw new InvalidOperationException($"Saga '{sagaId}' already exists.");
            }
        }
    }

    public SagaState Load(string sagaId)
    {
        lock (_gate)
        {
            if (!_sagas.TryGetValue(sagaId, out SagaState? saga))
            {
                throw new KeyNotFoundException($"Saga '{sagaId}' was not found.");
            }

            return Clone(saga);
        }
    }

    public bool TryReplace(SagaState expected, SagaState replacement)
    {
        ArgumentNullException.ThrowIfNull(expected);
        ArgumentNullException.ThrowIfNull(replacement);

        if (!StringComparer.Ordinal.Equals(expected.Id, replacement.Id) ||
            replacement.Version != expected.Version + 1)
        {
            throw new ArgumentException("A replacement must advance the same saga by one version.");
        }

        lock (_gate)
        {
            if (!_sagas.TryGetValue(expected.Id, out SagaState? current) ||
                current.Version != expected.Version)
            {
                return false;
            }

            _sagas[expected.Id] = Clone(replacement);
            return true;
        }
    }

    private static SagaState Clone(SagaState saga) =>
        saga with { Steps = saga.Steps.ToArray() };
}
