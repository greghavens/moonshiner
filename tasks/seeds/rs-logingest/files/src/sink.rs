//! Batching output sink.

/// Records accumulate in a buffer and move to the output in `batch_size`
/// blocks — the production writer emits each block with a single syscall, so
/// batch boundaries matter for throughput but must never cost us records.
pub(crate) struct BatchSink {
    batch_size: usize,
    buf: Vec<String>,
    out: Vec<String>,
}

impl BatchSink {
    pub(crate) fn new(batch_size: usize) -> BatchSink {
        BatchSink {
            batch_size: batch_size.max(1),
            buf: Vec::new(),
            out: Vec::new(),
        }
    }

    pub(crate) fn push(&mut self, record: String) {
        self.buf.push(record);
        self.flush_full();
    }

    /// Move a completed batch to the output.
    fn flush_full(&mut self) {
        if self.buf.len() >= self.batch_size {
            self.out.append(&mut self.buf);
        }
    }

    /// Close the sink and hand back everything written.
    pub(crate) fn finish(mut self) -> Vec<String> {
        self.flush_full();
        self.out
    }
}
