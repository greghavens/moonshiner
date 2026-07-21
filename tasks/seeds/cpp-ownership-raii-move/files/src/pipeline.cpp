#include "pipeline.hpp"

#include <stdexcept>

Pipeline Pipeline::open(const char* name) {
    cp_source* source = cp_source_open(name);
    if (source == nullptr) {
        throw std::runtime_error("cp_source_open failed");
    }

    cp_decoder* decoder = cp_decoder_create(source);
    if (decoder == nullptr) {
        cp_source_close(source);
        throw std::runtime_error("cp_decoder_create failed");
    }

    cp_sink* sink = cp_sink_create(decoder);
    if (sink == nullptr) {
        cp_decoder_destroy(decoder);
        cp_source_close(source);
        throw std::runtime_error("cp_sink_create failed");
    }

    return Pipeline(source, decoder, sink);
}

Pipeline::Pipeline(cp_source* source, cp_decoder* decoder, cp_sink* sink) noexcept
    : source_(source), decoder_(decoder), sink_(sink) {}

Pipeline::~Pipeline() noexcept {
    if (sink_ != nullptr) {
        cp_sink_destroy(sink_);
    }
    if (decoder_ != nullptr) {
        cp_decoder_destroy(decoder_);
    }
    if (source_ != nullptr) {
        cp_source_close(source_);
    }
}
