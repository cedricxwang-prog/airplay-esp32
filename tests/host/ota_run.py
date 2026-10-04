#!/usr/bin/env python3
"""Run the actual OTA receiver against synthetic HTTP, PSRAM and flash.

No device I/O is performed. SHA-256 is a known-answer subsystem stub: Python
hashlib supplies the digest for one synthetic image and the stub checks that
the receiver hashes exactly that image, excluding its appended digest.
"""
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
web = (ROOT / "main/network/web_server.c").read_text()
server = (ROOT / "main/rtsp/rtsp_server.c").read_text()


def function(source, signature):
    start = source.rindex(signature)
    return source[start:source.index("\n}", start) + 2]


image = bytearray(9000)
image[0] = 0xE9
image[1] = 1
image[23] = 1
for index in range(24, len(image)):
    image[index] = (index * 17) & 255
digest = hashlib.sha256(image).digest()

HEADERS = {
    "esp_err.h": """#pragma once
typedef int esp_err_t;
#define ESP_OK 0
#define ESP_FAIL -1
#define ESP_ERR_INVALID_ARG 1
#define ESP_ERR_INVALID_SIZE 2
#define ESP_ERR_INVALID_STATE 3
#define ESP_ERR_NO_MEM 4
#define ESP_ERR_NOT_FOUND 5
#define ESP_ERR_TIMEOUT 6
const char *esp_err_to_name(esp_err_t);
""",
    "esp_http_server.h": """#pragma once
#include <stddef.h>
#include "esp_err.h"
typedef struct { size_t content_len; } httpd_req_t;
#define HTTPD_SOCK_ERR_TIMEOUT -3
#define HTTPD_400_BAD_REQUEST 400
#define HTTPD_500_INTERNAL_SERVER_ERROR 500
int httpd_req_recv(httpd_req_t *, char *, size_t);
esp_err_t httpd_resp_send_err(httpd_req_t *, int, const char *);
esp_err_t httpd_resp_sendstr(httpd_req_t *, const char *);
""",
    "esp_app_format.h": """#pragma once
#include <stdint.h>
#define ESP_IMAGE_HEADER_MAGIC 0xE9
#define ESP_IMAGE_MAX_SEGMENTS 16
/* The only fields read by ota.c, with the real 24-byte wire offsets. */
typedef struct { uint8_t magic, segment_count, other[21], hash_appended; }
    esp_image_header_t;
""",
    "esp_heap_caps.h": """#pragma once
#include <stddef.h>
#define MALLOC_CAP_SPIRAM 1
void *heap_caps_malloc(size_t, unsigned);
void heap_caps_free(void *);
""",
    "esp_log.h": """#define ESP_LOGI(tag, ...) ((void)(tag))
#define ESP_LOGW(tag, ...) ((void)(tag))
#define ESP_LOGE(tag, ...) ((void)(tag))
""",
    "esp_ota_ops.h": """#pragma once
#include <stdint.h>
#include <stddef.h>
#include "esp_err.h"
typedef struct { uint32_t size; } esp_partition_t;
typedef unsigned esp_ota_handle_t;
#define OTA_SIZE_UNKNOWN ((size_t)-1)
const esp_partition_t *esp_ota_get_next_update_partition(const void *);
esp_err_t esp_ota_begin(const esp_partition_t *, size_t, esp_ota_handle_t *);
esp_err_t esp_ota_write(esp_ota_handle_t, const void *, size_t);
esp_err_t esp_ota_abort(esp_ota_handle_t);
esp_err_t esp_ota_end(esp_ota_handle_t);
esp_err_t esp_ota_set_boot_partition(const esp_partition_t *);
""",
    "mbedtls/sha256.h": """#pragma once
#include <stddef.h>
int mbedtls_sha256(const unsigned char *, size_t, unsigned char *, int);
""",
}

SOURCE = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "ota.h"
#include "esp_heap_caps.h"
#include "esp_ota_ops.h"
#include "esp_log.h"
#define pdMS_TO_TICKS(value) (value)
#define pdPASS 1
#define SERVER_STACK_SIZE 8192
typedef void *TaskHandle_t;
typedef void *SemaphoreHandle_t;
typedef int BaseType_t;
static const char *TAG="ota-handler-test";
static bool server_running;
static TaskHandle_t server_task_handle;
static SemaphoreHandle_t session_state_mutex;
static struct { TaskHandle_t task; unsigned marker; } clients[2];
static unsigned waits, workers_exit_after, server_exit_after, mutex_creates;
static unsigned listener_stops, listener_starts, responses, reboots;
static int response_status;
static esp_err_t listener_start_result;
static const unsigned char valid_data[] = {DATA};
static const unsigned char known_hash[] = {DIGEST};
static unsigned char uploaded[sizeof(valid_data)+32], flashed[sizeof(uploaded)];
static esp_partition_t partition;
static bool have_partition, no_psram, handle_open;
static unsigned allocations, frees, reads, begins, writes, aborts, ends, boots, hashes;
static size_t position, flash_length, alloc_length;
static esp_err_t begin_result, end_result, boot_result;
static unsigned fail_write;
static int schedule[40];
static size_t schedule_length, schedule_pos;
static void reset(void) {
  assert(!handle_open && allocations==frees);
  memcpy(uploaded,valid_data,sizeof(valid_data));
  memcpy(uploaded+sizeof(valid_data),known_hash,32);
  memset(flashed,0,sizeof(flashed));
  partition.size=sizeof(uploaded); have_partition=true; no_psram=false;
  allocations=frees=reads=begins=writes=aborts=ends=boots=hashes=0;
  position=flash_length=alloc_length=schedule_length=schedule_pos=0;
  begin_result=end_result=boot_result=ESP_OK; fail_write=0;
  server_running=false; listener_stops=listener_starts=responses=reboots=0;
  response_status=0; listener_start_result=ESP_OK;
  server_task_handle=NULL; session_state_mutex=NULL;
  memset(clients,0,sizeof(clients));
  waits=workers_exit_after=server_exit_after=mutex_creates=0;
}
const char *esp_err_to_name(esp_err_t error) { (void)error; return "synthetic"; }
void *heap_caps_malloc(size_t length, unsigned caps) {
  assert(caps==MALLOC_CAP_SPIRAM); alloc_length=length;
  if (no_psram) return NULL;
  allocations++; return malloc(length);
}
void heap_caps_free(void *buffer) { assert(buffer); frees++; free(buffer); }
const esp_partition_t *esp_ota_get_next_update_partition(const void *unused) {
  assert(!unused); return have_partition?&partition:NULL;
}
int httpd_req_recv(httpd_req_t *request, char *buffer, size_t capacity) {
  reads++; assert(position<=request->content_len && capacity<=request->content_len-position);
  int event=schedule_pos<schedule_length?schedule[schedule_pos++]:4096;
  if (event<=0) return event;
  size_t length=(size_t)event<capacity?(size_t)event:capacity;
  assert(position+length<=sizeof(uploaded));
  memcpy(buffer,uploaded+position,length); position+=length; return (int)length;
}
int mbedtls_sha256(const unsigned char *data, size_t length, unsigned char *hash, int is224) {
  assert(!is224 && length==sizeof(valid_data));
  assert(memcmp(data,valid_data,length)==0); hashes++; memcpy(hash,known_hash,32); return 0;
}
esp_err_t esp_ota_begin(const esp_partition_t *selected, size_t length, esp_ota_handle_t *handle) {
  assert(selected==&partition && !handle_open); begins++;
  if (begin_result!=ESP_OK) return begin_result;
  assert(length==OTA_SIZE_UNKNOWN || length<=partition.size);
  handle_open=true; *handle=7; return ESP_OK;
}
esp_err_t esp_ota_write(esp_ota_handle_t handle, const void *data, size_t length) {
  assert(handle_open && handle==7 && flash_length+length<=partition.size); writes++;
  if (writes==fail_write) return ESP_FAIL;
  memcpy(flashed+flash_length,data,length); flash_length+=length; return ESP_OK;
}
esp_err_t esp_ota_abort(esp_ota_handle_t handle) {
  assert(handle_open && handle==7); aborts++; handle_open=false; return ESP_OK;
}
esp_err_t esp_ota_end(esp_ota_handle_t handle) {
  assert(handle_open && handle==7); ends++; handle_open=false; return end_result;
}
esp_err_t esp_ota_set_boot_partition(const esp_partition_t *selected) {
  assert(selected==&partition && !handle_open && ends==1 && end_result==ESP_OK);
  boots++; return boot_result;
}
/* Use the actual accessor too; only task startup/shutdown is simulated. */
ACCESSOR
static void rtsp_server_stop(void) {
  assert(server_running); listener_stops++; server_running=false;
  server_task_handle=NULL;
}
static void server_task(void *parameters) { (void)parameters; assert(false); }
static SemaphoreHandle_t xSemaphoreCreateMutex(void) {
  assert(!clients[0].task && !clients[1].task); mutex_creates++; return (void *)(uintptr_t)1;
}
static int xTaskCreate(void (*task)(void *), const char *name, unsigned stack,
                       void *parameters, unsigned priority, TaskHandle_t *handle) {
  assert(task==server_task && !strcmp(name,"rtsp_server") && stack==SERVER_STACK_SIZE);
  assert(!parameters && priority==5 && handle==&server_task_handle);
  assert(!clients[0].task && !clients[1].task && !server_task_handle);
  listener_starts++;
  if (listener_start_result!=ESP_OK) return 0;
  server_task_handle=(void *)(uintptr_t)3; server_running=true; return pdPASS;
}
esp_err_t httpd_resp_send_err(httpd_req_t *request, int status, const char *message) {
  assert(request && message && !handle_open); response_status=status; responses++; return ESP_OK;
}
esp_err_t httpd_resp_sendstr(httpd_req_t *request, const char *message) {
  assert(request && message && boots==1 && boot_result==ESP_OK);
  response_status=200; responses++; return ESP_OK;
}
static void vTaskDelay(unsigned ticks) {
  if (ticks==500) { assert(responses==1 && response_status==200); return; }
  assert(ticks==50); waits++;
  if (workers_exit_after && waits==workers_exit_after) clients[0].task=clients[1].task=NULL;
  if (server_exit_after && waits==server_exit_after) server_task_handle=NULL;
}
static void esp_restart(void) { assert(responses==1 && response_status==200); reboots++; }
START_IMPLEMENTATION
HANDLER
static void events(const int *values, size_t count) {
  assert(count<=sizeof(schedule)/sizeof(schedule[0]));
  memcpy(schedule,values,count*sizeof(*values)); schedule_length=count;
}
int main(void) {
  httpd_req_t request={.content_len=sizeof(uploaded)};
  reset(); assert(ota_start_from_http(NULL)==ESP_ERR_INVALID_ARG);
  request.content_len=0; assert(ota_start_from_http(&request)==ESP_ERR_INVALID_SIZE);
  assert(!allocations && !reads && !begins && !boots);
  reset(); request.content_len=sizeof(uploaded)+1;
  assert(ota_start_from_http(&request)==ESP_ERR_INVALID_SIZE);
  assert(!allocations && !reads && !begins && !boots);
  reset(); request.content_len=sizeof(uploaded); have_partition=false;
  assert(ota_start_from_http(&request)==ESP_ERR_NOT_FOUND);
  assert(!allocations && !reads && !begins && !boots);

  reset(); assert(ota_start_from_http(&request)==ESP_OK);
  assert(alloc_length==partition.size && allocations==1 && frees==1);
  assert(hashes==1 && begins==1 && writes==3 && ends==1 && boots==1 && !aborts);
  assert(flash_length==request.content_len && !memcmp(flashed,uploaded,flash_length));

  reset(); request.content_len=23;
  assert(ota_start_from_http(&request)==ESP_ERR_INVALID_SIZE && !begins && !boots);
  request.content_len=sizeof(uploaded);
  reset(); uploaded[0]=0;
  assert(ota_start_from_http(&request)==ESP_ERR_INVALID_STATE && !begins && !boots);
  for (int count=0;count<=17;count+=17) {
    reset(); uploaded[1]=(unsigned char)count;
    assert(ota_start_from_http(&request)==ESP_ERR_INVALID_STATE && !begins && !boots);
  }
  reset(); uploaded[sizeof(uploaded)-1]^=1;
  assert(ota_start_from_http(&request)==ESP_ERR_INVALID_STATE);
  assert(hashes==1 && !begins && !writes && !boots);

  const int stalled[]={HTTPD_SOCK_ERR_TIMEOUT,HTTPD_SOCK_ERR_TIMEOUT,HTTPD_SOCK_ERR_TIMEOUT,4096};
  reset(); events(stalled,4);
  assert(ota_start_from_http(&request)==ESP_ERR_TIMEOUT);
  assert(reads==3 && !begins && frees==1 && !boots);
  const int progress[]={HTTPD_SOCK_ERR_TIMEOUT,HTTPD_SOCK_ERR_TIMEOUT,1000,
    HTTPD_SOCK_ERR_TIMEOUT,HTTPD_SOCK_ERR_TIMEOUT,1000,
    HTTPD_SOCK_ERR_TIMEOUT,HTTPD_SOCK_ERR_TIMEOUT,1000};
  reset(); events(progress,9); assert(ota_start_from_http(&request)==ESP_OK);
  assert(boots==1 && reads>9);
  reset(); const int disconnected[]={0}; events(disconnected,1);
  assert(ota_start_from_http(&request)==ESP_FAIL && !begins && !boots && frees==1);

  for (int streaming=0;streaming<2;streaming++) {
    reset(); no_psram=(bool)streaming; begin_result=ESP_FAIL;
    assert(ota_start_from_http(&request)==ESP_FAIL && begins==1 && !writes && !boots);
    reset(); no_psram=(bool)streaming; fail_write=2;
    assert(ota_start_from_http(&request)==ESP_FAIL && aborts==1 && !ends && !boots);
    reset(); no_psram=(bool)streaming; end_result=ESP_FAIL;
    assert(ota_start_from_http(&request)==ESP_FAIL && ends==1 && !boots && !handle_open);
    reset(); no_psram=(bool)streaming; boot_result=ESP_FAIL;
    assert(ota_start_from_http(&request)==ESP_FAIL && boots==1 && !handle_open);
  }
  reset(); begin_result=ESP_ERR_NO_MEM;
  assert(ota_start_from_http(&request)==ESP_ERR_NO_MEM);
  assert(position==request.content_len && begins==1 && !writes && !boots && frees==1);

  reset(); no_psram=true; assert(ota_start_from_http(&request)==ESP_OK);
  assert(!allocations && !hashes && begins==1 && ends==1 && boots==1);
  assert(flash_length==request.content_len && !memcmp(flashed,uploaded,flash_length));
  reset(); no_psram=true; events(stalled,4);
  assert(ota_start_from_http(&request)==ESP_ERR_TIMEOUT);
  assert(reads==3 && begins==1 && aborts==1 && !writes && !boots);
  reset(); no_psram=true; events(progress,9);
  assert(ota_start_from_http(&request)==ESP_OK && boots==1 && reads>9);
  reset(); no_psram=true; events(disconnected,1);
  assert(ota_start_from_http(&request)==ESP_FAIL && aborts==1 && !boots);

  reset(); request.content_len=0; server_running=true;
  assert(ota_update_handler(&request)==ESP_FAIL);
  assert(response_status==400 && !listener_stops && !listener_starts && server_running && !reboots);
  request.content_len=sizeof(uploaded);
  for (int previously_running=0;previously_running<2;previously_running++) {
    reset(); server_running=(bool)previously_running; uploaded[0]=0;
    assert(ota_update_handler(&request)==ESP_FAIL);
    assert(response_status==500 && listener_stops==(unsigned)previously_running);
    assert(listener_starts==(unsigned)previously_running && server_running==(bool)previously_running);
    assert(!reboots && !boots && !begins);
    reset(); server_running=(bool)previously_running;
    assert(ota_update_handler(&request)==ESP_OK);
    assert(listener_stops==(unsigned)previously_running && !listener_starts && reboots==1);
    assert(boots==1 && response_status==200);
  }
  reset(); server_running=true; events(stalled,4);
  assert(ota_update_handler(&request)==ESP_FAIL && response_status==500);
  assert(server_running && listener_starts==1 && listener_stops==1 && !boots && !reboots);
  reset(); server_running=true; uploaded[0]=0; listener_start_result=ESP_FAIL;
  assert(ota_update_handler(&request)==ESP_FAIL && response_status==500);
  assert(!server_running && listener_starts==1 && listener_stops==1 && !boots && !reboots);

  reset(); assert(rtsp_server_start()==ESP_OK);
  assert(!waits && mutex_creates==1 && listener_starts==1 && server_running);
  assert(rtsp_server_start()==ESP_ERR_INVALID_STATE && listener_starts==1 && !waits);
  for (int busy_slot=0;busy_slot<2;busy_slot++) {
    reset(); clients[busy_slot].task=(void *)(uintptr_t)1; clients[busy_slot].marker=123;
    assert(rtsp_server_start()==ESP_ERR_INVALID_STATE);
    assert(waits==100 && !mutex_creates && !listener_starts && !server_task_handle);
    assert(clients[busy_slot].task==(void *)(uintptr_t)1 && clients[busy_slot].marker==123);
  }
  reset(); clients[0].task=(void *)(uintptr_t)1; clients[1].task=(void *)(uintptr_t)2;
  workers_exit_after=3; assert(rtsp_server_start()==ESP_OK);
  assert(waits==3 && mutex_creates==1 && listener_starts==1 && server_running);
  reset(); server_task_handle=(void *)(uintptr_t)1;
  server_exit_after=2; clients[0].task=(void *)(uintptr_t)2; workers_exit_after=5;
  assert(rtsp_server_start()==ESP_OK);
  assert(waits==5 && mutex_creates==1 && listener_starts==1 && server_running);
  reset(); server_task_handle=(void *)(uintptr_t)1;
  assert(rtsp_server_start()==ESP_ERR_INVALID_STATE && waits==40);
  assert(!listener_starts && !mutex_creates && server_task_handle==(void *)(uintptr_t)1);
  reset(); server_running=true; clients[1].task=(void *)(uintptr_t)1; uploaded[0]=0;
  assert(ota_update_handler(&request)==ESP_FAIL && response_status==500);
  assert(waits==100 && listener_stops==1 && !listener_starts && !server_running && !boots && !reboots);
  reset();
  puts("PASS: actual ota.c; capacity/empty/missing-partition checks before I/O,");
  puts("      buffered prevalidation, exact-size upload, 3-timeout bound and progress,");
  puts("      streaming fallback, receive/flash/end/boot failures, no consumed-body retry;");
  puts("      actual web OTA handler restores only the previous listener, response before reboot;");
  puts("      actual RTSP start waits for retired workers, refuses live slots, fresh start without delay;");
  puts("      ASan/UBSan, synthetic HTTP/flash only");
  return 0;
}
'''
SOURCE = SOURCE.replace("{DATA}", "{" + ",".join(map(str, image)) + "}")
SOURCE = SOURCE.replace("{DIGEST}", "{" + ",".join(map(str, digest)) + "}")
SOURCE = SOURCE.replace("ACCESSOR", function(server, "bool rtsp_server_is_running("))
SOURCE = SOURCE.replace("START_IMPLEMENTATION", "\n".join(function(server, signature) for signature in (
    "static bool rtsp_server_wait_for_task_stopped(",
    "static bool rtsp_server_wait_for_clients_stopped(", "esp_err_t rtsp_server_start(")))
SOURCE = SOURCE.replace("HANDLER", function(web, "static esp_err_t ota_update_handler("))

with tempfile.TemporaryDirectory(prefix="cedric-ota-test-") as directory:
    temporary = Path(directory)
    for name, contents in HEADERS.items():
        path = temporary / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents)
    (temporary / "probe.c").write_text(SOURCE)
    binary = temporary / "probe"
    subprocess.run([os.environ.get("CC", "cc"), "-std=c11", "-Wall", "-Wextra",
                    "-Werror", "-fsanitize=address,undefined", "-fno-omit-frame-pointer",
                    "-I" + str(temporary), "-Imain/network",
                    str(temporary / "probe.c"), "main/network/ota.c",
                    "-o", str(binary)], cwd=ROOT, check=True)
    subprocess.run([str(binary)], check=True)
