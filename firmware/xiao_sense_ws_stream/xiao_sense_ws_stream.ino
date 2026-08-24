#include "app_config.h"
#include "app_state.h"
#include "device_runtime.h"
#include "network_transport.h"
#include "stream_tasks.h"

namespace {

// 初始化失败时统一走这里：先把错误打印到串口，给用户 5 秒观察时间，
// 然后重启设备。最后的死循环是为了防止 ESP.restart() 极少数情况下返回。
void restartAfterInitFailure(const char* message) {
  Serial.println(message);
  delay(5000);
  ESP.restart();
  while (true) {
    delay(1000);
  }
}

// 创建跨任务共享的 FreeRTOS 原语：
// - udpSendMutex：保护 UDP 发送，避免多个任务同时写 socket；
// - videoQueue/audioQueue：摄像头和麦克风采集线程把数据交给发送线程；
// - speakerQueue：服务端下发的 AI 语音交给播放线程。
void createRuntimePrimitives() {
  udpSendMutex = xSemaphoreCreateMutex();
  videoQueue = xQueueCreate(kVideoQueueDepth, sizeof(VideoFrameItem));
  audioQueue = xQueueCreate(kAudioQueueDepth, sizeof(AudioChunk));
  if (speakerReady) {
    speakerQueue = xQueueCreate(kSpeakerQueueDepth, sizeof(SpeakerChunk));
  }

  if (udpSendMutex == nullptr ||
      videoQueue == nullptr ||
      audioQueue == nullptr ||
      (speakerReady && speakerQueue == nullptr)) {
    restartAfterInitFailure("failed to create streaming queues");
  }
}

}  // namespace

void setup() {
  Serial.begin(115200);
  delay(1000);
  Serial.println();
  Serial.println("booting xiao sense udp streamer");
  Serial.printf("target udp %s:%u\n", UDP_HOST, UDP_PORT);

  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  WiFi.setTxPower(kWiFiTxPower);

  // 外设必须先初始化成功，否则后续任务拿到空句柄会反复失败。
  if (!initCamera()) {
    restartAfterInitFailure("camera setup failed, rebooting in 5 seconds");
  }

  if (!initMicrophone()) {
    restartAfterInitFailure("microphone setup failed, rebooting in 5 seconds");
  }

  speakerReady = initSpeaker();
  if (!speakerReady) {
    Serial.println("speaker output disabled; continuing without playback");
  } else {
    playSpeakerStartupTone();
  }

  createRuntimePrimitives();

  // Wi-Fi 连通后启动 UDP，再发送 hello，让服务端记录设备能力和回放端口。
  waitForWiFi();
  ensureUdpStarted();
  sendHelloIfDue();
  startStreamingTasks();
}

void loop() {
  // 主循环只做保活和统计；高频视频/音频采集都在 FreeRTOS 任务中运行。
  connectWiFiIfNeeded();
  ensureUdpStarted();
  sendHelloIfDue();
  logStreamingStats();
  delay(20);
}
