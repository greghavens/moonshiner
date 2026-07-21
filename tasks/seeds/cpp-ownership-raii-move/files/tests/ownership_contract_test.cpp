#include "pipeline.hpp"

#include <algorithm>
#include <cstdlib>
#include <exception>
#include <initializer_list>
#include <iostream>
#include <stdexcept>
#include <string>
#include <type_traits>
#include <utility>
#include <vector>

static_assert(!std::is_copy_constructible_v<Pipeline>,
              "Pipeline must remain non-copyable");
static_assert(!std::is_copy_assignable_v<Pipeline>,
              "Pipeline must remain non-copy-assignable");
static_assert(std::is_nothrow_move_constructible_v<Pipeline>,
              "Pipeline must support noexcept move construction");
static_assert(std::is_nothrow_move_assignable_v<Pipeline>,
              "Pipeline must support noexcept move assignment");
static_assert(
    std::is_same_v<decltype(&Pipeline::source_handle),
                   cp_source* (Pipeline::*)() const noexcept>,
    "Pipeline::source_handle must retain its public signature");
static_assert(
    std::is_same_v<decltype(&Pipeline::decoder_handle),
                   cp_decoder* (Pipeline::*)() const noexcept>,
    "Pipeline::decoder_handle must retain its public signature");
static_assert(
    std::is_same_v<decltype(&Pipeline::sink_handle),
                   cp_sink* (Pipeline::*)() const noexcept>,
    "Pipeline::sink_handle must retain its public signature");

struct cp_source {
    int id;
};

struct cp_decoder {
    int id;
    cp_source* source;
};

struct cp_sink {
    int id;
    cp_decoder* decoder;
};

namespace {

enum class FailAt { none, source, decoder, sink };

FailAt fail_at = FailAt::none;
int next_id = 1;
int live_sources = 0;
int live_decoders = 0;
int live_sinks = 0;
bool invalid_close_order = false;
std::vector<std::string> events;
std::vector<cp_source*> open_sources;
std::vector<cp_decoder*> open_decoders;
std::vector<cp_sink*> open_sinks;
int failures = 0;

template <typename T>
bool contains(const std::vector<T*>& values, const T* value) {
    return std::find(values.begin(), values.end(), value) != values.end();
}

template <typename T>
void forget(std::vector<T*>& values, T* value) {
    values.erase(std::find(values.begin(), values.end(), value));
}

template <typename T>
T&& rvalue_alias(T& value) noexcept {
    return static_cast<T&&>(value);
}

void expect(bool condition, const char* message) {
    if (!condition) {
        ++failures;
        std::cerr << "FAIL: " << message << '\n';
    }
}

void expect_events(std::initializer_list<const char*> expected,
                   const char* message) {
    std::vector<std::string> wanted;
    for (const char* event : expected) {
        wanted.emplace_back(event);
    }
    if (events != wanted) {
        ++failures;
        std::cerr << "FAIL: " << message << "\n  expected:";
        for (const std::string& event : wanted) {
            std::cerr << ' ' << event;
        }
        std::cerr << "\n  actual:";
        for (const std::string& event : events) {
            std::cerr << ' ' << event;
        }
        std::cerr << '\n';
    }
}

void expect_clean(const char* message) {
    expect(live_sources == 0 && live_decoders == 0 && live_sinks == 0, message);
    expect(!invalid_close_order, "a parent handle was released before its child");
}

void reset(FailAt next_failure = FailAt::none) {
    expect_clean("test leaked a C handle");
    fail_at = next_failure;
    invalid_close_order = false;
    events.clear();
}

void expect_open_failure(FailAt point, const char* expected_message,
                         std::initializer_list<const char*> expected_events) {
    reset(point);
    try {
        Pipeline unused = Pipeline::open("broken");
        (void)unused;
        expect(false, "Pipeline::open should throw when a C factory fails");
    } catch (const std::runtime_error& error) {
        expect(std::string(error.what()) == expected_message,
               "Pipeline::open changed its failure message");
    } catch (...) {
        expect(false, "Pipeline::open threw the wrong exception type");
    }
    expect_events(expected_events, "construction failure cleanup order changed");
    expect_clean("construction failure leaked a C handle");
}

void test_normal_destruction() {
    reset();
    {
        Pipeline pipeline = Pipeline::open("normal");
        expect(pipeline.source_handle() != nullptr, "source handle is not exposed");
        expect(pipeline.decoder_handle() != nullptr, "decoder handle is not exposed");
        expect(pipeline.sink_handle() != nullptr, "sink handle is not exposed");
    }
    expect_events({"source.open", "decoder.create", "sink.create", "sink.destroy",
                   "decoder.destroy", "source.close"},
                  "normal destruction order changed");
    expect_clean("normal destruction leaked a C handle");
}

void test_failure_cleanup() {
    expect_open_failure(FailAt::source, "cp_source_open failed", {"source.open"});
    expect_open_failure(FailAt::decoder, "cp_decoder_create failed",
                        {"source.open", "decoder.create", "source.close"});
    expect_open_failure(FailAt::sink, "cp_sink_create failed",
                        {"source.open", "decoder.create", "sink.create",
                         "decoder.destroy", "source.close"});
}

void test_move_construction() {
    reset();
    {
        Pipeline original = Pipeline::open("move-construction");
        cp_source* source = original.source_handle();
        cp_decoder* decoder = original.decoder_handle();
        cp_sink* sink = original.sink_handle();

        Pipeline moved(std::move(original));
        expect(original.source_handle() == nullptr &&
                   original.decoder_handle() == nullptr &&
                   original.sink_handle() == nullptr,
               "move construction did not empty the source object");
        expect(moved.source_handle() == source && moved.decoder_handle() == decoder &&
                   moved.sink_handle() == sink,
               "move construction changed a C-facing handle");
    }
    expect_events({"source.open", "decoder.create", "sink.create", "sink.destroy",
                   "decoder.destroy", "source.close"},
                  "move construction duplicated or reordered destruction");
    expect_clean("move construction leaked a C handle");
}

void test_move_assignment() {
    reset();
    {
        Pipeline destination = Pipeline::open("old");
        Pipeline source = Pipeline::open("new");
        cp_source* new_source = source.source_handle();
        cp_decoder* new_decoder = source.decoder_handle();
        cp_sink* new_sink = source.sink_handle();

        events.clear();
        destination = std::move(source);

        expect_events({"sink.destroy", "decoder.destroy", "source.close"},
                      "move assignment released the old pipeline out of order");
        expect(source.source_handle() == nullptr && source.decoder_handle() == nullptr &&
                   source.sink_handle() == nullptr,
               "move assignment did not empty the source object");
        expect(destination.source_handle() == new_source &&
                   destination.decoder_handle() == new_decoder &&
                   destination.sink_handle() == new_sink,
               "move assignment changed a C-facing handle");
        expect(live_sources == 1 && live_decoders == 1 && live_sinks == 1,
               "move assignment did not retain exactly one pipeline");
        events.clear();
    }
    expect_events({"sink.destroy", "decoder.destroy", "source.close"},
                  "moved pipeline destruction order changed");
    expect_clean("move assignment leaked a C handle");
}

void test_self_move_assignment() {
    reset();
    {
        Pipeline pipeline = Pipeline::open("self-move");
        cp_source* source = pipeline.source_handle();
        cp_decoder* decoder = pipeline.decoder_handle();
        cp_sink* sink = pipeline.sink_handle();
        events.clear();

        pipeline = rvalue_alias(pipeline);

        expect(events.empty(), "self move assignment destroyed a live handle");
        expect(pipeline.source_handle() == source &&
                   pipeline.decoder_handle() == decoder &&
                   pipeline.sink_handle() == sink,
               "self move assignment changed the pipeline");
    }
    expect_events({"sink.destroy", "decoder.destroy", "source.close"},
                  "self-moved pipeline destruction order changed");
    expect_clean("self move assignment leaked a C handle");
}

}  // namespace

extern "C" cp_source* cp_source_open(const char*) {
    events.emplace_back("source.open");
    if (fail_at == FailAt::source) {
        return nullptr;
    }
    cp_source* source = new cp_source{next_id++};
    ++live_sources;
    open_sources.push_back(source);
    return source;
}

extern "C" cp_decoder* cp_decoder_create(cp_source* source) {
    events.emplace_back("decoder.create");
    if (fail_at == FailAt::decoder) {
        return nullptr;
    }
    cp_decoder* decoder = new cp_decoder{next_id++, source};
    ++live_decoders;
    open_decoders.push_back(decoder);
    return decoder;
}

extern "C" cp_sink* cp_sink_create(cp_decoder* decoder) {
    events.emplace_back("sink.create");
    if (fail_at == FailAt::sink) {
        return nullptr;
    }
    cp_sink* sink = new cp_sink{next_id++, decoder};
    ++live_sinks;
    open_sinks.push_back(sink);
    return sink;
}

extern "C" void cp_sink_destroy(cp_sink* sink) {
    events.emplace_back("sink.destroy");
    if (sink == nullptr || !contains(open_sinks, sink) ||
        !contains(open_decoders, sink->decoder)) {
        invalid_close_order = true;
        return;
    }
    forget(open_sinks, sink);
    --live_sinks;
    delete sink;
}

extern "C" void cp_decoder_destroy(cp_decoder* decoder) {
    events.emplace_back("decoder.destroy");
    const bool has_live_child =
        decoder != nullptr &&
        std::any_of(open_sinks.begin(), open_sinks.end(),
                    [decoder](const cp_sink* sink) { return sink->decoder == decoder; });
    if (decoder == nullptr || !contains(open_decoders, decoder) || has_live_child ||
        !contains(open_sources, decoder->source)) {
        invalid_close_order = true;
        return;
    }
    forget(open_decoders, decoder);
    --live_decoders;
    delete decoder;
}

extern "C" void cp_source_close(cp_source* source) {
    events.emplace_back("source.close");
    const bool has_live_child =
        source != nullptr &&
        std::any_of(open_decoders.begin(), open_decoders.end(),
                    [source](const cp_decoder* decoder) {
                        return decoder->source == source;
                    });
    if (source == nullptr || !contains(open_sources, source) || has_live_child) {
        invalid_close_order = true;
        return;
    }
    forget(open_sources, source);
    --live_sources;
    delete source;
}

int main() {
    test_normal_destruction();
    test_failure_cleanup();
    test_move_construction();
    test_move_assignment();
    test_self_move_assignment();

    if (failures != 0) {
        std::cerr << failures << " contract check(s) failed\n";
        return EXIT_FAILURE;
    }
    std::cout << "ownership contract checks passed\n";
    return EXIT_SUCCESS;
}
