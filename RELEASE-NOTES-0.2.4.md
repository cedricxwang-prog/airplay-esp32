# 0.2.4 by cedric

Upstream baseline: rbouteiller/airplay-esp32 commit 811d5f8 (v0.2.1).
Local predecessor: 2801627 (0.2.3 by cedric). This is an experimental fork release. Revision tag: `v0.2.4-cedric.1` (phone-style WiFi selection, connected scans and credential validation).

## 本次迭代 / Since 0.2.3

- 扫描网络时保持 WiFi 连接，修复网页请求被主动断线及自动重连打断的问题。 Keep the station associated while scanning instead of resetting the requesting HTTP connection.
- 点选扫描结果直接输入密码连接；已保存网络可复用设备内密码，显示已保存/实际已连接状态。 Select a discovered network, enter its password and connect; reuse device-stored credentials for saved networks without exposing them.
- 支持开放网络，完整保留 32 字节 SSID 和 64 字符十六进制 PSK；验证凭据并区分保存成功与实际连接成功。 Support open networks and full-length credentials, validate input and distinguish saved configuration from confirmed connection.

## 修改及优化 / Changes

| 文件 / Files | 相比上游的变化 / Change from upstream |
|---|---|
| main/rtsp/rtsp_server.c, rtsp_handlers.c | 保留本地已有 iOS 探测连接隔离与 /info 查询兼容，避免探测连接中断正在播放的会话。 Preserve local iOS probe isolation and /info query compatibility. |
| main/audio/audio_timing.c | 保留本地 80 ms buffered 迟到容忍及四帧启动；首帧也按半帧窗口等待 PTP/NTP 时间，避免宽阈值导致各设备提前启动；丢弃已完全播放的重复 RTP 帧；单声道扩展为双通道；补偿重采样滤波延迟。 Strict first-frame scheduling, fully stale duplicate rejection, mono expansion, resampler delay compensation. |
| main/network/ptp_clock.c | 用递归任务互斥锁保护时钟滤波、主时钟选择和读数，防止双核并发读写 64 位偏移及重置状态竞争。 Protect clock state with a recursive task mutex. |
| main/audio/audio_receiver.c | 播放读取与 anchor、pause、flush 等控制操作串行化，减少 seek 和暂停恢复的状态竞争。 Serialize playback reads with timing control operations. |
| main/audio/audio_buffer.c/h | 队列计数作为真实状态，二值信号量仅用于唤醒；避免 flush 清除新帧通知；限制声道数。 Queue count is authoritative; binary wake hint avoids flush notification races. |
| main/audio/audio_output.c, audio_output_spdif.c, audio_output_usb.c; main/playback_control.c | 本地静音时输出精确零 PCM，同时继续消费时间线，保持同步；音量更新不解除硬件静音；修复 I2S 播放任务退出重采样缓存泄漏；输出缓存覆盖最低 8 kHz 输入的转换比例。 Explicit digital mute without stopping the timeline, preserve DAC mute, free exit buffer, size conversion buffers for supported minimum rate. |
| main/audio/audio_resample.c/h; main/Kconfig.projbuild | 44.1 kHz 输出也支持异采样率输入；检查转换缓存分配失败；提供滤波群延迟。 Enable conversion for 44.1 kHz output, check allocation failures, expose filter delay. |
| main/audio/audio_decoder.c | AAC ADTS 使用协商采样率/声道数；拒绝不支持的 PCM 输出格式。 ADTS reflects negotiated rate/channels; reject unsupported PCM formats. |
| main/audio/audio_stream_buffered.c | 读取 buffered 包头前检查完整最小长度。 Validate minimum header length before reading. |
| main/network/log_stream.c | 在原始日志处理消耗 va_list 之前复制，修复可变参数复用。 Copy va_list before consumption. |
| data/www/index.html | 主配置页中英文切换、记忆选择、顶部 by Cedric；OTA 与 WiFi 扫描检查 HTTP 状态；扫描按钮防重复点击、超时和真实错误提示；SSID 与提示安全文本渲染。 Main settings language switch, remembered preference, attribution, HTTP error checking, scan busy/timeout feedback and safe text rendering. |
| main/network/wifi.c, web_server.c | WiFi 扫描保持现有连接，不主动断线、不清除 BSSID、不修改重连定时器；启动选 AP 和网页扫描共用互斥锁并释放驱动扫描列表；空结果返回成功空数组，忙/超时等错误返回对应 HTTP 状态。 Scan while associated, preserve station/retry configuration, serialize scan results, clean up driver memory and report empty results/errors correctly. |
| main/network/wifi.c, web_server.c, data/www/index.html | 手机式扫描点选与密码连接；实际连接状态提示；开放网络认证、完整 SSID/PSK 拷贝；断线/重连清理旧事件位；读取完整配置请求并验证输入。 Phone-style selection/password flow, actual link status, open-network authentication, full-length credentials, fresh connection event bits and complete request validation. |
| main/settings.c/h, main/network/web_server.c | 最多保存 8 个 WiFi，迁移原单网络配置；列表只返回 SSID，选择网络复用设备内密码；WiFi 保存成功响应后才重启。 Save up to eight WiFi profiles, migrate the legacy profile, list SSIDs only, reuse stored passwords and respond before reboot. |
| main/settings.c/h, main/network/mdns_airplay.c, web_server.c, main/rtsp/rtsp_handlers.c, main/plist/bplist_builder.c | AirPlay 图标型号提示可选音箱/HomePod mini 或电视/Apple TV；NVS 保存，重启生效；mDNS 与所有 /info 响应保持一致，实际图标由客户端决定。 Persistent speaker/TV model hint, applied at reboot consistently across mDNS and all /info formats; client determines the final icon. |
| config/sdkconfig.defaults.esp32s3, version.txt, dependencies.lock | 包含本地已有板级/依赖配置与新版本标识。 Preserve local board/dependency configuration and identify this release (build version `0.2.4`, UI display `0.2.4 by cedric`). |
| README.md, tests/host/test_wifi_profiles.c, wifi_profiles_run.py, test_wifi_ui.js | 本分支下载与安装入口、WiFi 凭据存储和实际页面脚本回归测试。 Fork installation links and WiFi profile/page-script regression tests. |

## 硬件与安装 / Hardware and installation

Built with PlatformIO environment `esp32s3`: generic ESP32-S3, 16 MB flash, no display, I2S output. Existing wiring and GPIO settings are preserved. This binary is NOT for other boards or output backends.

- `AirPlay-ESP32S3-0.2.4-ota.bin`: application OTA image; upload through the existing firmware update API/UI. Do not flash it at address 0.
- `index.html`: upload separately to `/api/fs/upload?path=/spiffs/www/index.html` using POST with raw file body. Firmware OTA does not update SPIFFS.
- `SHA256SUMS`: integrity checksums.
- Source regression tests: `sh tests/host/run.sh`.
- WiFi profile tests: `python3 tests/host/wifi_profiles_run.py`.
- WiFi page flow tests: `node tests/host/test_wifi_ui.js`.

## 验证与限制 / Validation and limitations

ESP32-S3 firmware builds successfully. Host tests pass for 44.1 ↔ 48 kHz conversion, exact zero silence, output bounds, reset, filter delay, strict startup waiting, mono duplication and stale duplicate removal. Actual settings-code tests cover legacy WiFi migration, password persistence, profile selection, open networks, maximum lengths, full-list preservation and corrupt data rejection. Actual page-script tests cover selection/password focus, saved password reuse, open networks, UTF-8 limits, reboot/link confirmation, failure, timeout, duplicate submission and safe text rendering. All test credentials are synthetic.

On a generic ESP32-S3, three consecutive scans succeeded in about three seconds each while preserving WiFi, BSSID and uptime. Invalid SSIDs/passwords and missing profiles were rejected without reboot; a fragmented HTTP body was consumed correctly. The live page was checked for scan results, connected/saved labels and direct password input. Selecting the current saved network in the live page returned a save acknowledgement, rebooted the device and then displayed confirmed connection with a new uptime. A new password's save path was tested with synthetic credentials in host tests; live reconnection used the device's existing saved credentials. No real passwords were read back. Both online compatible generic ESP32-S3 receivers were upgraded to the same 0.2.4 image. Each passed three consecutive connected scans, configuration validation and fragmented-request checks. OTA and separately uploaded WebUI bytes were verified on both devices. Chinese and English main-page labels were checked in the live browser.

Icon presets still pass independent binary plist parsing. Earlier 0.2.3 on-device tests verified persistence, invalid preset rejection and unchanged advertised model until reboot; the default speaker preset was restored. Actual icon rendering on Apple clients has not been measured.

WiFi scan diagnosis: the inherited scan function called `esp_wifi_disconnect()` before scanning, resetting the requesting HTTP connection and racing the automatic reconnect (`ESP_ERR_WIFI_STATE`). This revision removes that forced disconnect. Scanning still uses the shared radio and may briefly affect audio packet delivery; avoid scanning during critical synchronized playback.

多设备仍沿用 AirPlay 的同一发送端 PTP/NTP anchor 与 RTP 时间线。所有同型号设备应使用相同固件、输出采样率、GPIO/DAC 配置，并在发送端同一组内播放。80 ms 是迟到容忍，不是同步精度保证。尚未完成多设备长时间漂移、端到端声学延迟或模拟底噪测量；不能保证 sample-accurate 同步或底噪消失。

Multi-device playback continues to use the sender's common PTP/NTP anchor and RTP timeline. Use matching firmware, output rate and DAC configuration across devices. The 80 ms late threshold is not a synchronization accuracy specification. Multi-device long-run drift, acoustic delay and analog noise have not been measured. Exact-zero digital silence cannot remove amplifier/DAC power-supply or analog noise. Legacy fallback without a valid network clock is not synchronized.

日志、EQ、测速子页面保留原界面；语言切换应用于主配置页。 Logs, EQ and speedtest retain their existing interfaces; language selection covers the main settings page.
