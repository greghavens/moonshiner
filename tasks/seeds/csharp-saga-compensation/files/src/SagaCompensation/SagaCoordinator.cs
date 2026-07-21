namespace SagaCompensation;

public sealed class SagaCoordinator
{
    private static long _compensationAttempt;

    private readonly ISagaStore _store;
    private readonly IStepCompensator _compensator;

    public SagaCoordinator(ISagaStore store, IStepCompensator compensator)
    {
        _store = store ?? throw new ArgumentNullException(nameof(store));
        _compensator = compensator ?? throw new ArgumentNullException(nameof(compensator));
    }

    public void StartSaga(string sagaId, params string[] stepNames) =>
        _store.Create(sagaId, stepNames);

    public void MarkStepStarted(string sagaId, string stepName) =>
        UpdateStep(
            sagaId,
            stepName,
            saga => saga.Status == SagaStatus.Running,
            SagaStepStatus.Pending,
            SagaStepStatus.Started);

    public void RecordStepSucceeded(string sagaId, string stepName) =>
        UpdateStep(
            sagaId,
            stepName,
            saga => saga.Status is SagaStatus.Running or SagaStatus.Compensating,
            SagaStepStatus.Started,
            SagaStepStatus.Succeeded);

    public void RecordStepFailed(string sagaId, string stepName) =>
        UpdateStep(
            sagaId,
            stepName,
            saga => saga.Status is SagaStatus.Running or SagaStatus.Compensating,
            SagaStepStatus.Started,
            SagaStepStatus.Failed);

    public void BeginCompensation(string sagaId)
    {
        while (true)
        {
            SagaState current = _store.Load(sagaId);
            if (current.Status is SagaStatus.Compensating or SagaStatus.Compensated)
            {
                return;
            }

            SagaState replacement = current with
            {
                Version = current.Version + 1,
                Status = SagaStatus.Compensating
            };

            if (_store.TryReplace(current, replacement))
            {
                return;
            }
        }
    }

    public CompensationRunResult ResumeCompensation(string sagaId)
    {
        SagaState saga = _store.Load(sagaId);
        if (saga.Status == SagaStatus.Running)
        {
            throw new InvalidOperationException("Compensation has not been requested.");
        }

        if (saga.Status == SagaStatus.Compensated)
        {
            return CompensationRunResult.Completed;
        }

        foreach (SagaStepState step in saga.Steps.Reverse())
        {
            if (step.Status is not (SagaStepStatus.Succeeded or SagaStepStatus.Compensating))
            {
                continue;
            }

            string compensationId =
                $"{saga.Id}:{step.Name}:attempt-{Interlocked.Increment(ref _compensationAttempt)}";
            _compensator.Compensate(saga.Id, step.Name, compensationId);
            MarkStepCompensated(saga.Id, step.Name);
        }

        MarkSagaCompensated(saga.Id);
        return CompensationRunResult.Completed;
    }

    private void MarkStepCompensated(string sagaId, string stepName) =>
        UpdateStep(
            sagaId,
            stepName,
            saga => saga.Status == SagaStatus.Compensating,
            new[] { SagaStepStatus.Succeeded, SagaStepStatus.Compensating },
            SagaStepStatus.Compensated);

    private void MarkSagaCompensated(string sagaId)
    {
        while (true)
        {
            SagaState current = _store.Load(sagaId);
            if (current.Status == SagaStatus.Compensated)
            {
                return;
            }

            if (current.Status != SagaStatus.Compensating)
            {
                throw new InvalidOperationException("The saga is not compensating.");
            }

            SagaState replacement = current with
            {
                Version = current.Version + 1,
                Status = SagaStatus.Compensated
            };

            if (_store.TryReplace(current, replacement))
            {
                return;
            }
        }
    }

    private void UpdateStep(
        string sagaId,
        string stepName,
        Func<SagaState, bool> sagaGuard,
        SagaStepStatus expected,
        SagaStepStatus replacement) =>
        UpdateStep(sagaId, stepName, sagaGuard, new[] { expected }, replacement);

    private void UpdateStep(
        string sagaId,
        string stepName,
        Func<SagaState, bool> sagaGuard,
        IReadOnlyCollection<SagaStepStatus> expected,
        SagaStepStatus replacement)
    {
        while (true)
        {
            SagaState current = _store.Load(sagaId);
            int index = FindStep(current, stepName);
            SagaStepState step = current.Steps[index];

            if (step.Status == replacement)
            {
                return;
            }

            if (!sagaGuard(current) || !expected.Contains(step.Status))
            {
                throw new InvalidOperationException(
                    $"Cannot move step '{stepName}' from {step.Status} to {replacement}.");
            }

            SagaStepState[] steps = current.Steps.ToArray();
            steps[index] = step with
            {
                Status = replacement,
                CompensationId = replacement == SagaStepStatus.Compensating
                    ? step.CompensationId
                    : null
            };

            SagaState next = current with
            {
                Version = current.Version + 1,
                Steps = steps
            };

            if (_store.TryReplace(current, next))
            {
                return;
            }
        }
    }

    private static int FindStep(SagaState saga, string stepName)
    {
        for (int index = 0; index < saga.Steps.Count; index++)
        {
            if (StringComparer.Ordinal.Equals(saga.Steps[index].Name, stepName))
            {
                return index;
            }
        }

        throw new KeyNotFoundException(
            $"Step '{stepName}' was not found in saga '{saga.Id}'.");
    }
}
