#include "stream_queue.h"

#include "app_state.h"

void enqueueVideoFrame(camera_fb_t* frame, uint32_t timestampMs) {
  // 摄像头缓冲来自驱动池；无论入队成功与否，都必须保证最终归还。
  if (frame == nullptr || videoQueue == nullptr) {
    if (frame != nullptr) {
      esp_camera_fb_return(frame);
    }
    return;
  }

  VideoFrameItem item = {frame, timestampMs};
  if (xQueueSend(videoQueue, &item, 0) == pdPASS) {
    return;
  }

  // 实时预览优先新鲜画面。发送端跟不上时丢最旧帧，
  // 防止几秒前的画面排队后才被展示。
  VideoFrameItem dropped = {};
  if (xQueueReceive(videoQueue, &dropped, 0) == pdPASS &&
      dropped.frame != nullptr) {
    esp_camera_fb_return(dropped.frame);
    ++videoDroppedCount;
  }

  if (xQueueSend(videoQueue, &item, 0) != pdPASS) {
    esp_camera_fb_return(frame);
    ++videoDroppedCount;
  }
}

void enqueueAudioChunk(const AudioChunk& chunk) {
  // 音频也不允许无限积压；队列满时丢旧块，降低 ASR 延迟。
  if (audioQueue == nullptr) {
    return;
  }

  if (xQueueSend(audioQueue, &chunk, 0) == pdPASS) {
    return;
  }

  AudioChunk dropped = {};
  if (xQueueReceive(audioQueue, &dropped, 0) == pdPASS) {
    ++audioDroppedCount;
  }

  if (xQueueSend(audioQueue, &chunk, 0) != pdPASS) {
    ++audioDroppedCount;
  }
}

void enqueueSpeakerChunk(const SpeakerChunk& chunk) {
  // AI 下行语音如果积压过多会导致播报明显滞后，因此队列满时同样丢旧块。
  if (speakerQueue == nullptr) {
    return;
  }

  if (xQueueSend(speakerQueue, &chunk, 0) == pdPASS) {
    return;
  }

  SpeakerChunk dropped = {};
  if (xQueueReceive(speakerQueue, &dropped, 0) == pdPASS) {
    ++speakerDroppedCount;
  }

  if (xQueueSend(speakerQueue, &chunk, 0) != pdPASS) {
    ++speakerDroppedCount;
  }
}
