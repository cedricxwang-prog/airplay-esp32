# 0.2.5 by cedric

Revision tag: `v0.2.5-cedric.1`. Upstream baseline: [rbouteiller/airplay-esp32 811d5f8 (v0.2.1)](https://github.com/rbouteiller/airplay-esp32/commit/811d5f8af750096683dcec7c95d1cbc08c2f7601). Previous fork release: [0.2.4 by cedric](https://github.com/cedricxwang-prog/airplay-esp32/releases/tag/v0.2.4-cedric.1).

## 本次改动 / Since 0.2.4

增加“通用音箱（类似 Sonos）”图标模式。此前“音箱”选项使用 HomePod mini 型号，客户端会显示 HomePod 外形；Apple TV 模式显示电视盒图标。新模式广播独立的第三方音箱身份：`model=AirPlay-ESP32-Speaker`、`manufacturer=Cedric`，AirPlay 2 增加 `HasUnifiedAdvertiserInfo`（bit 26）。不使用 Sonos 的品牌、设备型号或其他能力配置。

Adds a generic third-party speaker preset alongside HomePod mini and Apple TV. This is a client display hint, not an uploaded icon image. The receiver identifies itself as Cedric's ESP32 speaker rather than a Sonos product. Existing saved preset numbers and the original default remain compatible.

| 文件 / Files | 改动 / Changes |
|---|---|
| `main/settings.c/h` | 新增模式 2、统一型号映射和启动时 features；NVS 保存后型号与分类一起在重启生效，避免播放途中身份不一致。 Add preset 2 and apply its model/features together at reboot. |
| `main/network/airplay_advertisement.c/h`, `mdns_airplay.c/h`, `main/CMakeLists.txt` | 共用 AirPlay/RAOP 广播项与有边界检查的 DNS TXT 编码，保留原音频、配对、PTP 与 buffered 能力。 Shared advertisement data and bounded DNS TXT encoding; existing playback capabilities are retained. |
| `main/rtsp/rtsp_handlers.c/h`, `main/plist/bplist_builder.c` | mDNS、XML 与二进制 `/info` 的型号、厂商、features 一致；通用模式同时返回同源 `txtAirPlay` 与 `txtRAOP` 数据。扩大二进制响应容量并支持大于 255 字节的数据长度。 Consistent discovery and info metadata, unified TXT data and checked response capacity. |
| `main/plist/bplist_builder.c`, `tests/host/test_icon.c`, `run.py` | 修复设备名称中的非 ASCII 字符被误标为 ASCII，导致标准 plist 解析失败的问题；严格验证 UTF-8 并编码为 UTF-16BE，支持中文与表情。 Fix non-ASCII device names in binary plist, with strict UTF-8 validation and UTF-16BE encoding including surrogate pairs. |
| `main/network/web_server.c`, `data/www/index.html` | 中英文第三个图标选项；API 区分已保存型号与当前活动型号，保持重启提示。 Bilingual preset selection and explicit configured/active model reporting. |
| `tests/host/*`, `version.txt`, `README.md` | 新模式持久性、重启时机、旧模式兼容、TXT 与 plist 解析/容量验证；版本及下载说明。 Regression coverage and release links. |

旧 HomePod 与 Apple TV 模式保持原 features；强制 AirPlay 1 配置不启用 bit 26。新增 bit 26 不声明 MFi 认证或新的配对方式。

The classification follows published [reverse-engineering notes](https://github.com/openairplay/airplay-spec/blob/master/index.html) and unified TXT handling in [Shairport Sync](https://github.com/mikebrady/shairport-sync/blob/master/rtsp.c). It is not an Apple certification statement. iOS/macOS chooses the final artwork.

## 相比 GitHub 原版本 / Compared with upstream

继承 0.2.4 的音频时间线和静音优化、时钟与 buffer 并发修复、解码与重采样边界检查、已保存 WiFi 管理、扫描保持连接、手机式点选密码连接、中英文主界面与 by Cedric。完整文件对照与此前测试见 [0.2.4 Release Note](https://github.com/cedricxwang-prog/airplay-esp32/blob/main/RELEASE-NOTES-0.2.4.md)。本次修改集中于设备身份与图标分类，没有调整输出采样率、PTP/NTP anchor、DAC GPIO 或音频调度。

All earlier fork audio and WiFi fixes remain included. Use matching firmware, output rate and DAC settings on devices in the same AirPlay group. Audio timing algorithms are unchanged in this iteration; client grouping and long-run acoustic synchronization still require end-to-end measurement.

## 安装 / Installation

This OTA build targets the generic **ESP32-S3 with 16 MB flash**, existing I2S wiring and no display, matching the previous release. It is an application image, not a merged factory image and not a build for other board/output configurations.

- Upload `AirPlay-ESP32S3-0.2.5-ota.bin` through the device's firmware update page/API. Do not flash it at address 0.
- Upload `index.html` separately via raw POST to `/api/fs/upload?path=/spiffs/www/index.html`; application OTA does not update SPIFFS.
- Check `SHA256SUMS` before installation.
- 在设备设置中选择“通用音箱（类似 Sonos）”→ 保存图标 → 重启设备。Select Generic speaker, save the icon setting and restart.

## 验证 / Validation

ESP32-S3 firmware builds successfully (application image: 1,456,928 bytes). Regression commands:

```sh
sh tests/host/run.sh
python3 tests/host/wifi_profiles_run.py
node tests/host/test_wifi_ui.js
```

Host tests use actual firmware code and independent `plistlib` parsing. They cover three presets in AirPlay 1/2, 24 HTTP/RTSP plain/query handler paths, byte-identical registered mDNS and embedded TXT data, public-key/features consistency, all undersized output buffers, extended data lengths, accented/CJK/emoji names and invalid UTF-8 rejection. Settings tests verify persistence, pending/reboot semantics, invalid presets and legacy modes. Existing timing/resampling and WiFi page/profile tests continue to pass.

两台在线兼容 ESP32-S3 已使用同一最终镜像和 WebUI，均设为通用音箱模式。实机验证包括：非法图标值不改变配置、保存后活动型号/features 保持旧值、重启后新模式持久保存；真实 Bonjour AirPlay/RAOP、XML 与 binary `/info` 的型号、厂商、features、公钥和 TXT 数据一致；旧 HTTP query 返回文本格式。非 ASCII 设备名的二进制设备信息可正常解析。主页面中英文图标选项已验证，WiFi 扫描、输入验证与分段请求回归保持通过。

Both compatible online receivers run the same final image with separately verified WebUI bytes. Live checks confirm preset persistence and reboot timing, discovery/info consistency, preserved network settings and classic HTTP query compatibility. The final generic speaker artwork in iOS/macOS and end-to-end grouped audio playback have not been measured in this iteration. Receiver classification is verified; actual client artwork remains client-dependent. Previous synchronization and analog-noise measurement limits still apply.
