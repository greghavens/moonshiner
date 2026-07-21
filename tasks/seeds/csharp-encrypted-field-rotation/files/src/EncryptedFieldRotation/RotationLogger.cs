namespace EncryptedFieldRotation;

public interface IRotationLogger
{
    void Information(string messageTemplate, params object[] arguments);

    void Warning(string messageTemplate, params object[] arguments);
}
