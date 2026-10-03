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
    # Compile the actual mDNS source and shared TXT serializer with only the
    # platform entry points stubbed. Feature macros come from the real header.
    feature_header = (ROOT / "main/rtsp/rtsp_handlers.h").read_text()
    feature_section = feature_header[feature_header.index("// Key bits:"):feature_header.index("// Audio buffer size")]
    stubs = {
        "rtsp_handlers.h": '#include <stdint.h>\n#include "settings.h"\n' + feature_section,
        "esp_mac.h": '#include <stdint.h>\n#define ESP_MAC_WIFI_STA 0\nvoid esp_read_mac(uint8_t *, int);\n',
        "esp_wifi_types.h": 'typedef struct { unsigned char placeholder; } wifi_ap_record_t;\n',
        "hap.h": '#include <stdint.h>\nconst uint8_t *hap_get_public_key(void);\n',
        "mdns.h": '#include <stddef.h>\n#include <stdint.h>\n#include "esp_err.h"\ntypedef struct {const char *key;const char *value;} mdns_txt_item_t;\nesp_err_t mdns_init(void);\nesp_err_t mdns_hostname_set(const char *);\nesp_err_t mdns_service_add(const char *, const char *, const char *, uint16_t, mdns_txt_item_t *, size_t);\n',
    }
    for name, content in stubs.items():
        (temp / name).write_text(content)
    with (temp / "esp_log.h").open("a") as file:
        file.write('#include <assert.h>\n#define ESP_ERROR_CHECK(call) assert((call) == 0)\n')
    import plistlib
    def parse_txt(data):
        result, pos = {}, 0
        while pos < len(data):
            length = data[pos]
            pos += 1
            assert length > 0 and pos + length <= len(data)
            item = data[pos:pos + length].decode("ascii")
            pos += length
            key, value = item.split("=", 1)
            assert key not in result
            result[key] = value
        assert pos == len(data)
        return result
    for force_v1 in (False, True):
        binary = temp / ("test-icon-v1" if force_v1 else "test-icon")
        command = [os.environ.get("CC", "cc"), "-std=c11", "-I"+str(temp), "-Imain", "-Imain/audio", "-Imain/plist", "-Imain/network", "tests/host/test_icon.c", "main/plist/bplist_builder.c", "main/network/airplay_advertisement.c", "main/network/mdns_airplay.c", "-o", str(binary)]
        if force_v1:
            command.insert(2, "-DCONFIG_AIRPLAY_FORCE_V1=1")
        subprocess.run(command, cwd=ROOT, check=True)
        for mode, model in enumerate(("AudioAccessory5,1", "AppleTV3,2", "AirPlay-ESP32-Speaker")):
            output = temp / "info.plist"
            subprocess.run([str(binary), str(mode), str(output)], check=True)
            info = plistlib.loads(output.read_bytes())
            assert info["model"] == model and info["manufacturer"] == "Cedric"
            assert info["deviceid"] == "00:11:22:33:44:55"
            assert info["pk"] == bytes(range(32))
            expected_lo = 0x5C4A00 if force_v1 else 0x445C4A00 if mode == 2 else 0x405C4A00
            expected_hi = 0 if force_v1 else 0x1C340
            assert info["features"] == (expected_hi << 32) | expected_lo
            assert info["vv"] == (1 if force_v1 else 2)
            unicode_names = ("Test´ Speaker", "测试音箱", "Speaker 🎵", "测试´ 🎵 Receiver",
                             "Boundary \u0080\u0800\U00010000\U0010ffff", "🎵" * 140)
            for index, name in enumerate(unicode_names):
                unicode_info = plistlib.loads(Path(str(output) + f".unicode{index}.plist").read_bytes())
                assert unicode_info["name"] == name
                assert unicode_info["model"] == model
                assert unicode_info["features"] == info["features"]
            airplay = parse_txt(Path(str(output) + ".airplay.txt").read_bytes())
            raop = parse_txt(Path(str(output) + ".raop.txt").read_bytes())
            assert raop["am"] == model and raop["manufacturer"] == "Cedric"
            if force_v1:
                assert not airplay and "ft" not in raop and "pk" not in raop
                assert "txtAirPlay" not in info and "txtRAOP" not in info
                assert not info["features"] & (1 << 26)
            else:
                assert airplay["model"] == model and airplay["manufacturer"] == "Cedric"
                assert airplay["pk"] == raop["pk"] == bytes(range(32)).hex()
                advertised_features = f"0x{expected_lo:X},0x{expected_hi:X}"
                assert airplay["features"] == raop["ft"] == advertised_features
                if mode == 2:
                    assert info["txtAirPlay"] == Path(str(output) + ".airplay.txt").read_bytes()
                    assert info["txtRAOP"] == Path(str(output) + ".raop.txt").read_bytes()
                    assert parse_txt(info["txtAirPlay"]) == airplay
                    assert parse_txt(info["txtRAOP"]) == raop
                    assert info["features"] & (1 << 26)
                    extended = plistlib.loads(Path(str(output) + ".extended.plist").read_bytes())
                    assert len(extended["txtAirPlay"]) > 255
                    assert parse_txt(extended["txtAirPlay"])["deviceid"] == "d" * 64
                else:
                    assert "txtAirPlay" not in info and "txtRAOP" not in info
                    assert not info["features"] & (1 << 26)
    print("PASS: three binary /info presets, manufacturer/features/PK, exact mDNS TXT parity, force-v1 compatibility, Unicode UTF16BE/surrogate names, invalid UTF8 rejection and every output-capacity boundary")
