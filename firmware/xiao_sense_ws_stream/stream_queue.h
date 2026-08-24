#pragma once

#include "app_types.h"

// 队列入口统一封装“满了怎么办”的策略：
// 视频/音频/扬声器都优先保留最新数据，丢弃旧数据以控制实时延迟。
void enqueueVideoFrame(camera_fb_t* frame, uint32_t timestampMs);
void enqueueAudioChunk(const AudioChunk& chunk);
void enqueueSpeakerChunk(const SpeakerChunk& chunk);
