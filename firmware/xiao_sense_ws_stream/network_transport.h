#pragma once

#include <WiFiUdp.h>

#include "app_types.h"

// 底层 UDP 发送入口，内部会检查 Wi-Fi 状态并用互斥锁串行化写入。
bool sendUdpDatagram(WiFiUDP& socket, const uint8_t* data, size_t length);
// 周期性发送 hello 包，让服务端知道设备在线、能力和下行播放端口。
void sendHelloIfDue();
// Wi-Fi 断开时停止 UDP socket 并清空积压队列。
void stopUdpIfNeeded();
// Wi-Fi 已连通但 UDP 未启动时打开本地端口。
void ensureUdpStarted();
// 非阻塞 Wi-Fi 重连，供 loop 周期调用。
void connectWiFiIfNeeded();
// 启动阶段阻塞等待 Wi-Fi 连上。
void waitForWiFi();
// 打包并发送一个麦克风 PCM 音频块。
bool sendAudioChunk(const AudioChunk& chunk);
// 将 JPEG 帧拆分为多个 UDP datagram 发送。
bool sendVideoFrame(camera_fb_t* frame, uint32_t timestampMs);
