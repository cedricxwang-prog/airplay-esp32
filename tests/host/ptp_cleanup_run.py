#!/usr/bin/env python3
"""Run real RTSP lifecycle code with synthetic sockets and shared clock state.

The connection implementation is compiled unchanged. Server request processing,
client_task, ownership helpers and TEARDOWN are extracted from source, rather
than reproduced. Only platform I/O, scheduling and other subsystem entry points
are stubbed. No network, device, account or saved setting is accessed.
"""
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
server = (ROOT / "main/rtsp/rtsp_server.c").read_text()
handlers = (ROOT / "main/rtsp/rtsp_handlers.c").read_text()
message = (ROOT / "main/rtsp/rtsp_message.c").read_text()


def function(source, signature):
    start = source.rindex(signature)  # skip forward declarations
    return source[start:source.index("\n}", start) + 2]


PREFIX = r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <unistd.h>
#include "esp_log.h"
#include "settings.h"
#include "rtsp_conn.h"
#include "rtsp_message.h"
typedef void *TaskHandle_t;
typedef void *SemaphoreHandle_t;
#define portMAX_DELAY 0
#define pdMS_TO_TICKS(value) (value)
#define MALLOC_CAP_SPIRAM 0
#define MALLOC_CAP_8BIT 0
#define RTSP_BUFFER_INITIAL 4096
#define RTSP_BUFFER_LARGE ((size_t)256 * 1024)
enum { RTSP_EVENT_PAUSED, RTSP_EVENT_DISCONNECTED };
static bool server_running;
static unsigned mutex_depth, clears, persists, stops, flushes, ntp_stops;
static unsigned disconnects, pauses, event_stops, closes, dispatches, deletions;
static unsigned clock_state, volume_state = 12, hap_frees;
static int listener = -1;
static bool legacy, install_listener = true, streams, expect_retirement, ptp_running, fail_send_timeout;
static const char *packet;
static unsigned reads;
static void (*eof_hook)(void), (*delay_hook)(void);
static volatile bool s_resume_requested;
static int xSemaphoreTake(SemaphoreHandle_t mutex, unsigned timeout) {
  (void)timeout; assert(mutex && mutex_depth == 0); mutex_depth++; return 1;
}
static int xSemaphoreGive(SemaphoreHandle_t mutex) {
  assert(mutex && mutex_depth == 1); mutex_depth--; return 1;
}
static void vTaskDelete(void *task) { (void)task; deletions++; }
static void vTaskDelay(unsigned ticks) {
  (void)ticks; assert(mutex_depth == 0);
  if (delay_hook) { void (*hook)(void)=delay_hook; delay_hook=NULL; hook(); }
}
void ptp_clock_clear(void) { assert(mutex_depth == 1); clears++; clock_state=0; }
static int ptp_clock_init(void) { assert(mutex_depth == 1); ptp_running=true; return 0; }
static void audio_receiver_stop(void) { assert(mutex_depth == 1); stops++; }
static void audio_output_flush(void) { assert(mutex_depth == 1); flushes++; }
static void ntp_clock_stop(void) { assert(mutex_depth == 1); ntp_stops++; }
static void audio_receiver_set_playing(bool playing) { assert(!playing); }
static void dacp_clear_session(void) { assert(mutex_depth == 1); }
static bool dacp_probe_service(void) { return false; }
static void rtsp_events_emit(int event, void *data) {
  (void)data; assert(mutex_depth == 1);
  if (event == RTSP_EVENT_DISCONNECTED) disconnects++; else pauses++;
}
static int rtsp_event_port_listen_socket(void) { return listener; }
static void rtsp_stop_event_port_task(void) {
  assert(mutex_depth == 1); event_stops++; listener=-1;
}
esp_err_t settings_get_volume(float *volume) { *volume=-12; return 0; }
esp_err_t settings_set_volume(float volume) { (void)volume; volume_state++; return 0; }
esp_err_t settings_persist_volume(void) { assert(mutex_depth == 1); persists++; return 0; }
void hap_session_free(hap_session_t *session) { hap_frees++; free(session); }
int fake_close(int socket) {
  assert(socket >= 0 && socket != listener); closes++; return 0;
}
int fake_getpeername(int socket, struct sockaddr *address, socklen_t *length) {
  (void)socket; (void)address; (void)length; return -1;
}
int fake_setsockopt(int socket, int level, int option, const void *value, socklen_t length) {
  (void)socket; (void)level;
  if (option==SO_SNDTIMEO) {
    const struct timeval *timeout=value;
    assert(length==sizeof(*timeout) && timeout->tv_sec==5 && timeout->tv_usec==0);
    if (fail_send_timeout) { errno=ENOPROTOOPT; return -1; }
  }
  return 0;
}
int fake_shutdown(int socket, int how) { (void)socket; (void)how; return 0; }
ssize_t fake_recv(int socket, void *buffer, size_t capacity, int flags) {
  (void)socket; (void)flags;
  if (reads++ == 0 && packet) {
    size_t length=strlen(packet); assert(length < capacity); memcpy(buffer,packet,length); return (ssize_t)length;
  }
  if (eof_hook) { void (*hook)(void)=eof_hook; eof_hook=NULL; hook(); }
  return 0;
}
static void *heap_caps_malloc(size_t size, unsigned caps) { (void)caps; return malloc(size); }
static int rtsp_crypto_read_block(int socket, rtsp_conn_t *conn, uint8_t *buffer, size_t capacity) {
  (void)socket; (void)conn; (void)buffer; (void)capacity; return -1;
}
static bool bplist_get_streams_count(const uint8_t *body, size_t length, size_t *count) {
  (void)body; (void)length; *count=streams?1:0; return streams;
}
int rtsp_send_ok(int socket, rtsp_conn_t *conn, int cseq) {
  (void)socket; (void)conn; (void)cseq; return 0;
}
static int rtsp_dispatch(int socket, rtsp_conn_t *conn, const uint8_t *raw, size_t length);
static void signal_old_client_stop(int old_slot);
'''

MAIN = r'''
static const char *get="GET /info?txtAirPlay&txtRAOP RTSP/1.0\r\nCSeq: 1\r\n\r\n";
static const char *setup="SETUP rtsp://receiver/session RTSP/1.0\r\nCSeq: 2\r\n\r\n";
static const char *pair="POST /pair-setup RTSP/1.0\r\nCSeq: 1\r\n\r\n";
static int rtsp_dispatch(int socket, rtsp_conn_t *conn, const uint8_t *raw, size_t length) {
  assert(mutex_depth == 1); dispatches++;
  rtsp_request_t req; assert(rtsp_request_parse(raw,length,&req)==0);
  if (strcmp(req.method,"SETUP")==0) {
    if (expect_retirement) {
      assert(stops==1 && flushes==1 && ntp_stops==1 && clears==1 && persists==1);
      assert(ptp_running && clock_state==0 && clients[0].shared_state_retired);
    }
    clock_state=(unsigned)socket; conn->event_socket=socket+100;
    if (install_listener) listener=conn->event_socket;
    conn->protocol_version=legacy?1:2;
    if (legacy) { strcpy(conn->dacp_id,"test"); strcpy(conn->active_remote,"test"); ptp_running=false; }
  } else if (strcmp(req.method,"TEARDOWN")==0) {
    handle_teardown(socket,conn,&req,raw,length);
  }
  return 0;
}
static void reset(void) {
  assert(mutex_depth == 0);
  listener=-1;
  for (int i=0;i<2;i++) if (clients[i].conn) rtsp_conn_free(clients[i].conn);
  memset(clients,0,sizeof(clients));
  for (int i=0;i<2;i++) { clients[i].socket=100+i; clients[i].takeover_slot=-1; }
  current_slot=0; session_state_mutex=(void *)(uintptr_t)1; server_running=true;
  clears=persists=stops=flushes=ntp_stops=disconnects=pauses=event_stops=closes=dispatches=deletions=reads=0;
  clock_state=777; legacy=false; install_listener=true; streams=false; expect_retirement=false; ptp_running=true;
  fail_send_timeout=false;
  packet=NULL; eof_hook=delay_hook=NULL;
}
static void process(client_slot_t *slot, const char *request) {
  uint8_t buffer[512]; size_t length=strlen(request);
  assert(length<sizeof(buffer)); memcpy(buffer,request,length); buffer[length]=0;
  process_rtsp_buffer(slot,buffer,&length); assert(length==0);
}
static void prepare_new(void) {
  clients[1].conn=rtsp_conn_create(); assert(clients[1].conn);
  clients[1].task=(void *)(uintptr_t)2; clients[1].takeover_slot=0;
}
static void takeover_setup(void) { prepare_new(); legacy=false; expect_retirement=true; process(&clients[1],setup); }
static void mark_old_with_matching_listener(void) { signal_old_client_stop(0); clock_state=101; }
static void shutdown_owner(void) { server_running=false; clients[0].should_stop=true; }
int main(void) {
  reset();
  rtsp_conn_t *private_conn=rtsp_conn_create(); assert(private_conn);
  private_conn->data_socket=171; private_conn->control_socket=172; private_conn->event_socket=173;
  private_conn->hap_session=malloc(sizeof(hap_session_t)); assert(private_conn->hap_session);
  rtsp_conn_cleanup(private_conn); rtsp_conn_cleanup(private_conn); rtsp_conn_free(private_conn);
  assert(clears==0 && persists==0 && clock_state==777 && closes==3 && hap_frees==1);

  reset(); clients[0].conn=rtsp_conn_create(); clients[0].task=(void *)(uintptr_t)1;
  clients[0].conn->encrypted_mode=true; clients[0].conn->stream_active=true;
  clients[0].conn->event_socket=200; listener=200;
  clients[0].conn->hap_session=malloc(sizeof(hap_session_t)); assert(clients[0].conn->hap_session);
  unsigned volume_before=volume_state, frees_before=hap_frees;
  clients[0].session_started=true; clients[1].takeover_slot=0; clients[1].task=(void *)(uintptr_t)2;
  packet=get; client_task((void *)(uintptr_t)1);
  assert(clears==0 && persists==0 && stops==0 && clock_state==777 && current_slot==0);
  assert(!clients[0].is_old && clients[0].session_started && clients[1].task==NULL);
  assert(clients[0].conn->encrypted_mode && clients[0].conn->stream_active);
  assert(clients[0].conn->hap_session && hap_frees==frees_before);
  assert(listener==200 && event_stops==0 && volume_state==volume_before);

  reset(); clients[0].conn=rtsp_conn_create(); clients[0].task=(void *)(uintptr_t)1;
  clients[0].session_started=true; clients[1].takeover_slot=0; clients[1].task=(void *)(uintptr_t)2;
  fail_send_timeout=true; packet=get; client_task((void *)(uintptr_t)1);
  assert(dispatches==0 && clears==0 && persists==0 && clock_state==777 && current_slot==0);

  reset(); clients[0].task=(void *)(uintptr_t)1; packet=setup; client_task(NULL);
  assert(clears==1 && persists==1 && stops==1 && ntp_stops==1 && disconnects==1);
  assert(clock_state==0 && event_stops==1 && clients[0].task==NULL);

  reset(); clients[0].task=(void *)(uintptr_t)1; packet=setup; eof_hook=shutdown_owner; client_task(NULL);
  assert(clears==1 && persists==1 && clock_state==0 && disconnects==1);

  reset(); clients[0].task=(void *)(uintptr_t)1; packet=setup; eof_hook=takeover_setup; client_task(NULL);
  assert(clears==1 && persists==1 && stops==1 && disconnects==1 && clock_state==101);
  assert(event_stops==1 && listener==201 && current_slot==1 && clients[1].session_started);

  reset(); clients[0].conn=rtsp_conn_create(); clients[0].task=(void *)(uintptr_t)1;
  clients[0].session_started=true; clients[1].takeover_slot=0; clients[1].task=(void *)(uintptr_t)2;
  packet=pair; client_task((void *)(uintptr_t)1);
  assert(clears==0 && persists==0 && stops==0 && clock_state==777);
  assert(!clients[0].is_old && current_slot==0 && event_stops==0);

  reset(); clients[0].task=(void *)(uintptr_t)1; packet=setup; eof_hook=mark_old_with_matching_listener; client_task(NULL);
  assert(clears==0 && persists==0 && stops==0 && clock_state==101);
  assert(event_stops==1 && listener==-1);

  reset(); clients[0].task=(void *)(uintptr_t)1; packet=setup; legacy=true; delay_hook=takeover_setup; client_task(NULL);
  assert(clears==1 && persists==1 && clock_state==101 && pauses==1 && disconnects==1);
  assert(event_stops==1 && listener==201 && clients[1].session_started);

  reset(); clients[0].task=(void *)(uintptr_t)1; clients[0].session_started=true;
  prepare_new(); uint8_t fragmented[512]="SETUP rtsp://receiver/session RTSP/1.0\r\nContent-Length: 4\r\n\r\nab";
  size_t length=strlen((char *)fragmented); process_rtsp_buffer(&clients[1],fragmented,&length);
  assert(!clients[0].is_old && !clients[1].session_started && dispatches==0 && current_slot==0);
  memcpy(fragmented+length,"cd",2); length+=2; fragmented[length]=0;
  process_rtsp_buffer(&clients[1],fragmented,&length);
  assert(length==0 && clients[0].is_old && clients[1].session_started && current_slot==1);

  for (int with_stream=0;with_stream<2;with_stream++) {
    reset(); clients[0].conn=rtsp_conn_create(); clients[0].task=(void *)(uintptr_t)1;
    clients[0].session_started=true; clients[0].conn->protocol_version=2; streams=with_stream;
    const char *request=with_stream?"TEARDOWN rtsp://receiver/session RTSP/1.0\r\nContent-Length: 8\r\n\r\nbplist00":"TEARDOWN rtsp://receiver/session RTSP/1.0\r\nCSeq: 3\r\n\r\n";
    process(&clients[0],request);
    assert(clears==1 && clock_state==0 && stops==1 && flushes==1 && persists==0);
    assert(clients[0].conn->stream_paused==(bool)with_stream && pauses==(unsigned)with_stream);
  }
  reset(); assert(mutex_depth==0);
  puts("PASS: actual connection/client_task/request processing and TEARDOWN;");
  puts("      probes/pairing, repeated private cleanup, EOF/shutdown, SETUP takeover,");
  puts("      v1 grace reconnect, old/new event listeners, fragmented SETUP;");
  puts("      owner-only PTP clear/volume write, ASan/UBSan, no device I/O");
  return 0;
}
'''

declarations = server[server.index("// Client slot for tracking connections"):
                      server.index("// Forward declaration:")]
code = PREFIX + declarations
for signature in ("static bool request_takes_over(",
                  "static bool request_starts_session(",
                  "static uint8_t *grow_buffer(",
                  "static void signal_old_client_stop("):
    code += "\n" + function(server, signature)
for signature in ("const uint8_t *rtsp_find_header_end(",
                  "int rtsp_parse_cseq(", "int rtsp_parse_content_length(",
                  "const uint8_t *rtsp_get_body(",
                  "int rtsp_request_parse("):
    code += "\n" + function(message, signature)
code += "\n" + function(handlers, "static void handle_teardown(")
code += "\n" + function(server, "static void process_rtsp_buffer(")
code += "\n" + function(server, "static void client_task(") + MAIN

with tempfile.TemporaryDirectory(prefix="cedric-ptp-cleanup-") as directory:
    tmp = Path(directory)
    (tmp / "esp_log.h").write_text("#define ESP_LOGI(...) ((void)0)\n#define ESP_LOGW(...) ((void)0)\n#define ESP_LOGE(...) ((void)0)\n#define ESP_LOGD(...) ((void)0)\n")
    (tmp / "hap.h").write_text("#pragma once\ntypedef struct { int value; } hap_session_t;\nvoid hap_session_free(hap_session_t *);\n")
    (tmp / "settings.h").write_text("#pragma once\ntypedef int esp_err_t;\n#define ESP_OK 0\nesp_err_t settings_get_volume(float *);\nesp_err_t settings_set_volume(float);\nesp_err_t settings_persist_volume(void);\n")
    (tmp / "probe.c").write_text(code)
    binary = tmp / "probe"
    command = [os.environ.get("CC", "cc"), "-std=c11", "-Wall", "-Wextra",
               "-Werror", "-fsanitize=address,undefined", "-fno-omit-frame-pointer",
               "-I" + str(tmp), "-Imain/rtsp"]
    command += ["-D" + name + "=fake_" + name for name in
                ("close", "getpeername", "setsockopt", "shutdown", "recv")]
    command += [str(tmp / "probe.c"), "main/rtsp/rtsp_conn.c", "-o", str(binary)]
    subprocess.run(command, cwd=ROOT, check=True)
    subprocess.run([str(binary)], check=True)
