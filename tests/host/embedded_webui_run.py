#!/usr/bin/env python3
"""Run the real embedded-page helper and handlers with the actual HTML bytes.

Only linker spans and HTTP/app-description APIs are substituted. The source
helper and four handlers are extracted unchanged; no service implementation is
reproduced. All requests are synthetic, with no filesystem or device access by
the compiled service code.
"""
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
web = (ROOT / "main/network/web_server.c").read_text()


def function(signature):
    start = web.rindex(signature)
    return web[start:web.index("\n}", start) + 2]


PREFIX = r'''
#include <assert.h>
#include <limits.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/types.h>
typedef int esp_err_t;
typedef struct { unsigned unused; } httpd_req_t;
typedef struct { char version[32]; } esp_app_desc_t;
#define ESP_OK 0
#define HTTPD_500_INTERNAL_SERVER_ERROR 500
static esp_app_desc_t description={.version="0.2.7 by cedric"};
static unsigned calls, fail_at, headers, sends, errors;
static const char *mime, *keys[3], *values[3];
static const uint8_t *body;
static ssize_t length;
static esp_err_t error_result;
static const esp_app_desc_t *esp_app_get_description(void) { return &description; }
static esp_err_t result(void) { calls++; return calls==fail_at?(esp_err_t)(700+calls):ESP_OK; }
static esp_err_t httpd_resp_set_type(httpd_req_t *request, const char *type) {
  assert(request); mime=type; return result();
}
static esp_err_t httpd_resp_set_hdr(httpd_req_t *request, const char *key, const char *value) {
  assert(request && headers<3); keys[headers]=key; values[headers++]=value; return result();
}
static esp_err_t httpd_resp_send(httpd_req_t *request, const char *data, ssize_t size) {
  assert(request && size>0); sends++; body=(const uint8_t *)data; length=size; return result();
}
static esp_err_t httpd_resp_send_err(httpd_req_t *request, int status, const char *message) {
  assert(request && status==500 && message); errors++; return error_result;
}
static void reset(unsigned failure) {
  calls=headers=sends=errors=0; fail_at=failure; mime=NULL; body=NULL; length=0;
  memset(keys,0,sizeof(keys)); memset(values,0,sizeof(values)); error_result=ESP_OK;
}
'''

pages = []
for name, handler in (("index", "root_handler"), ("logs", "logs_page_handler"),
                      ("speedtest", "speedtest_page_handler"), ("eq", "eq_page_handler")):
    contents = (ROOT / "data/www" / (name + ".html")).read_bytes()
    assert contents and b"\0" not in contents
    contents.decode("utf-8")
    # The trailing sentinel is outside the linker span and must not be sent.
    PREFIX += "\nstatic const uint8_t " + name + "_bytes[]={" + ",".join(map(str, contents)) + ",0xA5};\n"
    PREFIX += "#define " + name + "_html_start " + name + "_bytes\n"
    PREFIX += "#define " + name + "_html_end (" + name + "_bytes+sizeof(" + name + "_bytes)-1)\n"
    pages.append("{" + handler + "," + name + "_bytes,sizeof(" + name + "_bytes)-1}")

code = PREFIX
for signature in ("static esp_err_t serve_embedded_page(",
                  "static esp_err_t root_handler(",
                  "static esp_err_t logs_page_handler(",
                  "static esp_err_t speedtest_page_handler(",
                  "static esp_err_t eq_page_handler("):
    code += "\n" + function(signature)

MAIN = r'''
static void assert_headers(void) {
  assert(headers==3 && !strcmp(mime,"text/html; charset=utf-8"));
  assert(!strcmp(keys[0],"Cache-Control") && !strcmp(values[0],"no-store"));
  assert(!strcmp(keys[1],"X-WebUI-Version") && !strcmp(values[1],description.version));
  assert(!strcmp(keys[2],"X-WebUI-Source") && !strcmp(values[2],"embedded"));
}
int main(void) {
  httpd_req_t request={0};
  const struct {
    esp_err_t (*handler)(httpd_req_t *);
    const uint8_t *bytes;
    size_t size;
  } pages[]={PAGES};
  for (size_t page=0;page<sizeof(pages)/sizeof(pages[0]);page++) {
    reset(0); assert(pages[page].handler(&request)==ESP_OK);
    assert_headers(); assert(calls==5 && sends==1 && !errors);
    assert((size_t)length==pages[page].size && body==pages[page].bytes);
    assert(!memcmp(body,pages[page].bytes,pages[page].size));
    for (unsigned failure=1;failure<=5;failure++) {
      reset(failure);
      assert(pages[page].handler(&request)==(esp_err_t)(700+failure));
      assert(calls==failure && !errors && sends==(failure==5?1u:0u));
    }
  }
  strcpy(description.version,"test-version-next");
  reset(0); assert(root_handler(&request)==ESP_OK); assert_headers();
  const uint8_t binary_utf8[]={'L',0,0xE4,0xB8,0xAD,0xA5};
  reset(0);
  assert(serve_embedded_page(&request,binary_utf8,binary_utf8+5)==ESP_OK);
  assert(length==5 && !memcmp(body,binary_utf8,5)); assert_headers();
  const uint8_t *invalid[][2]={
    {NULL,index_html_end},{index_html_start,NULL},
    {index_html_start,index_html_start},{index_html_end,index_html_start},
    {(const uint8_t *)(uintptr_t)1,(const uint8_t *)((uintptr_t)INT_MAX+2)}
  };
  for (size_t span=0;span<sizeof(invalid)/sizeof(invalid[0]);span++) {
    reset(0);
    assert(serve_embedded_page(&request,invalid[span][0],invalid[span][1])==ESP_OK);
    assert(errors==1 && !calls && !sends && !headers);
    reset(0); error_result=987;
    assert(serve_embedded_page(&request,invalid[span][0],invalid[span][1])==987);
    assert(errors==1 && !calls && !sends && !headers);
  }
  puts("PASS: actual embedded-page helper and four handlers, actual HTML bytes;");
  puts("      exact linker spans, UTF-8/binary length, no stale cache, running app version,");
  puts("      type/header/send failures propagated, invalid/oversized spans rejected;");
  puts("      ASan/UBSan, synthetic HTTP only, no SPIFFS or device I/O");
  return 0;
}
'''
code += MAIN.replace("{PAGES}", "{" + ",".join(pages) + "}")

with tempfile.TemporaryDirectory(prefix="cedric-embedded-webui-test-") as directory:
    temporary = Path(directory)
    (temporary / "probe.c").write_text(code)
    binary = temporary / "probe"
    subprocess.run([os.environ.get("CC", "cc"), "-std=c11", "-Wall", "-Wextra",
                    "-Werror", "-fsanitize=address,undefined", "-fno-omit-frame-pointer",
                    str(temporary / "probe.c"), "-o", str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
