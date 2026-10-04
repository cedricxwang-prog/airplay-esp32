# 0.2.7 by cedric

Revision tag: `v0.2.7-cedric.1`. Previous fork release: [0.2.6 by cedric](https://github.com/cedricxwang-prog/airplay-esp32/releases/tag/v0.2.6-cedric.1). Upstream baseline: [rbouteiller/airplay-esp32, commit 811d5f8 (v0.2.1)](https://github.com/rbouteiller/airplay-esp32/commit/811d5f8af750096683dcec7c95d1cbc08c2f7601).

解决“网页提示升级成功，版本号更新但功能仍是旧版”的问题：此前应用 OTA 不更新 SPIFFS 中的网页。现在配置、日志和测速页面随应用一起打包，并直接由固件提供；支持 EQ 的板型同时包含 EQ 页面。上传一个 BIN 即可更新程序和 WebUI，无需单独上传 `index.html`。

Fixes firmware upgrades retaining the old WebUI: earlier application OTA left the SPIFFS pages unchanged. Available pages are now embedded and served directly from the application, including EQ on supported boards. One BIN updates both firmware and WebUI.

## 本次代码改动 / Changes since 0.2.6

| 文件 / Files | 修改及优化 / Changes and rationale |
|---|---|
| `main/CMakeLists.txt`, `platformio.ini`, `CMakeLists.txt` | 将 `index.html`、`logs.html`、`speedtest.html`、`eq.html` 嵌入应用；同时配置 ESP-IDF 与 PlatformIO 的生成规则。保留 SPIFFS 镜像与分区供 DSP 固件、显示背景和文件管理使用。 Embed the four pages in both build systems while retaining board assets in SPIFFS. |
| `main/network/web_server.c` | `/`、`/logs`、`/speedtest` 和支持 EQ 的板型上的 `/eq` 直接发送嵌入页面。设置 UTF-8、`Cache-Control: no-store`、`X-WebUI-Version` 和 `X-WebUI-Source: embedded`，旧 SPIFFS 页面不再覆盖这些路由。 Serve application pages with explicit version/source and cache headers. |
| `main/network/web_server.c` | 系统信息新增 WebUI 版本/来源、每次开机变化的 `boot_id`、实际应用 chip ID 和 OTA 分区容量，供上传前检查和重启后确认。 Report image and boot metadata for upgrade verification. |
| `data/www/index.html` | 上传前检查应用镜像头、应用描述、版本、segment 边界与文件长度；若设备提供元数据，则核对芯片和 OTA 分区容量。拒绝完整工厂/合并镜像、截断或结构不符的文件。增加中英文上传/确认状态、超时与防重复提交。 Validate the application image structure and available hardware limits before upload; provide bilingual progress and errors. |
| `data/www/index.html` | 上传后确认同一 MAC、重启证据、目标固件与 WebUI 版本及嵌入来源；连续两次稳定确认后再检查首页响应头，成功才自动重新载入页面。网络中断会进入确认流程，明确 HTTP 错误直接报错。 Confirm the reboot and matching firmware/page before reporting success, including same-version reinstalls. |
| `main/network/ota.c` | 在分配/写入前拒绝空文件和超分区文件；连续三次接收超时后退出；只有首次 PSRAM 分配失败才改为 streaming，防止已经消费上传内容后错误地重新读取。 Bound stalled uploads and restrict fallback to the initial allocation failure. |
| `main/network/web_server.c`, `main/rtsp/rtsp_server.c/h` | 上传失败后仅恢复此前已运行的 AirPlay listener；RTSP 重启在有限时间内等待旧 client 清理完成，避免复用仍被旧 worker 持有的槽位。 Reopen a previously active listener after failure and wait for old clients before reusing their slots. |
| `tests/host/embedded_webui_run.py`, `ota_run.py`, `test_ota_ui.js`, `run.sh` | 实际 C 页面发送/OTA 路径与实际主页面脚本回归；覆盖嵌入页面、错误传播、接收边界、失败恢复、镜像检查和重启确认。 Add actual-code regressions for page serving and the upload/confirmation flow. |
| `version.txt`, `README.md` | 应用版本 `0.2.7`，界面显示 `0.2.7 by cedric`，更新单 BIN 安装说明。 Identify the release and document one-file OTA. |

失败恢复重新开放 AirPlay 服务，正在播放的会话仍会被升级过程终止；不保证自动恢复上一首歌曲。升级中会停止接收音频并重启设备。

Failure recovery reopens the listener; it does not resume the interrupted playback session. Updating stops audio reception and reboots the device.

## 相比上游及继承功能 / Compared with upstream and retained features

本次以上游 v0.2.1 为基础的 Cedric 0.2.6 代码继续迭代。完整的既有文件对照见 [0.2.4 Note](RELEASE-NOTES-0.2.4.md) 和 [0.2.6 Note](RELEASE-NOTES-0.2.6.md)：音频时间线/数字静音、PTP 与 buffer 并发保护、解码/重采样边界与滤波延迟补偿、探测连接隔离、AirPlay 握手/PTP owner 清理，以及八个已保存 WiFi、保持连接扫描、密码连接、图标配置、中英文主页面和顶部 `by Cedric` 均保留。

The earlier notes provide the full upstream file comparison. This release retains the existing audio timing/mute, clock/buffer protection, decoder/resampler, AirPlay handshake/session cleanup, saved-WiFi, connected-scan, icon and bilingual main-page changes.

本次未改动音频时钟、PTP/NTP anchor、播放调度、采样率、DAC GPIO 或输出算法，也未改动分区表或保存配置的 NVS 格式。同类型设备应使用相同固件、输出采样率和 DAC 配置，在同一发送端组内播放；多设备声学同步、长时间漂移及模拟底噪仍未测量。

Audio clock, anchor, scheduling and output algorithms retain 0.2.6 behavior. Partition layout and NVS settings remain compatible. Use matching firmware, output rate and DAC configuration within one sender group. Acoustic synchronization, long-run drift and analog noise remain unmeasured.

## 验证 / Validation

- 实际 C 代码的页面发送与 OTA 测试通过，包含 ASan/UBSan：四个页面的字节和响应头、非法 span/发送错误、空/过大上传、连续超时、PSRAM fallback 与上传失败 listener 恢复。 Actual C-code tests passed with ASan/UBSan for embedded serving, upload boundaries/fallback and failure recovery.
- 实际主页面脚本测试覆盖镜像结构、芯片/容量检查、错误状态、断网后的确认、同版本重装、错误设备或旧页面拒绝、两次稳定确认、自动重新载入及语言切换。 The page-script tests cover validation, failure and reboot/page-confirmation cases.
- 完整 host suite、WiFi profiles/UI 和协议离线 self-test 通过；OTA UI 共 57 个用例，其中一个解析已有真实应用镜像。最终 ESP32-S3 构建通过，配置/日志/测速页面在 BIN 中逐字节匹配源码。 The full host suite, WiFi and offline protocol checks passed; 57 OTA UI cases include an existing real application image. The final build contains the exact available page bytes.
- 实机网页升级、同版本重装及 0.2.7 Spotify 试听尚未完成，不以 host 测试替代。 Live GUI installation, same-version reinstall and a new 0.2.7 Spotify listening test are pending.
- 0.2.6 的 Mac 顶部系统输出切换/Spotify 实播已由用户确认正常，详见其 release note。本次不把该历史结果写成 0.2.7 的新试听结果。 The documented 0.2.6 listening result is retained as historical evidence, not a new 0.2.7 playback test.

Regression commands:

```sh
sh tests/host/run.sh
python3 tests/host/wifi_profiles_run.py
node tests/host/test_wifi_ui.js
python3 tests/integration/airplay_handshake.py --self-test
```

日志、测速和 EQ 页面保留原界面；中英文切换应用于主配置页。EQ 路由仍仅对支持 TAS58xx EQ 的板型启用。 Logs, speedtest and EQ retain their existing interfaces; language selection covers the main settings page. The EQ route remains hardware-dependent.

## 固件与安装 / Firmware and installation

发布应用 BIN：`AirPlay-ESP32S3-0.2.7-ota.bin`。构建环境 `esp32s3`：通用 ESP32-S3、16 MB flash、8 MB PSRAM、现有 I2S 输出、无显示屏；OTA 应用分区容量为 `0x300000`（3 MiB）。适用于匹配板型、现有分区与输出配置，校验值见发布附件 `SHA256SUMS`。

The single application BIN targets the generic ESP32-S3 build with 16 MB flash, 8 MB PSRAM, existing I2S output and a 3 MiB application OTA slot. Verify it using the release's `SHA256SUMS` and use it only with matching hardware/configuration.

| 发布文件 / Release asset | 信息 / Details |
|---|---|
| `AirPlay-ESP32S3-0.2.7-ota.bin` | 1,520,304 bytes |
| SHA-256 | `9524c90e539d946a65e9084ec5fdbcccb97a2ef0f60a8916e0173827d399171a` |
| Release tag | `v0.2.7-cedric.1` |

1. 从 [0.2.7 Release / BIN](https://github.com/cedricxwang-prog/airplay-esp32/releases/tag/v0.2.7-cedric.1) 下载并核对 SHA-256。
2. 在设备固件升级页选择该 BIN，上传一次。程序和该板型启用的页面随同一应用镜像更新；无需另传 HTML 或刷新 SPIFFS。
3. 设备重启后核对固件和 WebUI 均为 `0.2.7`。如果通过 0.2.6 或更早的旧页面上传，等待设备重启后重新打开设备地址以加载新页面；新确认流程从 0.2.7 页面开始提供。

Download and verify the release BIN, upload it once through the device's firmware update page, then check firmware and WebUI versions after reboot. When uploading through an older page, reopen the device address after reboot to load 0.2.7's new confirmation interface.

已保存 WiFi、设备名称、图标及输出设置不因本次升级清除。SPIFFS 继续用于板级 DSP/显示素材及文件管理；手动上传 HTML 到 SPIFFS 不再改变这些嵌入页面，修改网页后需重建应用。

Saved settings are preserved. SPIFFS remains available for DSP/display assets and file management. Rebuild the application to update embedded pages.

这个 BIN 是 OTA 应用镜像，不是完整工厂/合并镜像，不能作为首次 USB 烧录的地址 0 镜像。首次安装应使用匹配板型构建生成的 bootloader、分区表和应用，并按硬件需要烧录 SPIFFS 素材；后续单 BIN OTA 不改变分区表。

This is an application OTA image, not a complete factory/merged image. First USB installation still requires the generated bootloader, partition table and application at their proper offsets, plus any board-required SPIFFS assets. Subsequent one-BIN OTA preserves the partition layout.
