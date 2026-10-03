#!/usr/bin/env python3
"""Run the real WiFi settings code against an in-memory NVS stub.

All SSIDs and passwords used here are synthetic fixtures. The stub deliberately
models NVS length checks so migration, boundary lengths, and corrupt blobs are
tested without reading device credentials.
"""
from pathlib import Path
import os
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
HEADERS = {
    "esp_err.h": """#pragma once
#include <stddef.h>
#include <stdint.h>
typedef int esp_err_t;
#define ESP_OK 0
#define ESP_ERR_INVALID_ARG 1
#define ESP_ERR_NOT_FOUND 2
#define ESP_ERR_NO_MEM 3
#define ESP_ERR_INVALID_SIZE 4
#define ESP_ERR_NVS_NOT_FOUND 5
#define ESP_ERR_NVS_INVALID_LENGTH 6
const char *esp_err_to_name(esp_err_t);
""",
    "esp_log.h": """#define ESP_LOGI(...) ((void)0)
#define ESP_LOGE(...) ((void)0)
#define ESP_LOGW(...) ((void)0)
""",
    "esp_mac.h": """#pragma once
#include "esp_err.h"
#define ESP_MAC_WIFI_STA 0
esp_err_t esp_read_mac(uint8_t *, int);
""",
    "dac.h": "void dac_set_volume(float);\n",
    "nvs.h": """#pragma once
#include "esp_err.h"
typedef int nvs_handle_t;
#define NVS_READONLY 0
#define NVS_READWRITE 1
esp_err_t nvs_open(const char *, int, nvs_handle_t *);
void nvs_close(nvs_handle_t);
esp_err_t nvs_commit(nvs_handle_t);
esp_err_t nvs_get_str(nvs_handle_t, const char *, char *, size_t *);
esp_err_t nvs_set_str(nvs_handle_t, const char *, const char *);
esp_err_t nvs_get_blob(nvs_handle_t, const char *, void *, size_t *);
esp_err_t nvs_set_blob(nvs_handle_t, const char *, const void *, size_t);
esp_err_t nvs_get_i32(nvs_handle_t, const char *, int32_t *);
esp_err_t nvs_set_i32(nvs_handle_t, const char *, int32_t);
esp_err_t nvs_get_u8(nvs_handle_t, const char *, uint8_t *);
esp_err_t nvs_set_u8(nvs_handle_t, const char *, uint8_t);
esp_err_t nvs_erase_key(nvs_handle_t, const char *);
""",
}
with tempfile.TemporaryDirectory(prefix="cedric-wifi-profiles-") as directory:
    temp = Path(directory)
    for name, source in HEADERS.items():
        (temp / name).write_text(source)
    binary = temp / "test-wifi-profiles"
    command = [os.environ.get("CC", "cc"), "-std=c11", "-I" + str(temp),
               "-Imain", "tests/host/test_wifi_profiles.c", "main/settings.c",
               "-o", str(binary)]
    for flags in ([], ["-DCONFIG_AIRPLAY_FORCE_V1=1"]):
        subprocess.run(command + flags, cwd=ROOT, check=True)
        subprocess.run([str(binary)], check=True)
