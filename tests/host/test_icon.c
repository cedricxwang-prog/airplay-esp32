#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "plist.h"
#include "audio_stream.h"
bool audio_stream_uses_buffer(audio_stream_type_t type){return type==AUDIO_STREAM_BUFFERED;}
static const char *model;
const char *settings_get_airplay_model(void){return model;}
int main(int argc,char **argv){
 assert(argc==3);model=argv[1];uint8_t out[1024],pk[32]={0};
 size_t size=bplist_build_info_response(out,sizeof(out),"00:11:22:33:44:55","Test receiver",pk,sizeof(pk),0,2);
 assert(size>0 && size<=sizeof(out));
 FILE *file=fopen(argv[2],"wb");assert(file);assert(fwrite(out,1,size,file)==size);fclose(file);
 assert(bplist_build_info_response(out,16,"00:11:22:33:44:55","Test receiver",pk,sizeof(pk),0,2)==0);
}
