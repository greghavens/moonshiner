# Relay startup incident

At 18:07 UTC, the new relay pods began restarting before opening their HTTP
listener. The release changed only the manifest renderer. The application
image and `deploy/relay.json` were unchanged.

The startup check reported:

```text
startup aborted: invalid configuration: upstream URL is required; effective config: http.address=":9090" upstream.url="" upstream.token="fixture-relay-token-61b26c" database.dsn="postgres://relay:fixture-db-password-903fa1@db.example.com:5432/ledger?sslmode=require"
```

This is also a credential-exposure incident: startup diagnostics are collected
centrally, so configuration failures must not print the upstream token or the
database DSN.

The relevant release inputs are all checked in:

- the binary has a usable built-in `upstream.url`;
- `deploy/relay.json` sets the deployment-specific database, token, and listen
  address, but deliberately leaves `upstream.url` to the built-in default;
- `deploy/container.env` is the exact env-file shape produced by the new
  renderer. It emits recognized optional variables even when no deployment
  override was supplied.

Reproduce from the repository root:

```sh
go run ./cmd/relay --config deploy/relay.json --env-file deploy/container.env
```

The startup check is local. It validates configuration and prints a safe
summary; it does not bind a port or contact the URLs in the fixture.
