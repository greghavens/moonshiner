# Framework 7.2 encrypted-attribute migration contract (protected local copy)

The Rails-like runtime in this fixture models the current encrypted-attribute
boundary without loading a gem or contacting a service.

- The old `encrypts(name, scheme:)` declaration is removed. Models declare
  `encrypted_attribute(name, key_provider:)`.
- A key provider names one primary write scheme and an ordered set of previous
  read schemes. New ciphertext uses only the primary scheme; previous schemes
  are read-only compatibility paths.
- Version 2 ciphertext includes a per-write nonce. Saving the same plaintext
  twice must not produce the same bytes.
- Validation runs on plaintext before encryption. A rejected record does not
  consume a nonce or write a row.
- Reading previous ciphertext is side-effect free. Rotation does not rewrite
  a row merely because it was loaded.
- Authentication, malformed payload, and unknown-scheme failures surface as
  `DecryptionError`; they are not converted to blank plaintext or validation
  errors.
