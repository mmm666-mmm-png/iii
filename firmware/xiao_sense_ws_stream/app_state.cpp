#include "app_state.h"

// UDP socket 分开保存，便于服务端和抓包时区分 hello、视频、音频与下行播放。
WiFiUDP udpHello;
WiFiUDP udpVideo;
WiFiUDP udpAudio;
WiFiUDP udpPlayback;

// I2SClass 同时覆盖 PDM 麦克风输入和标准 I2S 扬声器输出。
I2SClass microphone;
I2SClass speaker;

// FreeRTOS 队列/互斥锁在 setup 阶段创建，任务启动后全程复用。
QueueHandle_t videoQueue = nullptr;
QueueHandle_t audioQueue = nullptr;
QueueHandle_t speakerQueue = nullptr;
SemaphoreHandle_t udpSendMutex = nullptr;

// 运行状态、序号和统计数据。序号随成功发送递增，用于服务端检测丢包/乱序。
bool udpReady = false;
bool speakerReady = false;
uint32_t lastVideoSentAtMs = 0;
uint32_t lastHelloSentAtMs = 0;
uint32_t lastWifiAttemptAtMs = 0;
uint32_t lastStatsLogAtMs = 0;
uint32_t videoSequence = 0;
uint32_t audioSequence = 0;
uint16_t lastAudioPeak = 0;
uint16_t lastAudioAverage = 0;

// 轻量音频处理需要保存上一采样点和滤波状态，用于去直流与 AGC 平滑。
int16_t previousAudioInput = 0;
int32_t previousAudioOutput = 0;
uint32_t currentAudioGainQ8 = 256;
volatile uint32_t videoCapturedCount = 0;
volatile uint32_t videoDroppedCount = 0;
volatile uint32_t audioCapturedCount = 0;
volatile uint32_t audioDroppedCount = 0;
volatile uint32_t speakerReceivedCount = 0;
volatile uint32_t speakerPlayedCount = 0;
volatile uint32_t speakerDroppedCount = 0;
uint32_t lastSpeakerSequence = 0;
uint16_t lastSpeakerSampleRate = 0;
