# 0.2.2-cedric.1 — by Cedric

Upstream baseline: rbouteiller/airplay-esp32 commit 811d5f8 (v0.2.1).
Local predecessor: 3c7a4b2 (0.2.1.2). This is an experimental fork release.

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
| data/www/index.html | 主配置页中英文切换、记忆选择、顶部 by Cedric；OTA 检查 HTTP 状态；SSID 与提示安全文本渲染。 Main settings language switch, remembered preference, attribution, HTTP error checking, text rendering. |
| config/sdkconfig.defaults.esp32s3, version.txt, dependencies.lock | 包含本地已有板级/依赖配置与新版本标识。 Preserve local board/dependency configuration and identify this release. |

## 硬件与安装 / Hardware and installation

Built with PlatformIO environment `esp32s3`: generic ESP32-S3, 16 MB flash, no display, I2S output. Existing wiring and GPIO settings are preserved. This binary is NOT for other boards or output backends.

- `AirPlay-ESP32S3-0.2.2-cedric.1-ota.bin`: application OTA image; upload through the existing firmware update API/UI. Do not flash it at address 0.
- `index.html`: upload separately to `/api/fs/upload?path=/spiffs/www/index.html` using POST with raw file body. Firmware OTA does not update SPIFFS.
- `SHA256SUMS`: integrity checksums.
- Source regression tests: `sh tests/host/run.sh`.

## 验证与限制 / Validation and limitations

ESP32-S3 firmware builds successfully. Host tests cover 44.1 ↔ 48 kHz conversion, exact zero silence, output bounds, reset, filter delay, strict startup waiting, mono duplication and stale duplicate removal. JavaScript syntax and Chinese/English switching were checked.

多设备仍沿用 AirPlay 的同一发送端 PTP/NTP anchor 与 RTP 时间线。所有同型号设备应使用相同固件、输出采样率、GPIO/DAC 配置，并在发送端同一组内播放。80 ms 是迟到容忍，不是同步精度保证。尚未完成多设备长时间漂移、端到端声学延迟或模拟底噪测量；不能保证 sample-accurate 同步或底噪消失。

Multi-device playback continues to use the sender's common PTP/NTP anchor and RTP timeline. Use matching firmware, output rate and DAC configuration across devices. The 80 ms late threshold is not a synchronization accuracy specification. Multi-device long-run drift, acoustic delay and analog noise have not been measured. Exact-zero digital silence cannot remove amplifier/DAC power-supply or analog noise. Legacy fallback without a valid network clock is not synchronized.

日志、EQ、测速子页面保留原界面；语言切换应用于主配置页。 Logs, EQ and speedtest retain their existing interfaces; language selection covers the main settings page.
