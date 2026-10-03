#!/usr/bin/env python3
"""Compile actual firmware timing/resampling code with minimal host stubs."""
from pathlib import Path
import os, subprocess, tempfile
ROOT = Path(__file__).resolve().parents[2]
HEADERS = {'esp_log.h': '#define ESP_LOGI(...) ((void)0)\n#define ESP_LOGE(...) ((void)0)\n#define ESP_LOGW(...) ((void)0)\n\n#define ESP_LOGD(...) ((void)0)\n', 'sdkconfig.h': '#define CONFIG_RESAMPLER_TAPS 28\n#define CONFIG_AIRPLAY_TIMING_THRESHOLD_MS 80\n#define CONFIG_AIRPLAY_RT_TIMING_THRESHOLD_MS 50\n', 'esp_err.h': 'typedef int esp_err_t;\n#define ESP_OK 0\n', 'esp_timer.h': '#include <stdint.h>\nint64_t esp_timer_get_time(void);\n', 'freertos/semphr.h': 'typedef void *SemaphoreHandle_t;\n', 'freertos/portmacro.h': 'typedef int portMUX_TYPE;\n', 'freertos/FreeRTOS.h': '#pragma once\n#include <stdint.h>\ntypedef uint32_t TickType_t;\n'}
with tempfile.TemporaryDirectory(prefix="cedric-audio-") as directory:
    temp = Path(directory)
    for name, content in HEADERS.items():
        path = temp / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    cases = {
        "resample": ["main/audio/audio_resample.c", "components/audio-resampler/resampler.c"],
        "timing": ["main/audio/audio_timing.c"],
    }
    for name, sources in cases.items():
        binary = temp / ("test-" + name)
        command = [os.environ.get("CC", "cc"), "-std=c11", "-I"+str(temp), "-Imain/audio", "-Imain/network", "-Icomponents/audio-resampler", "tests/host/test_"+name+".c", *sources, "-lm", "-o", str(binary)]
        subprocess.run(command, cwd=ROOT, check=True)
        subprocess.run([str(binary)], check=True)
