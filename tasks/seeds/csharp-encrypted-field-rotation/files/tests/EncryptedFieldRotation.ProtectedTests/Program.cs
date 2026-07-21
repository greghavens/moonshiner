using EncryptedFieldRotation;

var tests = new (string Name, Action Run)[]
{
    ("codec decrypts legacy and current envelopes", ProtectedTests.CodecDecryptsEveryConfiguredVersion),
    ("rotation upgrades metadata and preserves the value", ProtectedTests.RotationUpgradesEnvelope),
    ("rotation is resumable", ProtectedTests.RotationIsResumable),
    ("authenticated failures are isolated and contain no plaintext logs", ProtectedTests.AuthenticationFailureIsIsolated),
    ("configuration errors are not swallowed", ProtectedTests.MissingKeyVersionIsNotSwallowed),
};

var failed = 0;
foreach (var test in tests)
{
    try
    {
        test.Run();
        Console.WriteLine($"PASS {test.Name}");
    }
    catch (Exception exception)
    {
        failed++;
        Console.Error.WriteLine($"FAIL {test.Name}");
        Console.Error.WriteLine(exception);
    }
}

return failed == 0 ? 0 : 1;
