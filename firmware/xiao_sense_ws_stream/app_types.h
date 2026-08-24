#pragma once

#include "app_config.h"

// 摄像头帧队列项。frame 指针必须最终调用 esp_camera_fb_return 归还给驱动。
struct VideoFrameItem {
  camera_fb_t* frame;
  uint32_t timestampMs;
};

// 麦克风上行音频块。peak 用于串口统计和判断输入音量是否正常。
struct AudioChunk {
  uint32_t timestampMs;
  uint16_t peak;
  uint16_t bytes;
  uint8_t data[kAudioChunkBytes];
};

// 服务端下发给设备扬声器播放的 AI 语音块。
// 当前只接受 PCM16 mono，播放前会扩展为 I2S stereo。
struct SpeakerChunk {
  uint32_t sequence;
  uint32_t timestampMs;
  uint16_t sampleRate;
  uint16_t bytes;
  uint8_t channels;
  uint8_t bitsPerSample;
  uint8_t data[kSpeakerMaxPayloadBytes];
};
