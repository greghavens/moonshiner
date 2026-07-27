# Artifact key routing protocol, revision 4

An artifact identifier on the wire has this shape:

```
<cell>/<namespace>/<artifact-key>
```

`cell` and `namespace` are routing tokens. Each is non-empty and consists only
of ASCII letters, ASCII digits, `.`, `_`, or `-`. Routers compare ASCII letters
in these two fields without regard to case. They do not trim whitespace and do
not perform locale-sensitive or Unicode case conversion.

Everything after the second `/` is the artifact key. The key is a non-empty,
opaque UTF-8 string owned by the artifact store. Slashes in it are data, not
more routing fields. The store compares key bytes exactly. In particular, case,
punctuation, and Unicode spelling are significant.

Responses and inventory displays use the identifier spelling recorded at
creation time. A router may derive a private comparison key, but that value is
not a replacement display identifier.

When loading a journal, two records that compare as the same identifier under
the rules above are corrupt input. Readers must reject the journal rather than
choosing a winner. Diagnostic ordering is bytewise so operators get the same
report on every host.
