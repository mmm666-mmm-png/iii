#pragma once

#include <ESP_I2S.h>
#include <WiFiUdp.h>

#include "app_types.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"

// 跨 setup、网络传输和 FreeRTOS 任务共享的运行时对象。
// 这里只声明，实际定义在 app_state.cpp，避免多个编译单元重复定义。
extern WiFiUDP udpHello;
extern WiFiUDP udpVideo;
extern WiFiUDP udpAudio;
extern WiFiUDP udpPlayback;
extern I2SClass microphone;
extern I2SClass speaker;

extern QueueHandle_t videoQueue;
extern QueueHandle_t audioQueue;
extern QueueHandle_t speakerQueue;
extern SemaphoreHandle_t udpSendMutex;

// 网络/外设状态和统计计数。volatile 计数可能被多个任务更新，
// 当前仅用于日志展示，不参与严格同步决策。
extern bool udpReady;
extern bool speakerReady;
extern uint32_t lastVideoSentAtMs;
extern uint32_t lastHelloSentAtMs;
extern uint32_t lastWifiAttemptAtMs;
extern uint32_t lastStatsLogAtMs;
extern uint32_t videoSequence;
extern uint32_t audioSequence;
extern uint16_t lastAudioPeak;
extern uint16_t lastAudioAverage;
extern int16_t previousAudioInput;
extern int32_t previousAudioOutput;
extern uint32_t currentAudioGainQ8;
extern volatile uint32_t videoCapturedCount;
extern volatile uint32_t videoDroppedCount;
extern volatile uint32_t audioCapturedCount;
extern volatile uint32_t audioDroppedCount;
extern volatile uint32_t speakerReceivedCount;
extern volatile uint32_t speakerPlayedCount;
extern volatile uint32_t speakerDroppedCount;
extern uint32_t lastSpeakerSequence;
extern uint16_t lastSpeakerSampleRate;
