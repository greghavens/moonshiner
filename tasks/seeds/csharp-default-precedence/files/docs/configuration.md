# Configuration notes

## Original library contract

The first Relay prototype treated the property initializers on `RelayOptions` as
the application defaults. Binding a sparse section onto that instance changes the
keys present in the section and leaves the other initialized properties alone.
This behavior is relied on by applications which specify only a queue name.

## Older host guidance

An earlier host guide described sources in this order:

1. property initializers
2. appsettings
3. environment values
4. command-line values

That guide called the list a "lookup order" and said the first non-empty value was
kept. The sample in `examples/legacy-bootstrap.txt` was based on that wording.

## Operations guidance

The service and container templates use environment variables to override image
settings without rebuilding the image. A one-off command-line switch is expected
to override the otherwise resolved service configuration. Property initializers
are only fallbacks when no explicit source supplies a key.

If multiple explicit providers set the same Relay option, the host surfaces a
`CFG001` compatibility diagnostic. Diagnostics must name the final selected
provider and each shadowed provider. They are ordered by option path, then from
the least-specific shadowed provider to the most-specific one.
