# 0.2.6 by cedric

Revision tag: `v0.2.6-cedric.1`. Upstream baseline: [rbouteiller/airplay-esp32, commit 811d5f8 (v0.2.1)](https://github.com/rbouteiller/airplay-esp32/commit/811d5f8af750096683dcec7c95d1cbc08c2f7601). Previous fork release: [0.2.5 by cedric](https://github.com/cedricxwang-prog/airplay-esp32/releases/tag/v0.2.5-cedric.1).

修复 AirPlay 设备能被发现但选中后连接失败，以及探测/旧连接清理错误地重置当前播放时钟的问题。用户通过 Mac 顶部系统喇叭选择 Edifier，已确认最终固件的 Spotify 持续播放听感正常。

Fixes AirPlay selection failures and probe/replaced-connection cleanup resetting the active playback clock. The user selected Edifier from the Mac menu-bar sound control and confirmed normal sustained Spotify playback with the final firmware.

## 本次代码改动 / Changes since 0.2.5

| 文件 / Files | 修改及原因 / Changes and rationale |
|---|---|
| `main/settings.c` | 撤回 0.2.5 通用音箱模式新增的 feature bit 26。实机旧模式触发 Mac `/auth-setup` 后断连；图标提示不应添加未实现的认证能力。所有 AirPlay 2 模式恢复 `0x405C4A00,0x1C340`，强制 AirPlay 1 保持 `0x5C4A00`；保留通用型号与 `Cedric` 厂商。 Remove bit 26 after observed authentication failures; retain the generic identity and original capabilities. |
| `main/network/airplay_advertisement.c/h`, `main/rtsp/rtsp_handlers.c`, `main/plist/bplist_builder.c` | 所有模式的 HTTP/RTSP `/info?txtAirPlay&txtRAOP` 恢复旧 `text/parameters` 回复，兼容系统输出探测。普通 `/info` 保留 RTSP binary/HTTP XML；通用模式继续返回同源 AirPlay/RAOP TXT，独立于 bit 26。 Restore legacy query replies while retaining ordinary plist info and shared TXT data. |
| `main/plist/bplist_builder.c`, `main/plist/plist.h`, `main/rtsp/rtsp_handlers.c` | PTP 初始 SETUP 增加 `timingPeerInfo`，包含本机地址的 `Addresses` 数组及相同 `ID`；地址由实际控制 socket 的 `getsockname`/`inet_ntop` 获取。其他请求保留旧回复与原 builder 接口。 Complete PTP peer metadata using the actual receiver socket address; preserve legacy responses. |
| `main/rtsp/rtsp_conn.c`, `main/rtsp/rtsp_server.c` | 探测及被替代旧连接只清理私有 socket/state；全局 PTP 清除与音量 NVS 持久化归真正播放 owner。完整请求 dispatch/takeover 与 owner cleanup 共用互斥锁；旧 event listener 仅匹配 fd 时停止，移除重复 cleanup。 Isolate private cleanup, serialize playback ownership/cleanup and preserve the new session's global state. |
| `main/rtsp/rtsp_server.c` | SETUP/ANNOUNCE/RECORD 接管前退休旧 audio/output/NTP/PTP/DACP/event，配对 POST 保留旧流；`shared_state_retired` 避免 grace 重复清理。控制发送增加 5 秒 `SO_SNDTIMEO`，设置失败则关闭连接。 Retire old shared resources before real stream takeover, preserve pairing-time playback and bound control sends. |
| `CMakeLists.txt`, `version.txt` | 登记版本文件为 CMake 配置依赖，使增量构建更新固件版本。 Make version changes trigger CMake reconfiguration. |
| `main/rtsp/rtsp_handlers.c`, `main/rtsp/rtsp_server.c` | 增加有限的握手/PTP/连接结束诊断，不打印密码、密钥或原始配对报文。 Add limited handshake/disconnection diagnostics without secrets. |
| `tests/host/initial_setup_run.py`, `ptp_cleanup_run.py`, 现有 host tests / existing host tests | 实际 builder 容量与旧输出兼容、连接 owner/接管/探测/grace/event/分段请求/发送超时回归；ASan/UBSan 与旧清理代码负对照。 Add actual-code regressions, sanitizers and a failing legacy-code negative control. |
| `tests/integration/airplay_handshake.py` | 严格验证瞬态 SRP、服务端证明、加密响应、RTSP 状态行/CSeq/Content-Length 和实际 PTP peer 地址。 Add strict synthetic encrypted control-protocol checks. |

清理问题由源码和日志共同确认：一次修复前的 180 秒诊断记录出现 24 次音量持久化写入、14 次重复设置 master，以及 4 次 PTP 锁定后紧接 probe 清理而丢锁。修复限制了清理的作用范围，避免探测连接破坏正在播放的会话；该证据未将所有音质问题归为同一原因。

Source and logs confirmed the cleanup bug: a pre-fix 180-second capture recorded 24 volume-persistence writes, 14 repeated master-setting events and four lock losses immediately after probe cleanup. Cleanup is now scoped to preserve active playback. This evidence does not establish one cause for every possible audio-quality issue.

0.2.5 的静态测试验证了发现信息、TXT、plist 与配置保存，但没有验证真实 Apple 播放流程。0.2.6 增加实际加密握手与用户指定的系统输出/Spotify 实播验证。用户也确认回退 0.2.4 能正常播放；此前空闲 Mac 的握手对照不代表 0.2.4 普遍播放失败。

The 0.2.5 checks covered discovery metadata, TXT/plist structure and settings, without a real Apple playback flow. This release adds encrypted integration checks and the requested system-output/Spotify listening test. The user also confirmed working playback after rolling back to 0.2.4; the earlier idle-Mac handshake comparison did not establish a general playback failure in that release.

PTP peer fields follow [Shairport Sync's initial SETUP implementation](https://github.com/mikebrady/shairport-sync/blob/master/rtsp.c). The three icon presets remain identity hints; final artwork is client-dependent, and the generic preset does not claim Sonos identity or MFi certification.

## 相比上游及继承功能 / Compared with upstream and retained features

完整的既有文件对照见 [0.2.3](RELEASE-NOTES-0.2.3.md)、[0.2.4](RELEASE-NOTES-0.2.4.md) 与 [0.2.5](RELEASE-NOTES-0.2.5.md)。本版本继承：

- 音频时间线与数字静音优化、时钟/buffer 并发保护、解码/重采样边界检查、滤波延迟补偿和探测连接隔离。
- 八个已保存 WiFi、保持连接的扫描、点选密码连接、设备内凭据复用、开放网络/完整长度凭据及重启后实际连接确认。
- 主配置页中英文切换、顶部 `by Cedric`、重启生效的图标配置、型号/厂商/TXT 一致性和非 ASCII 设备名的 UTF-16BE binary plist 编码。

All earlier audio timing/mute, clock/buffer concurrency, decoder/resampler and probe-isolation fixes remain included. Saved WiFi profiles, connected scans, phone-style password entry, stored-credential reuse and confirmed reconnection remain available. The bilingual main WebUI, `by Cedric`, persistent icon presets and Unicode plist names are retained. Earlier notes provide the full upstream file comparison.

输出采样率、DAC GPIO、音频调度与 PTP/NTP anchor 算法保持既有配置。同类型设备应使用相同固件、输出采样率和 DAC 配置，在同一发送端组内播放。多设备声学延迟、长时间漂移与模拟底噪尚未测量；数字零静音不能消除模拟放大器或电源噪声。

Output rate, DAC wiring, audio scheduling and PTP/NTP anchor algorithms retain the existing configuration. Use matching firmware/output/DAC settings within the same sender group. Multi-device acoustic delay, long-run drift and analog noise have not been measured; exact-zero digital mute cannot remove analog amplifier or power-supply noise.

## 验证 / Validation

- 完整 host suite 通过：实际 initial SETUP builder 的旧输出逐字节兼容、IPv4/IPv6 peer、五种端口边界、容量 0–512 canary、ASan/UBSan、独立 `plistlib` 解析及非法输入拒绝；24 个 HTTP/RTSP handler 分支及音频/WiFi 回归。 The full host suite passed, including builder/capacity/sanitizer checks, all 24 handler paths and audio/WiFi regressions.
- 实际连接清理测试通过：owner-only PTP/音量清理、probe、接管、配对保流、grace/event/分段请求和发送超时；旧代码负对照触发预期失败。 Actual cleanup regressions passed; the legacy implementation fails the negative-control check.
- 兼容接收端的严格合成加密 PTP 测试通过：瞬态 SRP、加密响应与实际 peer 地址。 Strict synthetic encrypted PTP checks passed on a compatible receiver.
- 用户从 Mac 顶部系统喇叭选择 Edifier，用 Spotify 持续播放，确认听感正常。日志采集为初始约 96 秒、后续约 70 秒两个独立窗口，中间有间隔；窗口内记录显示 PTP 锁定及 `gaps=0`。 The user confirmed normal sustained Spotify sound after menu-bar selection. Two separate log windows, approximately 96 seconds initially and 70 seconds later, showed PTP locked and `gaps=0`; there was a gap between captures.
- 发布前只读核对两台设备均报告 `firmware_version=0.2.6`、WiFi 在线，核对期间未重启。 Read-only pre-release checks confirmed both receivers on 0.2.6 and online, without restarting them during the check.

日志结果只涵盖上述采集窗口；本次没有进行多设备声学同步测量或完整长时间连续日志采集。合成协议测试独立于真实音频验收。

Log findings cover those captured windows. This release did not measure multi-device acoustic synchronization or collect a complete uninterrupted long-run log. Synthetic protocol checks are separate from the real listening test.

Regression commands:

```sh
sh tests/host/run.sh
python3 tests/host/wifi_profiles_run.py
node tests/host/test_wifi_ui.js
python3 tests/integration/airplay_handshake.py --self-test
```

## 固件与安装 / Firmware and installation

构建环境 `esp32s3`：通用 ESP32-S3、16 MB flash、无屏幕、现有 I2S 输出及 GPIO 配置。下列 BIN 是应用 OTA 镜像，适用于匹配的板型与配置。应用 OTA 不更新 SPIFFS；WebUI 如需更新须单独上传。

Built with `esp32s3` for the generic ESP32-S3, 16 MB flash, no display and existing I2S configuration. The BIN is an application OTA image for matching hardware. Application OTA does not update SPIFFS; upload the WebUI separately if needed.

| 发布文件 / Release asset | 信息 / Details |
|---|---|
| `AirPlay-ESP32S3-0.2.6-ota.bin` | 1,460,400 bytes |
| SHA-256 | `9a12d006d79756361314f704b73d4c55a3e6ce529d7ae41426fc13067b1b08cd` |
| Release tag | `v0.2.6-cedric.1` |

- 从 [0.2.6 Release / BIN](https://github.com/cedricxwang-prog/airplay-esp32/releases/tag/v0.2.6-cedric.1) 下载，核对 `SHA256SUMS` 后通过设备固件更新页上传应用 BIN；不要刷到地址 0。 Download the release, verify the checksum and upload the application BIN through the device OTA page.
- `index.html` 单独以原始 POST body 上传到 `/api/fs/upload?path=/spiffs/www/index.html`。 Upload the WebUI separately with a raw POST body.
