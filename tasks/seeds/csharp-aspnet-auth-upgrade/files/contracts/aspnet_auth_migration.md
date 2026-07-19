# ASP.NET Core 10 authentication migration contract (protected local excerpt)

The retired application-specific registration snapshot is not consumed by the
current host.  Authentication is registered through `AddAuthentication`, with
explicit default authenticate and challenge schemes, followed by one
`AddScheme<TOptions,THandler>` registration per handler.

Authorization is registered through the current authorization builder.  The
`reports.read` policy uses the portal-header scheme and requires an
authenticated principal carrying scope `reports.read`.  The `operations` policy selects only the service-key scheme
and requires role `operations`.

`UseAuthentication` precedes `UseAuthorization`.  Challenges are delegated to
the policy-selected authentication scheme: the default report challenge is a
401 PortalHeader challenge, while operations challenges use ServiceKey.  A
host built with an injected server must start without opening a real socket.
