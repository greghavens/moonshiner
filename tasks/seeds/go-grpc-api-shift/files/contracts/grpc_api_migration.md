# Generated gRPC API migration contract (protected local excerpt)

The regenerated server contract embeds `stockpb.UnimplementedStockServer` for
forward compatibility.  Server-streaming methods now accept
`stockpb.ServerStreamingServer[stockpb.StockUpdate]`; the old generated
`Stock_WatchServer` interface is not part of the current package.

Unary interceptors remain attached at registration and see the canonical full
method name.  Application status mapping is unchanged: missing stock is
`NotFound`, repository failures are `Internal`, and caller cancellation is
`Canceled`, with original causes retained.

Streaming cancellation comes from `stream.Context()`.  Once canceled, the
handler stops sending and returns `Canceled`; a non-cancellation send status is
returned unchanged.  Generated message field numbers and wire bytes do not change as part of
the service API migration.
