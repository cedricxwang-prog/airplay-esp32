<div align="center">

<img src="docs/assets/logo_airplay_esp32.png" alt="AirPlay ESP32" width="400">

# ESP32 AirPlay 2 Receiver

**Stream music from your Apple devices — or from any phone over Bluetooth — to any speaker for about $10**

[![GitHub stars](https://img.shields.io/github/stars/rbouteiller/airplay-esp32?style=flat-square)](https://github.com/rbouteiller/airplay-esp32/stargazers)
[![GitHub forks](https://img.shields.io/github/forks/rbouteiller/airplay-esp32?style=flat-square)](https://github.com/rbouteiller/airplay-esp32/network)
[![License](https://img.shields.io/badge/license-Non--Commercial-blue?style=flat-square)](LICENSE)
[![ESP-IDF](https://img.shields.io/badge/ESP--IDF-v5.5+-red?style=flat-square)](https://docs.espressif.com/projects/esp-idf/)
[![Platform](https://img.shields.io/badge/platform-ESP32%20%7C%20S2%20%7C%20S3%20%7C%20C5-green?style=flat-square)](https://www.espressif.com/en/products/socs)

### [Documentation](https://rbouteiller.github.io/airplay-esp32/) · [Install from your browser](https://rbouteiller.github.io/airplay-esp32/getting-started/flashing/) · [Troubleshooting](https://rbouteiller.github.io/airplay-esp32/troubleshooting/)

</div>

---

## Cedric fork — 0.2.7 by cedric

下载本分支固件：[0.2.7 Release / BIN](https://github.com/cedricxwang-prog/airplay-esp32/releases/tag/v0.2.7-cedric.1)。上传一个 `AirPlay-ESP32S3-0.2.7-ota.bin` 即可同时更新程序和 WebUI，无需另传 `index.html`。[0.2.7 Release Note](RELEASE-NOTES-0.2.7.md) 列出本次 OTA 与页面整合改动；[0.2.6 Note](RELEASE-NOTES-0.2.6.md) 记录 AirPlay 握手/PTP 清理修复和实际 Spotify 验证，[0.2.4 Note](RELEASE-NOTES-0.2.4.md) 记录相对上游的音频与 WiFi 优化。本分支基于 [rbouteiller/airplay-esp32](https://github.com/rbouteiller/airplay-esp32)。

0.2.7 将配置、日志和测速页面嵌入应用固件，支持 EQ 的板型同时包含 EQ 页面，避免 SPIFFS 中旧页面继续生效。主页面增加应用 BIN 检查，并在重启后核对设备身份、固件与 WebUI 版本再报告升级成功；同版本重装也需要重启证据。已保存 WiFi、名称、图标和输出设置继续保留，分区表不变。Firmware and available WebUI pages now update through one application BIN, including EQ on supported boards. The new main page validates the image and confirms a reboot, matching device identity and matching firmware/WebUI before reporting success.

0.2.7 保留 0.2.6 的音频时钟、调度与输出算法。用户已确认 0.2.6 经 Mac 顶部系统喇叭切换后的 Spotify 持续播放听感正常；这项历史验收不代表已完成 0.2.7 的新试听。多设备声学同步及长时间漂移尚未测量。The 0.2.6 menu-bar system-output/Spotify listening test passed; 0.2.7 retains those audio algorithms. Grouped acoustic synchronization remains unmeasured.

WebUI 支持中英文：扫描 WiFi → 点选网络 → 输入密码连接；已保存网络可复用密码，重启并确认实际连接后显示“已连接”。扫描保持原 WiFi 关联，不再主动断线。

AirPlay 图标增加“通用音箱（类似 Sonos）”，保留 HomePod mini 与 Apple TV。选择并保存后重启应用；iOS/macOS 决定最终图案。Generic speaker mode advertises a third-party speaker identity with matching Bonjour and `/info` metadata; it does not claim to be a Sonos product.

The release contains one OTA application image for the generic ESP32-S3 with 16 MB flash, 8 MB PSRAM and the configured I2S wiring. Upload the BIN through the device's firmware update page; the embedded WebUI updates with it. When upgrading through an older page, wait for reboot and reopen the device address to load the new page. SPIFFS remains available for board-specific DSP/display assets and file management. This application image requires an existing compatible bootloader and partition table; it is not a complete first-flash image. The browser installer linked below installs upstream firmware. See the release note for hardware compatibility and synchronization test limits.

## What is this?

This turns a cheap ESP32 board into a wireless AirPlay 2 speaker. Plug it into any
amplifier or powered speakers and it shows up on your iPhone, iPad or Mac just like a
HomePod or an AirPlay TV.

Works with **ESP32**, **ESP32-S2**, **ESP32-S3** and **ESP32-C5** chips, including the
[SqueezeAMP](https://github.com/philippe44/SqueezeAMP) (ESP32 + TAS5756) and
[Esparagus Audio Brick](https://sonocotta.com/espragus-audio-brick/) (ESP32 + TAS5825M)
boards, which have amplifiers built in.

ESP32-based boards also support **Bluetooth A2DP**, so anything that can pair with a
Bluetooth speaker can play to them when AirPlay is idle. The Esparagus Audio Brick
additionally supports **wired Ethernet** through an optional W5500 module.

**No cloud. No app. Just tap and play.**

## Quick start

The fastest route is the browser installer — no toolchain, no command line:

**[→ Install from your browser](https://rbouteiller.github.io/airplay-esp32/getting-started/flashing/)**

Building from parts instead? You need an ESP32-S3 dev board, a PCM5102A DAC and a female
pin header, roughly $10 total, and no soldering. See the
[getting started guide](https://rbouteiller.github.io/airplay-esp32/getting-started/).

Building from source:

```bash
git clone --recursive https://github.com/cedricxwang-prog/airplay-esp32
cd airplay-esp32
pio run -e esp32s3
pio run -e esp32s3 -t upload    # first USB flash: generated bootloader, partitions and application
# Only needed for hardware that uses SPIFFS DSP/display assets:
# pio run -e <matching-board-environment> -t uploadfs
```

For later OTA updates, use `.pio/build/esp32s3/firmware.bin` through the device's
firmware update page. The CMake `EMBED_FILES` and PlatformIO
`board_build.embed_files` declarations embed the four pages automatically;
rebuild the application after changing a page. Separate WebUI uploads are no
longer required. Select the build environment matching your board and output;
the published generic ESP32-S3 BIN is not for every supported board.

## Features

- **AirPlay 2** — appears natively in Control Center and every AirPlay-capable app
- **ALAC and AAC decoding** — live streaming (Siri, calls) and music playback
- **Multi-room** — PTP-based timing for synchronised playback
- **Bluetooth A2DP** — receive audio from phones and tablets (ESP32 boards only)
- **W5500 Ethernet** — wired networking with automatic WiFi failover
- **Web configuration** and **OTA updates** — USB is only needed for the first flash
- **48 kHz output** — optional 44.1 → 48 kHz conversion via a sinc resampler
- **Displays** — optional OLED or 320×170 colour TFT with track metadata
- **Hardware buttons** — optional play/pause, volume and track skip

Audio only, one speaker per board, and a decent WiFi signal is required.

## Documentation

| | |
| --- | --- |
| [Getting started](https://rbouteiller.github.io/airplay-esp32/getting-started/) | Shopping list, assembly, flashing, first boot |
| [Supported boards](https://rbouteiller.github.io/airplay-esp32/boards/) | SqueezeAMP, Esparagus Audio Brick, XIAO ESP32-C5, custom boards |
| [Features](https://rbouteiller.github.io/airplay-esp32/features/bluetooth/) | Bluetooth, Ethernet, displays, buttons, AirPlay tuning |
| [Reference](https://rbouteiller.github.io/airplay-esp32/reference/build-environments/) | Build environments, SPIFFS, OTA, architecture |
| [Troubleshooting](https://rbouteiller.github.io/airplay-esp32/troubleshooting/) | No sound, no setup WiFi, dropouts, build errors |

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) and the
[contributing guide](https://rbouteiller.github.io/airplay-esp32/contributing/).
Documentation lives in [`docs/`](docs/) and is built with [Zensical](https://zensical.org/)
— every page on the site has an edit link that takes you straight to the GitHub editor.

## Acknowledgements

- [Shairport Sync](https://github.com/mikebrady/shairport-sync) — the reference AirPlay implementation
- [openairplay/airplay2-receiver](https://github.com/openairplay/airplay2-receiver) — Python AirPlay 2 implementation
- [Espressif](https://github.com/espressif) — ESP-IDF framework and codec libraries

## Legal

**Non-commercial use only.** Commercial use requires explicit permission — see [LICENSE](LICENSE).

This is an independent project based on protocol analysis. Not affiliated with Apple Inc.
Not guaranteed to work with future iOS or macOS versions. Provided as-is without warranty.
