namespace MoonRelay.Configuration;

public sealed class RelayOptions
{
    public string QueueName { get; set; } = "default";

    public int MaxConcurrency { get; set; } = 4;

    public int DrainTimeoutSeconds { get; set; } = 30;
}
