#include "stream_tasks.h"

#include <string.h>

#include "app_config.h"
#include "app_state.h"
#include "app_utils.h"
#include "device_runtime.h"
#include "network_transport.h"
#include "stream_queue.h"

namespace {

void taskVideoCapture(void*) {
  // 摄像头采集任务：只负责取最新 JPEG 帧并入队，不做网络发送。
  for (;;) {
    if (!udpReady || WiFi.status() != WL_CONNECTED) {
      vTaskDelay(pdMS_TO_TICKS(20));
      continue;
    }

    if (videoQueue != nullptr && uxQueueSpacesAvailable(videoQueue) == 0) {
      vTaskDelay(pdMS_TO_TICKS(kVideoQueuePauseMs));
      continue;
    }

    camera_fb_t* frame = esp_camera_fb_get();
    if (frame == nullptr) {
      vTaskDelay(pdMS_TO_TICKS(2));
      continue;
    }

    ++videoCapturedCount;
    if (frame->format != PIXFORMAT_JPEG) {
      esp_camera_fb_return(frame);
      vTaskDelay(pdMS_TO_TICKS(1));
      continue;
    }

    enqueueVideoFrame(frame, millis());
  }
}

void taskVideoSend(void*) {
  // 视频发送任务：从队列取帧、限速、UDP 分片发送，然后归还 frame buffer。
  TickType_t lastSendTick = 0;

  for (;;) {
    VideoFrameItem item = {};
    if (videoQueue == nullptr ||
        xQueueReceive(videoQueue, &item, pdMS_TO_TICKS(50)) != pdPASS) {
      continue;
    }

    if (item.frame == nullptr) {
      continue;
    }

    if (!udpReady || WiFi.status() != WL_CONNECTED) {
      esp_camera_fb_return(item.frame);
      continue;
    }

    // 每次发送前先清空队列，只保留最新帧，保证浏览器看到的是实时画面。
    VideoFrameItem latest = {};
    while (videoQueue != nullptr && xQueueReceive(videoQueue, &latest, 0) == pdPASS) {
      if (item.frame != nullptr) {
        esp_camera_fb_return(item.frame);
        ++videoDroppedCount;
      }
      item = latest;
    }

    if (lastSendTick != 0) {
      // 根据 kVideoIntervalMs 做发送限速，控制 Wi-Fi 带宽和服务端解码压力。
      const TickType_t nowTick = xTaskGetTickCount();
      const uint32_t elapsedMs =
          static_cast<uint32_t>((nowTick - lastSendTick) * portTICK_PERIOD_MS);
      if (elapsedMs < kVideoIntervalMs) {
        vTaskDelay(pdMS_TO_TICKS(kVideoIntervalMs - elapsedMs));
      }
    }

    if (sendVideoFrame(item.frame, item.timestampMs)) {
      lastVideoSentAtMs = millis();
      ++videoSequence;
      lastSendTick = xTaskGetTickCount();
    }

    esp_camera_fb_return(item.frame);
  }
}

void taskAudioCapture(void*) {
  // 麦克风采集任务：按固定大小凑满一个 PCM chunk，再做轻量处理并入队。
  AudioChunk chunk = {};
  size_t fillBytes = 0;

  for (;;) {
    if (!udpReady || WiFi.status() != WL_CONNECTED) {
      fillBytes = 0;
      vTaskDelay(pdMS_TO_TICKS(10));
      continue;
    }

    const size_t readNow = microphone.readBytes(
        reinterpret_cast<char*>(chunk.data + fillBytes),
        kAudioChunkBytes - fillBytes);
    if (readNow == 0) {
      vTaskDelay(pdMS_TO_TICKS(1));
      continue;
    }

    fillBytes += readNow;
    if (fillBytes < kAudioChunkBytes) {
      continue;
    }

    chunk.timestampMs = millis();
    chunk.bytes = static_cast<uint16_t>(kAudioChunkBytes);
    chunk.peak =
        applyAudioProcessing(reinterpret_cast<int16_t*>(chunk.data),
                             kAudioChunkSamples);
    lastAudioPeak = chunk.peak;
    ++audioCapturedCount;
    enqueueAudioChunk(chunk);
    fillBytes = 0;
  }
}

void taskAudioSend(void*) {
  // 音频发送任务：把已处理 PCM chunk 打包成 UDP 包发给服务端。
  for (;;) {
    AudioChunk chunk = {};
    if (audioQueue == nullptr ||
        xQueueReceive(audioQueue, &chunk, pdMS_TO_TICKS(20)) != pdPASS) {
      continue;
    }

    if (!udpReady || WiFi.status() != WL_CONNECTED) {
      continue;
    }

    if (sendAudioChunk(chunk)) {
      ++audioSequence;
    }
  }
}

void taskSpeakerReceive(void*) {
  // 扬声器接收任务：监听本地播放端口，接收服务端下发的 AI 语音分片。
  uint8_t packetBuffer[kAudioHeaderBytes + kSpeakerMaxPayloadBytes];

  for (;;) {
    if (!speakerReady || !udpReady || WiFi.status() != WL_CONNECTED) {
      vTaskDelay(pdMS_TO_TICKS(10));
      continue;
    }

    const int packetSize = udpPlayback.parsePacket();
    if (packetSize <= 0) {
      vTaskDelay(pdMS_TO_TICKS(2));
      continue;
    }

    if (packetSize < static_cast<int>(kAudioHeaderBytes) ||
        packetSize > static_cast<int>(sizeof(packetBuffer))) {
      while (udpPlayback.available() > 0) {
        udpPlayback.read();
      }
      continue;
    }

    const int readBytes = udpPlayback.read(packetBuffer, sizeof(packetBuffer));
    if (readBytes != packetSize) {
      continue;
    }

    // 包头校验：magic/version/type 不匹配说明不是本协议的 AI 音频。
    if (packetBuffer[0] != kPacketMagic0 ||
        packetBuffer[1] != kPacketMagic1 ||
        packetBuffer[2] != kPacketVersion ||
        packetBuffer[3] != kPacketAiAudio) {
      continue;
    }

    const uint16_t payloadLen = readU16LE(packetBuffer + 16);
    if (payloadLen == 0 ||
        payloadLen > kSpeakerMaxPayloadBytes ||
        static_cast<size_t>(readBytes) != kAudioHeaderBytes + payloadLen) {
      continue;
    }

    // 解析出播放参数和 PCM payload，交给播放任务真正写 I2S。
    SpeakerChunk chunk = {};
    chunk.sequence = readU32LE(packetBuffer + 4);
    chunk.timestampMs = readU32LE(packetBuffer + 8);
    chunk.sampleRate = readU16LE(packetBuffer + 12);
    chunk.channels = packetBuffer[14];
    chunk.bitsPerSample = packetBuffer[15];
    chunk.bytes = payloadLen;
    memcpy(chunk.data, packetBuffer + kAudioHeaderBytes, payloadLen);

    ++speakerReceivedCount;
    enqueueSpeakerChunk(chunk);
  }
}

void taskSpeakerPlay(void*) {
  // 扬声器播放任务：把 mono PCM16 扩展为 stereo，并按需要调整 I2S 采样率。
  int16_t stereoBuffer[kSpeakerStereoSamples];

  for (;;) {
    SpeakerChunk chunk = {};
    if (speakerQueue == nullptr ||
        xQueueReceive(speakerQueue, &chunk, pdMS_TO_TICKS(50)) != pdPASS) {
      continue;
    }

    if (!speakerReady ||
        chunk.bitsPerSample != 16 ||
        chunk.channels != 1 ||
        chunk.bytes == 0 ||
        !ensureSpeakerFormat(chunk.sampleRate)) {
      continue;
    }

    const size_t sampleCount = chunk.bytes / sizeof(int16_t);
    if (sampleCount * 2 > kSpeakerStereoSamples) {
      ++speakerDroppedCount;
      continue;
    }

    for (size_t i = 0; i < sampleCount; ++i) {
      // 下行 payload 是小端 PCM16 mono；设备 I2S 输出为左右声道相同的 stereo。
      int32_t raw = static_cast<int16_t>(
          static_cast<uint16_t>(chunk.data[i * 2]) |
          (static_cast<uint16_t>(chunk.data[i * 2 + 1]) << 8));
      raw = (raw * kSpeakerGainQ8) >> 8;
      if (raw > 32767) raw = 32767;
      if (raw < -32768) raw = -32768;
      const int16_t sample = static_cast<int16_t>(raw);
      stereoBuffer[i * 2] = sample;
      stereoBuffer[i * 2 + 1] = sample;
    }

    const size_t bytesToWrite = sampleCount * 2 * sizeof(int16_t);
    const size_t written =
        speaker.write(reinterpret_cast<uint8_t*>(stereoBuffer), bytesToWrite);
    if (written == bytesToWrite) {
      lastSpeakerSequence = chunk.sequence;
      ++speakerPlayedCount;
    } else {
      ++speakerDroppedCount;
    }
  }
}

}  // namespace

void startStreamingTasks() {
  // 采集和发送分离：Wi-Fi 短时阻塞不会长时间卡住摄像头/麦克风读取。
  // 摄像头任务放在 core 1，音频与播放任务放在 core 0，减少互相抢占。
  xTaskCreatePinnedToCore(taskVideoCapture, "video_cap", 6144, nullptr, 3, nullptr, 1);
  xTaskCreatePinnedToCore(taskVideoSend, "video_send", 8192, nullptr, 3, nullptr, 1);
  xTaskCreatePinnedToCore(taskAudioCapture, "audio_cap", 4096, nullptr, 2, nullptr, 0);
  xTaskCreatePinnedToCore(taskAudioSend, "audio_send", 4096, nullptr, 3, nullptr, 0);
  if (speakerReady) {
    xTaskCreatePinnedToCore(taskSpeakerReceive, "speaker_rx", 4096, nullptr, 2, nullptr, 0);
    xTaskCreatePinnedToCore(taskSpeakerPlay, "speaker_play", 4096, nullptr, 2, nullptr, 0);
  }
}

void logStreamingStats() {
  // 串口统计每 5 秒输出一次，用于判断：
  // - videoDrop/audioDrop 是否过高；
  // - speakerRx 与 speakerPlay 是否匹配；
  // - audioPeak/audioAvg/audioGainQ8 是否说明麦克风音量异常。
  const uint32_t now = millis();
  if (now - lastStatsLogAtMs < kStatsLogIntervalMs) {
    return;
  }

  lastStatsLogAtMs = now;
  Serial.printf(
      "stats wifi=%d udp=%d videoCap=%lu videoSent=%lu videoDrop=%lu "
      "audioCap=%lu audioSent=%lu audioDrop=%lu speakerRx=%lu "
      "speakerPlay=%lu speakerDrop=%lu speakerSeq=%lu audioPeak=%u "
      "audioAvg=%u audioGainQ8=%lu\n",
      static_cast<int>(WiFi.status()),
      udpReady ? 1 : 0,
      static_cast<unsigned long>(videoCapturedCount),
      static_cast<unsigned long>(videoSequence),
      static_cast<unsigned long>(videoDroppedCount),
      static_cast<unsigned long>(audioCapturedCount),
      static_cast<unsigned long>(audioSequence),
      static_cast<unsigned long>(audioDroppedCount),
      static_cast<unsigned long>(speakerReceivedCount),
      static_cast<unsigned long>(speakerPlayedCount),
      static_cast<unsigned long>(speakerDroppedCount),
      static_cast<unsigned long>(lastSpeakerSequence),
      static_cast<unsigned int>(lastAudioPeak),
      static_cast<unsigned int>(lastAudioAverage),
      static_cast<unsigned long>(currentAudioGainQ8));
}
