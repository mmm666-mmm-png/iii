#pragma once

#include <Arduino.h>
#include <WiFi.h>
#include <esp_camera.h>

// 如果存在 local_config.h，就优先使用本地配置。该文件不提交到仓库，
// 适合放真实 Wi-Fi 密码、服务器地址和设备 token。
#if __has_include("local_config.h")
#include "local_config.h"
#endif

// 以下默认值主要用于开发环境快速启动；正式部署建议复制
// local_config.example.h 为 local_config.h 后单独修改。
#ifndef AIGLASS_WIFI_SSID
#define AIGLASS_WIFI_SSID "call"
#endif

#ifndef AIGLASS_WIFI_PASSWORD
#define AIGLASS_WIFI_PASSWORD "my12345678"
#endif

#ifndef AIGLASS_UDP_HOST
#define AIGLASS_UDP_HOST "192.168.211.5"
#endif

#ifndef AIGLASS_DEVICE_TOKEN
#define AIGLASS_DEVICE_TOKEN "123456"
#endif

// XIAO ESP32S3 Sense 的硬件引脚映射：
// 摄像头使用并口 SCCB/VSYNC/HREF/PCLK，麦克风使用 PDM，
// 扬声器使用标准 I2S BCLK/WS/DOUT。
namespace Pins {
constexpr int kCamPwdn = -1;
constexpr int kCamReset = -1;
constexpr int kCamXclk = 10;
constexpr int kCamSiod = 40;
constexpr int kCamSioc = 39;
constexpr int kCamY9 = 48;
constexpr int kCamY8 = 11;
constexpr int kCamY7 = 12;
constexpr int kCamY6 = 14;
constexpr int kCamY5 = 16;
constexpr int kCamY4 = 18;
constexpr int kCamY3 = 17;
constexpr int kCamY2 = 15;
constexpr int kCamVsync = 38;
constexpr int kCamHref = 47;
constexpr int kCamPclk = 13;

constexpr int kMicClock = 42;
constexpr int kMicData = 41;
constexpr int kSpeakerBclk = D0;
constexpr int kSpeakerWs = D1;
constexpr int kSpeakerData = D2;
}  // namespace Pins

// 网络与设备身份。DEVICE_TOKEN 会被 hello 包带到服务端用于鉴权。
constexpr char WIFI_SSID[] = AIGLASS_WIFI_SSID;
constexpr char WIFI_PASSWORD[] = AIGLASS_WIFI_PASSWORD;

constexpr char UDP_HOST[] = AIGLASS_UDP_HOST;
constexpr uint16_t UDP_PORT = 8888;
constexpr uint16_t LOCAL_UDP_PORT = 3333;
constexpr uint16_t LOCAL_PLAYBACK_PORT = 3334;

constexpr char DEVICE_ID[] = "xiao-sense-01";
constexpr char DEVICE_TOKEN[] = AIGLASS_DEVICE_TOKEN;
constexpr char FIRMWARE_VERSION[] = "sense-udp-av-4";

// 摄像头默认参数。VGA 在带宽和识别精度之间比较均衡；
// kVideoIntervalMs 控制上行帧率，值越小越实时但越占 Wi-Fi。
constexpr framesize_t kFrameSize = FRAMESIZE_VGA;
constexpr int kFrameWidth = 640;
constexpr int kFrameHeight = 480;
constexpr uint32_t kVideoIntervalMs = 160;
constexpr int kJpegQuality = 14;

// 麦克风采集格式。每个 audio chunk 大约 20ms，服务端按固定长度拼接给 ASR。
constexpr uint32_t kAudioSampleRate = 22050;
constexpr uint8_t kAudioChannels = 1;
constexpr uint8_t kAudioBitsPerSample = 16;
constexpr size_t kAudioChunkSamples = 441;
constexpr size_t kAudioChunkBytes = kAudioChunkSamples * sizeof(int16_t);

// 扬声器播放格式。服务端把 AI 语音按 PCM16 mono 分片发回本地播放端口。
constexpr uint32_t kSpeakerDefaultSampleRate = 24000;
constexpr uint8_t kSpeakerChannels = 1;
constexpr uint8_t kSpeakerBitsPerSample = 16;
constexpr size_t kSpeakerMaxPayloadBytes = 960;
constexpr size_t kSpeakerStereoSamples =
    (kSpeakerMaxPayloadBytes / sizeof(int16_t)) * 2;
constexpr size_t kSpeakerTestBatchSamples = 128;
constexpr float kTwoPi = 6.28318530718f;

// 扬声器播放增益，Q8 定点数：256=1倍，512=2倍，768=3倍。
constexpr uint16_t kSpeakerGainQ8 = 768;

// 轻量音频清理和自动增益参数。ESP32 端计算资源有限，因此只做
// 去直流、噪声门、慢速 AGC 和软限幅，避免上传过小或削波的语音。
constexpr uint16_t kAudioNoiseFloor = 28;
constexpr uint16_t kAudioActivationPeak = 120;
constexpr uint16_t kAudioActivationAverage = 18;
constexpr uint16_t kAudioOutputFloor = 24;
constexpr uint16_t kAudioTargetPeak = 15000;
constexpr uint16_t kAudioMinGainQ8 = 256;
// 最大增益从 5 倍降至 2 倍：避免扬声器播报回声被过分放大，
// 减少噪声/回声导致 ASR 误识别成用户语音。
constexpr uint16_t kAudioMaxGainQ8 = 2 * 256;
constexpr uint16_t kAudioLimiterThreshold = 22000;
constexpr uint8_t kAudioAgcAttackShift = 3;
constexpr uint8_t kAudioAgcReleaseShift = 5;
constexpr uint8_t kAudioDcKeep = 248;

// Wi-Fi 和心跳时间参数。hello 周期不宜太长，否则服务端判断设备在线会滞后。
constexpr wifi_power_t kWiFiTxPower = WIFI_POWER_11dBm;
constexpr uint32_t kWifiRetryIntervalMs = 8000;
constexpr uint32_t kHelloIntervalMs = 3000;
constexpr uint32_t kStatsLogIntervalMs = 5000;
constexpr uint32_t kVideoQueuePauseMs = 4;

// UDP 协议布局。所有数据包都以 'X''S' + version + packetType 开头，
// 服务端据此区分 hello、音频、视频分片和 AI 下行音频。
constexpr uint8_t kPacketMagic0 = 'X';
constexpr uint8_t kPacketMagic1 = 'S';
constexpr uint8_t kPacketVersion = 1;
constexpr uint8_t kPacketHello = 1;
constexpr uint8_t kPacketAudio = 2;
constexpr uint8_t kPacketVideo = 3;
constexpr uint8_t kPacketAiAudio = 4;

constexpr size_t kMaxUdpPacketBytes = 1200;
constexpr size_t kHelloHeaderBytes = 4;
constexpr size_t kAudioHeaderBytes = 18;
constexpr size_t kVideoHeaderBytes = 26;
constexpr size_t kVideoChunkBytes = kMaxUdpPacketBytes - kVideoHeaderBytes;
constexpr size_t kMaxHelloPayloadBytes = 448;
constexpr size_t kMaxHelloPacketBytes = kHelloHeaderBytes + kMaxHelloPayloadBytes;

// 队列深度刻意保持较小：实时预览宁可丢旧帧，也不要堆积延迟。
constexpr size_t kVideoQueueDepth = 2;
constexpr size_t kAudioQueueDepth = 6;
constexpr size_t kSpeakerQueueDepth = 32;
