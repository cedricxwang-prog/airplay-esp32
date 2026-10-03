#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "audio_resample.h"
static void conversion(uint32_t source, uint32_t target) {
  assert(audio_resample_init(source, target, 2));
  assert(audio_resample_is_active());
  assert(audio_resample_get_latency_us()>0);
  int16_t input[704]={0},output[800];
  size_t total=0;
  for(int block=0;block<100;block++) {
    memset(output,0x55,sizeof(output));
    size_t n=audio_resample_process(input,352,output,400);
    assert(n>0 && n<=400);
    for(size_t i=0;i<n*2;i++) assert(output[i]==0);
    total+=n;
  }
  double expected=35200.0*target/source;
  assert(total>expected-50 && total<expected+50);
  audio_resample_reset();
  audio_resample_destroy();
}
int main(void) {
  assert(!audio_resample_init(0,44100,2));
  assert(!audio_resample_init(44100,48000,3));
  assert(audio_resample_init(44100,44100,2));
  assert(!audio_resample_is_active());
  assert(audio_resample_get_latency_us()==0);
  conversion(44100,48000);conversion(48000,44100);
  puts("PASS: rate conversion in both directions, silence, bounds, delay and reset");
}
