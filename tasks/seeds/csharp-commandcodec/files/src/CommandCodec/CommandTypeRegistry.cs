using System.Text.Json;

namespace CommandCodec;

public sealed class CommandTypeRegistry
{
    private static readonly JsonSerializerOptions SerializerOptions = new()
    {
        PropertyNameCaseInsensitive = true
    };

    private readonly Dictionary<(string Discriminator, int Version), Registration> _registrations = [];

    public void Register<TCommand>(
        string discriminator,
        int version,
        Func<JsonElement, TCommand>? decoder = null,
        Func<TCommand, CancellationToken, ValueTask>? validator = null)
        where TCommand : ICommand
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(discriminator);
        ArgumentOutOfRangeException.ThrowIfLessThan(version, 1);

        decoder ??= element =>
            JsonSerializer.Deserialize<TCommand>(element.GetRawText(), SerializerOptions)
            ?? throw new JsonException("The command payload was JSON null.");

        validator ??= static (_, _) => ValueTask.CompletedTask;

        _registrations[(discriminator, version)] = new Registration(
            element => decoder(element),
            (command, cancellationToken) => validator((TCommand)command, cancellationToken));
    }

    public async ValueTask<ICommand> DecodeAsync(
        CommandEnvelope envelope,
        CancellationToken cancellationToken = default)
    {
        ArgumentNullException.ThrowIfNull(envelope);
        cancellationToken.ThrowIfCancellationRequested();

        if (!_registrations.TryGetValue((envelope.Discriminator, envelope.Version), out var registration))
        {
            ThrowMissingRegistration(envelope);
        }

        ICommand command;
        try
        {
            using var document = JsonDocument.Parse(envelope.Payload);
            command = registration!.Decode(document.RootElement);
        }
        catch (JsonException exception)
        {
            throw new CommandDecodeException(
                DeadLetterReason.MalformedPayload,
                $"Malformed payload for discriminator '{envelope.Discriminator}' version {envelope.Version}: {exception.Message}",
                exception);
        }

        cancellationToken.ThrowIfCancellationRequested();

        try
        {
            await registration!.Validate(command, cancellationToken).ConfigureAwait(false);
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
            throw;
        }
        catch (Exception exception)
        {
            throw new CommandDecodeException(
                DeadLetterReason.ValidationFailed,
                $"Validation failed for discriminator '{envelope.Discriminator}' version {envelope.Version}: {exception.Message}",
                exception);
        }

        cancellationToken.ThrowIfCancellationRequested();
        return command;
    }

    private void ThrowMissingRegistration(CommandEnvelope envelope)
    {
        var versions = _registrations.Keys
            .Where(key => StringComparer.Ordinal.Equals(key.Discriminator, envelope.Discriminator))
            .Select(key => key.Version)
            .OrderBy(version => version)
            .ToArray();

        if (versions.Length == 0)
        {
            throw new CommandDecodeException(
                DeadLetterReason.UnknownDiscriminator,
                $"No command type is registered for discriminator '{envelope.Discriminator}'.");
        }

        throw new CommandDecodeException(
            DeadLetterReason.UnsupportedVersion,
            $"Discriminator '{envelope.Discriminator}' has no decoder for version {envelope.Version}. Registered versions: {string.Join(", ", versions)}.");
    }

    private sealed record Registration(
        Func<JsonElement, ICommand> Decode,
        Func<ICommand, CancellationToken, ValueTask> Validate);
}
