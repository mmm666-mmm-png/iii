#include "network_transport.h"

#include <string.h>

#include "app_config.h"
#include "app_state.h"
#include "app_utils.h"

namespace {

// 生成 hello JSON。它不是高频数据，因此直接用 snprintf 构造即可；
// 服务端通过此包登记设备 ID、视频/音频格式和扬声器播放端口。
String makeHelloMessage() {
  char message[kMaxHelloPayloadBytes];
  snprintf(
      message,
      sizeof(message),
      "{\"type\":\"hello\",\"deviceId\":\"%s\",\"token\":\"%s\","
      "\"firmware\":\"%s\","
      "\"video\":{\"format\":\"jpeg\",\"width\":%d,\"height\":%d,"
      "\"quality\":%d,\"intervalMs\":%lu},"
      "\"audio\":{\"sampleRate\":%lu,\"channels\":%u,"
      "\"bitsPerSample\":%u,\"chunkSamples\":%u},"
      "\"speaker\":{\"enabled\":%s,\"audioPort\":%u,"
      "\"sampleRate\":%lu,\"channels\":%u,\"bitsPerSample\":%u}}",
      DEVICE_ID,
      DEVICE_TOKEN,
      FIRMWARE_VERSION,
      kFrameWidth,
      kFrameHeight,
      kJpegQuality,
      static_cast<unsigned long>(kVideoIntervalMs),
      static_cast<unsigned long>(kAudioSampleRate),
      static_cast<unsigned int>(kAudioChannels),
      static_cast<unsigned int>(kAudioBitsPerSample),
      static_cast<unsigned int>(kAudioChunkSamples),
      speakerReady ? "true" : "false",
      static_cast<unsigned int>(LOCAL_PLAYBACK_PORT),
      static_cast<unsigned long>(kSpeakerDefaultSampleRate),
      static_cast<unsigned int>(kSpeakerChannels),
      static_cast<unsigned int>(kSpeakerBitsPerSample));
  return String(message);
}

}  // namespace

bool sendUdpDatagram(WiFiUDP& socket, const uint8_t* data, size_t length) {
  // 视频、麦克风上行和 hello 都可能由不同任务触发发送。
  // 用互斥锁串行化写 socket，避免包内容在底层交错。
  if (!udpReady || WiFi.status() != WL_CONNECTED || udpSendMutex == nullptr) {
    return false;
  }

  if (xSemaphoreTake(udpSendMutex, pdMS_TO_TICKS(20)) != pdTRUE) {
    return false;
  }

  bool sent = false;
  if (socket.beginPacket(UDP_HOST, UDP_PORT)) {
    const size_t written = socket.write(data, length);
    sent = socket.endPacket() == 1 && written == length;
  }

  xSemaphoreGive(udpSendMutex);
  return sent;
}

void sendHelloIfDue() {
  // 只有 UDP 和 Wi-Fi 都可用时才发送；周期内直接返回，避免刷屏。
  if (!udpReady || WiFi.status() != WL_CONNECTED) {
    return;
  }

  const uint32_t now = millis();
  if (now - lastHelloSentAtMs < kHelloIntervalMs) {
    return;
  }

  const String hello = makeHelloMessage();
  const size_t payloadLen = hello.length();
  if (kHelloHeaderBytes + payloadLen > kMaxHelloPacketBytes) {
    return;
  }

  // hello 包头：magic/version/type，后面紧跟 UTF-8 JSON。
  uint8_t packetBuffer[kMaxHelloPacketBytes];
  packetBuffer[0] = kPacketMagic0;
  packetBuffer[1] = kPacketMagic1;
  packetBuffer[2] = kPacketVersion;
  packetBuffer[3] = kPacketHello;
  memcpy(packetBuffer + kHelloHeaderBytes, hello.c_str(), payloadLen);

  if (sendUdpDatagram(
          udpHello, packetBuffer, kHelloHeaderBytes + payloadLen)) {
    lastHelloSentAtMs = now;
  }
}

void stopUdpIfNeeded() {
  // Wi-Fi 断开后关闭 socket，同时清理队列，避免重连后发送过期数据。
  if (!udpReady) {
    return;
  }

  udpHello.stop();
  udpVideo.stop();
  udpAudio.stop();
  udpPlayback.stop();
  udpReady = false;

  // 摄像头帧缓冲必须归还给驱动，否则 PSRAM 会被旧帧占住，
  // 后续 esp_camera_fb_get 可能一直拿不到新帧。
  VideoFrameItem pendingFrame = {};
  while (videoQueue != nullptr &&
         xQueueReceive(videoQueue, &pendingFrame, 0) == pdPASS) {
    if (pendingFrame.frame != nullptr) {
      esp_camera_fb_return(pendingFrame.frame);
    }
  }
  if (audioQueue != nullptr) {
    xQueueReset(audioQueue);
  }
  if (speakerQueue != nullptr) {
    xQueueReset(speakerQueue);
  }
  Serial.println("udp sender stopped");
}

void ensureUdpStarted() {
  // UDP 生命周期跟 Wi-Fi 状态绑定；断网时主动停止，联网后再重新 begin。
  if (WiFi.status() != WL_CONNECTED) {
    stopUdpIfNeeded();
    return;
  }

  if (udpReady) {
    return;
  }

  const bool helloReady = udpHello.begin(LOCAL_UDP_PORT);
  const bool videoReady = udpVideo.begin(0);
  const bool audioReady = udpAudio.begin(0);
  bool playbackReady = true;
  if (speakerReady) {
    playbackReady = udpPlayback.begin(LOCAL_PLAYBACK_PORT);
  }

  if (helloReady && videoReady && audioReady && playbackReady) {
    udpReady = true;
    Serial.printf("udp sender ready: %s:%u\n", UDP_HOST, UDP_PORT);
  } else {
    udpHello.stop();
    udpVideo.stop();
    udpAudio.stop();
    udpPlayback.stop();
    Serial.println("failed to start udp sender");
  }
}

void connectWiFiIfNeeded() {
  // 非阻塞重连：失败后按间隔重试，避免 loop 被 Wi-Fi 连接过程卡住。
  if (WiFi.status() == WL_CONNECTED) {
    return;
  }

  const uint32_t now = millis();
  if (now - lastWifiAttemptAtMs < kWifiRetryIntervalMs) {
    return;
  }

  lastWifiAttemptAtMs = now;
  Serial.printf("connecting to Wi-Fi SSID %s\n", WIFI_SSID);
  WiFi.disconnect();
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
}

void waitForWiFi() {
  // 启动阶段需要阻塞等待一次，保证后续 UDP 和任务启动时网络可用。
  connectWiFiIfNeeded();

  while (WiFi.status() != WL_CONNECTED) {
    delay(250);
    Serial.print(".");
    connectWiFiIfNeeded();
  }

  Serial.println();
  Serial.printf("Wi-Fi connected, IP=%s RSSI=%d\n",
                WiFi.localIP().toString().c_str(),
                WiFi.RSSI());
}

bool sendAudioChunk(const AudioChunk& chunk) {
  // 音频包格式：
  // 0..3  magic/version/type
  // 4..7  sequence
  // 8..11 timestampMs
  // 12..17 sampleRate/channels/bits/payloadLen
  // 18..  PCM16 payload
  uint8_t packetBuffer[kAudioHeaderBytes + kAudioChunkBytes];
  packetBuffer[0] = kPacketMagic0;
  packetBuffer[1] = kPacketMagic1;
  packetBuffer[2] = kPacketVersion;
  packetBuffer[3] = kPacketAudio;
  writeU32LE(packetBuffer + 4, audioSequence);
  writeU32LE(packetBuffer + 8, chunk.timestampMs);
  writeU16LE(packetBuffer + 12, static_cast<uint16_t>(kAudioSampleRate));
  packetBuffer[14] = kAudioChannels;
  packetBuffer[15] = kAudioBitsPerSample;
  writeU16LE(packetBuffer + 16, chunk.bytes);
  memcpy(packetBuffer + kAudioHeaderBytes, chunk.data, chunk.bytes);
  return sendUdpDatagram(
      udpAudio, packetBuffer, kAudioHeaderBytes + chunk.bytes);
}

bool sendVideoFrame(camera_fb_t* frame, uint32_t timestampMs) {
  if (frame == nullptr) {
    return false;
  }

  // JPEG 帧需要分片，确保每个 UDP datagram 小于较安全的 Wi-Fi MTU。
  // 服务端按 sequence + fragIndex/fragCount 重组整帧。
  const size_t fragmentCount =
      (frame->len + kVideoChunkBytes - 1) / kVideoChunkBytes;
  if (fragmentCount == 0 || fragmentCount > 0xffff) {
    return false;
  }

  const uint16_t frameWidth =
      frame->width > 0 ? frame->width : static_cast<uint16_t>(kFrameWidth);
  const uint16_t frameHeight =
      frame->height > 0 ? frame->height : static_cast<uint16_t>(kFrameHeight);
  uint8_t packetBuffer[kMaxUdpPacketBytes];

  for (uint16_t fragIndex = 0; fragIndex < fragmentCount; ++fragIndex) {
    // 视频包头包含帧尺寸、分片序号、总分片数和完整 JPEG 长度。
    const size_t offset = static_cast<size_t>(fragIndex) * kVideoChunkBytes;
    size_t chunkLen = frame->len - offset;
    if (chunkLen > kVideoChunkBytes) {
      chunkLen = kVideoChunkBytes;
    }

    packetBuffer[0] = kPacketMagic0;
    packetBuffer[1] = kPacketMagic1;
    packetBuffer[2] = kPacketVersion;
    packetBuffer[3] = kPacketVideo;
    writeU32LE(packetBuffer + 4, videoSequence);
    writeU32LE(packetBuffer + 8, timestampMs);
    writeU16LE(packetBuffer + 12, frameWidth);
    writeU16LE(packetBuffer + 14, frameHeight);
    writeU16LE(packetBuffer + 16, fragIndex);
    writeU16LE(packetBuffer + 18, static_cast<uint16_t>(fragmentCount));
    writeU32LE(packetBuffer + 20, static_cast<uint32_t>(frame->len));
    writeU16LE(packetBuffer + 24, static_cast<uint16_t>(chunkLen));
    memcpy(packetBuffer + kVideoHeaderBytes, frame->buf + offset, chunkLen);

    if (!sendUdpDatagram(udpVideo, packetBuffer, kVideoHeaderBytes + chunkLen)) {
      return false;
    }

    if ((fragIndex & 0x7) == 0x7) {
      taskYIELD();
    }
  }

  return true;
}
