#ifndef PIPELINE_HPP
#define PIPELINE_HPP

#include "c_pipeline.h"

class Pipeline final {
public:
    static Pipeline open(const char* name);

    ~Pipeline() noexcept;

    Pipeline(const Pipeline&) = delete;
    Pipeline& operator=(const Pipeline&) = delete;

    cp_source* source_handle() const noexcept { return source_; }
    cp_decoder* decoder_handle() const noexcept { return decoder_; }
    cp_sink* sink_handle() const noexcept { return sink_; }

private:
    Pipeline(cp_source* source, cp_decoder* decoder, cp_sink* sink) noexcept;

    cp_source* source_;
    cp_decoder* decoder_;
    cp_sink* sink_;
};

#endif
