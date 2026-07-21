using System.Text.Json;

namespace CommandCodec;

public sealed class CommandCodec
{
    private static readonly JsonSerializerOptions SerializerOptions = new()
    {
        PropertyNameCaseInsensitive = true
    };

    private readonly CommandTypeRegistry _registry;

    public CommandCodec(CommandTypeRegistry registry)
    {
        _registry = registry ?? throw new ArgumentNullException(nameof(registry));
    }

    public ValueTask<ICommand> DecodeAsync(
        CommandEnvelope envelope,
        CancellationToken cancellationToken = default)
    {
        ArgumentNullException.ThrowIfNull(envelope);
        cancellationToken.ThrowIfCancellationRequested();

        // Compatibility path for envelopes written before discriminators were introduced.
        // Modern queued envelopes still arrive here, even though their discriminator is
        // an application-defined registry key rather than an assembly-qualified CLR name.
        var commandType = Type.GetType(envelope.Discriminator, throwOnError: false, ignoreCase: false);
        if (commandType is null || !typeof(ICommand).IsAssignableFrom(commandType))
        {
            return ValueTask.FromException<ICommand>(new CommandDecodeException(
                DeadLetterReason.UnknownDiscriminator,
                $"No CLR command type could be loaded for '{envelope.Discriminator}'."));
        }

        try
        {
            var command = JsonSerializer.Deserialize(envelope.Payload, commandType, SerializerOptions) as ICommand;
            return command is null
                ? ValueTask.FromException<ICommand>(new CommandDecodeException(
                    DeadLetterReason.MalformedPayload,
                    $"Payload for '{envelope.Discriminator}' produced no command."))
                : ValueTask.FromResult(command);
        }
        catch (JsonException exception)
        {
            return ValueTask.FromException<ICommand>(new CommandDecodeException(
                DeadLetterReason.MalformedPayload,
                $"Malformed payload for '{envelope.Discriminator}': {exception.Message}",
                exception));
        }
    }
}
