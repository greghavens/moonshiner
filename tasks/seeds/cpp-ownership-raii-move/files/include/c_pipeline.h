#ifndef C_PIPELINE_H
#define C_PIPELINE_H

#ifdef __cplusplus
extern "C" {
#endif

typedef struct cp_source cp_source;
typedef struct cp_decoder cp_decoder;
typedef struct cp_sink cp_sink;

cp_source* cp_source_open(const char* name);
cp_decoder* cp_decoder_create(cp_source* source);
cp_sink* cp_sink_create(cp_decoder* decoder);

void cp_source_close(cp_source* source);
void cp_decoder_destroy(cp_decoder* decoder);
void cp_sink_destroy(cp_sink* sink);

#ifdef __cplusplus
}
#endif

#endif
