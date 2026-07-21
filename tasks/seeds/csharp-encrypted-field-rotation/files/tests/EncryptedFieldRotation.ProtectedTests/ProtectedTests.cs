using System.Security.Cryptography;
using EncryptedFieldRotation;

internal static class ProtectedTests
{
    private const string FirstSecret = "violet-otter-184";
    private const string BrokenSecret = "this-value-must-never-be-logged";
    private const string LastSecret = "marble-raven-927";

    public static void CodecDecryptsEveryConfiguredVersion()
    {
        var fixture = new Fixture();
        var oldEnvelope = fixture.Codec.Encrypt(FirstSecret, keyVersion: 1);
        var newEnvelope = fixture.Codec.Encrypt(LastSecret);

        AssertEx.Equal(1, oldEnvelope.KeyVersion);
        AssertEx.Equal(2, newEnvelope.KeyVersion);
        AssertEx.Equal(FirstSecret, fixture.Codec.Decrypt(oldEnvelope));
        AssertEx.Equal(LastSecret, fixture.Codec.Decrypt(newEnvelope));
    }

    public static void RotationUpgradesEnvelope()
    {
        var fixture = new Fixture();
        var id = Guid.Parse("11111111-1111-1111-1111-111111111111");
        var original = fixture.Codec.Encrypt(FirstSecret, keyVersion: 1);
        fixture.Repository.Add(new CustomerRecord(id, original));

        var report = fixture.Rotator.RotateBatch();
        var persisted = fixture.Repository.Get(id).Secret;

        AssertEx.Equal(1, report.Rotated);
        AssertEx.Equal(0, report.Skipped);
        AssertEx.Empty(report.FailedRecordIds);
        AssertEx.Equal(2, persisted.KeyVersion);
        AssertEx.Equal(FirstSecret, fixture.Codec.Decrypt(persisted));
        AssertEx.False(original.Ciphertext.SequenceEqual(persisted.Ciphertext), "Ciphertext was not replaced.");
        AssertEx.DoesNotContain(FirstSecret, fixture.Logger.RenderedMessages);
    }

    public static void RotationIsResumable()
    {
        var fixture = new Fixture();
        var id = Guid.Parse("22222222-2222-2222-2222-222222222222");
        fixture.Repository.Add(new CustomerRecord(id, fixture.Codec.Encrypt(FirstSecret, keyVersion: 1)));

        var firstReport = fixture.Rotator.RotateBatch();
        var afterFirstRun = fixture.Repository.Get(id).Secret;
        var secondReport = fixture.Rotator.RotateBatch();
        var afterSecondRun = fixture.Repository.Get(id).Secret;

        AssertEx.Equal(1, firstReport.Rotated);
        AssertEx.Equal(0, secondReport.Rotated);
        AssertEx.Equal(1, secondReport.Skipped);
        AssertEx.Same(afterFirstRun, afterSecondRun);
    }

    public static void AuthenticationFailureIsIsolated()
    {
        var fixture = new Fixture();
        var firstId = Guid.Parse("33333333-3333-3333-3333-333333333333");
        var brokenId = Guid.Parse("44444444-4444-4444-4444-444444444444");
        var lastId = Guid.Parse("55555555-5555-5555-5555-555555555555");

        fixture.Repository.Add(new CustomerRecord(firstId, fixture.Codec.Encrypt(FirstSecret, keyVersion: 1)));
        var tampered = fixture.Codec.Encrypt(BrokenSecret, keyVersion: 1);
        tampered.AuthenticationTag[0] ^= 0x80;
        fixture.Repository.Add(new CustomerRecord(brokenId, tampered));
        fixture.Repository.Add(new CustomerRecord(lastId, fixture.Codec.Encrypt(LastSecret, keyVersion: 1)));

        var report = fixture.Rotator.RotateBatch();

        AssertEx.Equal(2, report.Rotated);
        AssertEx.Equal(0, report.Skipped);
        AssertEx.SequenceEqual(new[] { brokenId }, report.FailedRecordIds);
        AssertEx.Equal(2, fixture.Repository.Get(firstId).Secret.KeyVersion);
        AssertEx.Same(tampered, fixture.Repository.Get(brokenId).Secret);
        AssertEx.Equal(2, fixture.Repository.Get(lastId).Secret.KeyVersion);
        AssertEx.Equal(FirstSecret, fixture.Codec.Decrypt(fixture.Repository.Get(firstId).Secret));
        AssertEx.Equal(LastSecret, fixture.Codec.Decrypt(fixture.Repository.Get(lastId).Secret));
        AssertEx.DoesNotContain(FirstSecret, fixture.Logger.RenderedMessages);
        AssertEx.DoesNotContain(BrokenSecret, fixture.Logger.RenderedMessages);
        AssertEx.DoesNotContain(LastSecret, fixture.Logger.RenderedMessages);
        AssertEx.Equal(1, fixture.Logger.WarningMessages.Count);
        AssertEx.Contains(brokenId.ToString(), fixture.Logger.WarningMessages);
        AssertEx.Contains("key version 1", fixture.Logger.WarningMessages);

        var firstAfterFailure = fixture.Repository.Get(firstId).Secret;
        var lastAfterFailure = fixture.Repository.Get(lastId).Secret;
        fixture.Repository.ReplaceForRecovery(
            brokenId,
            fixture.Codec.Encrypt(BrokenSecret, keyVersion: 1));

        var resumed = fixture.Rotator.RotateBatch();

        AssertEx.Equal(1, resumed.Rotated);
        AssertEx.Equal(2, resumed.Skipped);
        AssertEx.Empty(resumed.FailedRecordIds);
        AssertEx.Same(firstAfterFailure, fixture.Repository.Get(firstId).Secret);
        AssertEx.Same(lastAfterFailure, fixture.Repository.Get(lastId).Secret);
        AssertEx.Equal(BrokenSecret, fixture.Codec.Decrypt(fixture.Repository.Get(brokenId).Secret));
        AssertEx.DoesNotContain(BrokenSecret, fixture.Logger.RenderedMessages);
    }

    public static void MissingKeyVersionIsNotSwallowed()
    {
        var keyRing = new EncryptionKeyRing(
            currentVersion: 2,
            new Dictionary<int, byte[]>
            {
                [2] = Enumerable.Range(101, 32).Select(value => (byte)value).ToArray(),
            });
        var codec = new EncryptedFieldCodec(keyRing, new CountingNonceSource());
        var repository = new MemoryRepository();
        var logger = new MemoryLogger();
        var rotator = new EncryptedFieldRotator(repository, codec, keyRing, logger);
        var id = Guid.Parse("66666666-6666-6666-6666-666666666666");
        var unavailable = new EncryptedValue(99, new byte[12], new byte[1], new byte[16]);
        repository.Add(new CustomerRecord(id, unavailable));

        AssertEx.Throws<KeyNotFoundException>(() => rotator.RotateBatch());
        AssertEx.Same(unavailable, repository.Get(id).Secret);
        AssertEx.Empty(logger.WarningMessages);
    }

    private sealed class Fixture
    {
        public Fixture()
        {
            var keyRing = new EncryptionKeyRing(
                currentVersion: 2,
                new Dictionary<int, byte[]>
                {
                    [1] = Enumerable.Range(1, 32).Select(value => (byte)value).ToArray(),
                    [2] = Enumerable.Range(101, 32).Select(value => (byte)value).ToArray(),
                });
            Codec = new EncryptedFieldCodec(keyRing, new CountingNonceSource());
            Repository = new MemoryRepository();
            Logger = new MemoryLogger();
            Rotator = new EncryptedFieldRotator(Repository, Codec, keyRing, Logger);
        }

        public EncryptedFieldCodec Codec { get; }

        public MemoryRepository Repository { get; }

        public MemoryLogger Logger { get; }

        public EncryptedFieldRotator Rotator { get; }
    }

    private sealed class CountingNonceSource : INonceSource
    {
        private byte _next = 1;

        public void Fill(Span<byte> destination)
        {
            for (var index = 0; index < destination.Length; index++)
            {
                destination[index] = _next++;
            }
        }
    }

    internal sealed class MemoryRepository : ICustomerRepository
    {
        private readonly List<CustomerRecord> _records = [];

        public void Add(CustomerRecord record) => _records.Add(record);

        public CustomerRecord Get(Guid id) => _records.Single(record => record.Id == id);

        public IReadOnlyList<CustomerRecord> ReadBatch() => _records.ToArray();

        public bool TryReplaceSecret(Guid id, EncryptedValue expected, EncryptedValue replacement)
        {
            var index = _records.FindIndex(record => record.Id == id);
            if (index < 0 || !ReferenceEquals(_records[index].Secret, expected))
            {
                return false;
            }

            _records[index] = _records[index] with { Secret = replacement };
            return true;
        }

        public void ReplaceForRecovery(Guid id, EncryptedValue replacement)
        {
            var index = _records.FindIndex(record => record.Id == id);
            _records[index] = _records[index] with { Secret = replacement };
        }
    }

    internal sealed class MemoryLogger : IRotationLogger
    {
        private readonly List<string> _informationMessages = [];
        private readonly List<string> _warningMessages = [];

        public IReadOnlyList<string> RenderedMessages =>
            _informationMessages.Concat(_warningMessages).ToArray();

        public IReadOnlyList<string> WarningMessages => _warningMessages;

        public void Information(string messageTemplate, params object[] arguments) =>
            _informationMessages.Add(string.Format(messageTemplate, arguments));

        public void Warning(string messageTemplate, params object[] arguments) =>
            _warningMessages.Add(string.Format(messageTemplate, arguments));
    }
}

internal static class AssertEx
{
    public static void Equal<T>(T expected, T actual)
    {
        if (!EqualityComparer<T>.Default.Equals(expected, actual))
        {
            throw new InvalidOperationException($"Expected <{expected}> but found <{actual}>.");
        }
    }

    public static void Empty<T>(IEnumerable<T> values)
    {
        if (values.Any())
        {
            throw new InvalidOperationException("Expected an empty sequence.");
        }
    }

    public static void Same(object expected, object actual)
    {
        if (!ReferenceEquals(expected, actual))
        {
            throw new InvalidOperationException("Expected references to be identical.");
        }
    }

    public static void False(bool condition, string message)
    {
        if (condition)
        {
            throw new InvalidOperationException(message);
        }
    }

    public static void SequenceEqual<T>(IEnumerable<T> expected, IEnumerable<T> actual)
    {
        if (!expected.SequenceEqual(actual))
        {
            throw new InvalidOperationException("Sequences are not equal.");
        }
    }

    public static void Contains(string expected, IEnumerable<string> values)
    {
        if (!values.Any(value => value.Contains(expected, StringComparison.Ordinal)))
        {
            throw new InvalidOperationException($"Expected a message containing <{expected}>.");
        }
    }

    public static void DoesNotContain(string unexpected, IEnumerable<string> values)
    {
        if (values.Any(value => value.Contains(unexpected, StringComparison.Ordinal)))
        {
            throw new InvalidOperationException($"A message disclosed <{unexpected}>.");
        }
    }

    public static void Throws<TException>(Action action)
        where TException : Exception
    {
        try
        {
            action();
        }
        catch (TException)
        {
            return;
        }
        catch (Exception exception)
        {
            throw new InvalidOperationException(
                $"Expected {typeof(TException).Name} but found {exception.GetType().Name}.",
                exception);
        }

        throw new InvalidOperationException(
            $"Expected {typeof(TException).Name} but no exception was thrown.");
    }
}
