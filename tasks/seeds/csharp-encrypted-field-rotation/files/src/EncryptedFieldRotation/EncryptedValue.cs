namespace EncryptedFieldRotation;

public sealed record EncryptedValue(
    int KeyVersion,
    byte[] Nonce,
    byte[] Ciphertext,
    byte[] AuthenticationTag);
