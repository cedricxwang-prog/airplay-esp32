#include <assert.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include "audio_timing.h"
#include "ptp_clock.h"
static int64_t now=1000000;
static unsigned head,tail;
static struct {audio_frame_header_t header;int16_t pcm[704];} frames[8];
int64_t esp_timer_get_time(void){return now;}
bool ptp_clock_is_locked(void){return true;}
int64_t ptp_clock_get_offset_ns(void){return 0;}
void ptp_clock_get_stats(ptp_stats_t *s){memset(s,0,sizeof(*s));}
bool ntp_clock_is_locked(void){return false;}
int64_t ntp_clock_get_offset_ns(void){return 0;}
uint32_t audio_output_get_hardware_latency_us(void){return 0;}
uint32_t audio_output_get_pipeline_us(void){return 0;}
uint32_t audio_output_get_underruns(void){return 0;}
uint32_t audio_resample_get_latency_us(void){return 0;}
bool audio_stream_uses_buffer(audio_stream_type_t t){return true;}
int audio_buffer_get_frame_count(audio_buffer_t *b){return tail-head;}
bool audio_buffer_take(audio_buffer_t *b,void **p,size_t *n,TickType_t ticks){if(head==tail)return false;*p=&frames[head++];audio_frame_header_t *h=*p;*n=sizeof(*h)+h->samples_per_channel*h->channels*2;return true;}
void audio_buffer_return(audio_buffer_t *b,void *p){}
void audio_buffer_flush(audio_buffer_t *b){head=tail;}
bool audio_buffer_peek_newest_rtp(audio_buffer_t *b,uint32_t *p){if(head==tail)return false;*p=frames[tail-1].header.rtp_timestamp;return true;}
bool audio_buffer_bulk_start_rtp(audio_buffer_t *b,uint32_t *p){return false;}
static void queue(uint32_t ts,int channels,int value){assert(tail<8);frames[tail].header=(audio_frame_header_t){ts,352,channels,0};for(int i=0;i<352*channels;i++)frames[tail].pcm[i]=value;tail++;}
int main(void){
 audio_timing_t t;audio_buffer_t b={0};audio_stats_t stats={0};
 audio_stream_t s={0};s.format=(audio_format_t){.sample_rate=44100,.channels=1,.frame_size=352};
 audio_timing_init(&t,4096);t.playing=true;t.target_buffer_frames=1;t.anchor_valid=true;t.anchor_network_time_ns=1040000000;t.anchor_rtp_time=0;
 queue(0,1,123);int16_t out[706];memset(out,0x55,sizeof(out));
 assert(audio_timing_read(&t,&b,&s,&stats,out,353)==352);
 for(int i=0;i<704;i++)assert(out[i]==0);
 assert(t.pending_valid && !t.playout_started);
 now=1035000;
 size_t n=audio_timing_read(&t,&b,&s,&stats,out,353);assert(n==352);
 for(size_t i=0;i<n*2;i++)assert(out[i]==123);
 queue(0,1,999);queue(352,1,456);now+=7982;
 n=audio_timing_read(&t,&b,&s,&stats,out,353);assert(n==352);
 for(size_t i=0;i<n*2;i++)assert(out[i]==456);
 free(t.pending_frame);puts("PASS: early startup waits, mono fills both channels, duplicates discarded");
}
