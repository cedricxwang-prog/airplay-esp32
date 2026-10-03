#!/usr/bin/env python3
"""Exercise the real /info handler and serializers with synthetic host inputs.

Only platform/state/response entry points are stubbed. The handler, its path
and protocol helpers, feature macros, TXT builder and plist serializers are
read from the firmware sources on every run. No device or network is accessed.
Settings persistence and actual mDNS registration have separate host tests.
"""
from pathlib import Path
import ast
import os
import plistlib
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
source = (ROOT/'main/rtsp/rtsp_handlers.c').read_text()
request_start = source.index('static bool request_uses_rtsp(')
request_end = source.index('\n}', request_start) + 2
get_start = source.index('static bool path_is(')
get_end = source.index('\nstatic void handle_post(', get_start)
functions = source[request_start:request_end] + '\n' + source[get_start:get_end]
host_tree = ast.parse((ROOT/'tests/host/run.py').read_text())
headers = next(
    ast.literal_eval(node.value)
    for node in host_tree.body
    if isinstance(node, ast.Assign)
    and any(isinstance(target, ast.Name) and target.id == 'HEADERS'
            for target in node.targets)
)
feature_header = (ROOT / 'main/rtsp/rtsp_handlers.h').read_text()
feature_section = feature_header[
    feature_header.index('// Key bits:'):feature_header.index('// Audio buffer size')
]
prefix = r'''
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <stdint.h>
#include "sdkconfig.h"
#include "esp_log.h"
#include "settings.h"
#include "plist.h"
#include "base64.h"
#include "airplay_advertisement.h"
#include "audio_stream.h"
typedef struct { int unused; } rtsp_conn_t;
typedef struct { char method[32],path[256],protocol[16]; int cseq; } rtsp_request_t;
static unsigned mode;
static FILE *output;
static const uint8_t pk[32] = {0};
const char *settings_get_airplay_model(void){return mode==2?"AirPlay-ESP32-Speaker":mode==1?"AppleTV3,2":"AudioAccessory5,1";}
const char *settings_get_airplay_manufacturer(void){return "Cedric";}
uint32_t settings_get_airplay_features_lo(void){
#ifdef CONFIG_AIRPLAY_FORCE_V1
 return 0x5C4A00;
#else
 return 0x405C4A00;
#endif
}
esp_err_t settings_get_device_name(char *name,size_t size){snprintf(name,size,"%s","1234567890123456789012345678901234567890123456789012345678901234");return 0;}
void rtsp_get_device_id(char *id,size_t size){snprintf(id,size,"%s","00:11:22:33:44:55");}
const uint8_t *hap_get_public_key(void){return pk;}
bool audio_stream_uses_buffer(audio_stream_type_t type){return type==AUDIO_STREAM_BUFFERED;}
int rtsp_send_response(int socket,rtsp_conn_t *conn,int status,const char *status_text,int cseq,const char *extra_headers,const char *body,size_t length){
 assert(status==200);assert(body);assert(length<=2048);assert(fwrite(body,1,length,output)==length);printf("binary %zu\n",length);return 0;}
int rtsp_send_http_response(int socket,rtsp_conn_t *conn,int status,const char *status_text,const char *content_type,const char *body,size_t length){
 assert(status==200);assert(body);assert(length<=4096);assert(fwrite(body,1,length,output)==length);printf("%s %zu\n",content_type,length);return 0;}
'''
main = r'''
int main(int argc,char **argv){
 assert(argc==5);mode=(unsigned)atoi(argv[1]);output=fopen(argv[4],"wb");assert(output);
 rtsp_request_t req={0};snprintf(req.path,sizeof(req.path),"%s",argv[3]);snprintf(req.protocol,sizeof(req.protocol),"%s",argv[2]);
 rtsp_conn_t conn={0};handle_get(0,&conn,&req,NULL,0);fclose(output);return 0;
}
'''

def txt_decode(data):
    pos=0; items={}
    while pos<len(data):
        length=data[pos]; pos+=1
        assert length and pos+length<=len(data)
        key,value=data[pos:pos+length].decode().split('=',1); pos+=length
        assert key not in items; items[key]=value
    return items

with tempfile.TemporaryDirectory(prefix='cedric-info-review-') as d:
    tmp=Path(d)
    for name,value in headers.items():
        target=tmp/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_text(value)
    (tmp/'probe.c').write_text(prefix + feature_section + functions + main)
    all_results=[]
    for v1 in (False,True):
        config=headers['sdkconfig.h']+ ('\n#define CONFIG_AIRPLAY_FORCE_V1 1\n' if v1 else '')
        (tmp/'sdkconfig.h').write_text(config)
        binary=tmp/'probe'
        subprocess.run([
            os.environ.get('CC', 'cc'), '-std=c11', '-I'+str(tmp),
            '-Imain', '-Imain/audio', '-Imain/network', '-Imain/plist',
            str(tmp/'probe.c'), 'main/plist/bplist_builder.c',
            'main/plist/plist_xml.c', 'main/plist/base64.c',
            'main/network/airplay_advertisement.c', '-o', str(binary),
        ], cwd=ROOT, check=True)
        for mode in range(3):
            for protocol in ('HTTP/1.1','RTSP/1.0'):
                for path in ('/info','/info?txtAirPlay&txtRAOP'):
                    target=tmp/'response'
                    result=subprocess.run([str(binary),str(mode),protocol,path,str(target)],check=True,capture_output=True,text=True)
                    payload=target.read_bytes()
                    # Query probes keep the legacy text response for every
                    # preset and transport; ordinary /info remains unchanged.
                    is_query = '?' in path
                    if is_query:
                        expected_type = 'text/parameters'
                    elif protocol == 'HTTP/1.1':
                        expected_type = 'text/x-apple-plist+xml'
                    else:
                        expected_type = 'binary'
                    response_type = result.stdout.strip().split(' ', 1)[0]
                    assert response_type == expected_type, (v1, mode, protocol, path, response_type)
                    row={'v1':v1,'mode':mode,'protocol':protocol,'path':path,'response':result.stdout.strip()}
                    if payload.startswith((b'bplist00',b'<?xml')):
                        info=plistlib.loads(payload)
                        expected_model=('AudioAccessory5,1','AppleTV3,2','AirPlay-ESP32-Speaker')[mode]
                        assert info['model']==expected_model
                        assert info['manufacturer']=='Cedric'
                        assert info['features']==(0x5C4A00 if v1 else (0x1C340<<32)|0x405C4A00)
                        assert not info['features'] & ((1<<26)|(1<<51))
                        if mode==2 and not v1:
                            ap=txt_decode(info['txtAirPlay']);raop=txt_decode(info['txtRAOP'])
                            assert ap['model']==expected_model and raop['am']==expected_model
                            assert ap['manufacturer']==raop['manufacturer']=='Cedric'
                            assert ap['features']==raop['ft']=='0x405C4A00,0x1C340'
                            assert ap['pk']==raop['pk']=='00'*32
                            assert ap['deviceid']==info.get('deviceid',info.get('deviceID'))
                            row['txt_lengths']=[len(info['txtAirPlay']),len(info['txtRAOP'])]
                        else:
                            assert 'txtAirPlay' not in info and 'txtRAOP' not in info
                    else:
                        text=payload.decode();assert 'am='+('AudioAccessory5,1','AppleTV3,2','AirPlay-ESP32-Speaker')[mode]+'\r\n' in text
                    all_results.append(row)
    generic_xml = next(row for row in all_results if not row['v1'] and row['mode'] == 2
                       and row['protocol'] == 'HTTP/1.1' and row['path'] == '/info')
    generic_binary = next(row for row in all_results if not row['v1'] and row['mode'] == 2
                          and row['protocol'] == 'RTSP/1.0' and row['path'] == '/info')
    print('PASS: actual /info handler, 24 HTTP/RTSP plain/query cases across 3 presets and v1/v2')
    print('PASS: generic XML and binary plist parse; model/manufacturer/features/TXT agree; no extra authentication bits are advertised; Apple playback requires a separate live test')
    print('Sizes: ' + generic_xml['response'] + '; ' + generic_binary['response']
          + '; DNS TXT ' + '/'.join(map(str, generic_binary['txt_lengths'])) + ' bytes')
