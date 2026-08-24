#include "device_runtime.h"

#include <math.h>
#include <string.h>

#include "app_config.h"
#include "app_state.h"
#include "app_utils.h"

bool initCamera() {
  // camera_config_t 必须逐项填写 XIAO Sense 的摄像头并口引脚和 JPEG 参数。
  camera_config_t config = {};
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer = LEDC_TIMER_0;
  config.pin_d0 = Pins::kCamY2;
  config.pin_d1 = Pins::kCamY3;
  config.pin_d2 = Pins::kCamY4;
  config.pin_d3 = Pins::kCamY5;
  config.pin_d4 = Pins::kCamY6;
  config.pin_d5 = Pins::kCamY7;
  config.pin_d6 = Pins::kCamY8;
  config.pin_d7 = Pins::kCamY9;
  config.pin_xclk = Pins::kCamXclk;
  config.pin_pclk = Pins::kCamPclk;
  config.pin_vsync = Pins::kCamVsync;
  config.pin_href = Pins::kCamHref;
  config.pin_sccb_sda = Pins::kCamSiod;
  config.pin_sccb_scl = Pins::kCamSioc;
  config.pin_pwdn = Pins::kCamPwdn;
  config.pin_reset = Pins::kCamReset;
  config.xclk_freq_hz = 20000000;
  config.pixel_format = PIXFORMAT_JPEG;
  config.frame_size = kFrameSize;
  config.jpeg_quality = kJpegQuality;
  config.grab_mode = CAMERA_GRAB_LATEST;
  config.fb_location = CAMERA_FB_IN_PSRAM;
  config.fb_count = 2;

  const esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    Serial.printf("camera init failed: 0x%x\n", err);
    return false;
  }

  sensor_t* sensor = esp_camera_sensor_get();
  if (sensor != nullptr) {
    // 传感器参数偏向“稳定实时预览”，不是追求最高画质。
    // 导航场景更需要曝光/白平衡稳定，避免模型输入大幅闪烁。
    sensor->set_framesize(sensor, kFrameSize);
    sensor->set_quality(sensor, kJpegQuality);
    sensor->set_brightness(sensor, 0);
    sensor->set_contrast(sensor, 1);
    sensor->set_saturation(sensor, 1);
    sensor->set_gain_ctrl(sensor, 1);
    sensor->set_exposure_ctrl(sensor, 1);
    sensor->set_whitebal(sensor, 1);
    sensor->set_awb_gain(sensor, 1);
    if (sensor->set_aec2 != nullptr) {
      sensor->set_aec2(sensor, 1);
    }
    if (sensor->set_raw_gma != nullptr) {
      sensor->set_raw_gma(sensor, 1);
    }
    if (sensor->set_lenc != nullptr) {
      sensor->set_lenc(sensor, 1);
    }
    if (sensor->set_denoise != nullptr) {
      sensor->set_denoise(sensor, 0);
    }
    if (sensor->set_sharpness != nullptr) {
      sensor->set_sharpness(sensor, 2);
    }
    if (sensor->id.PID == OV3660_PID) {
      sensor->set_vflip(sensor, 1);
    }
  }

  Serial.println("camera ready");
  return true;
}

bool initMicrophone() {
  // PDM 麦克风只需要时钟和数据脚；后续 readBytes 会按固定 chunk 读取。
  microphone.setPinsPdmRx(Pins::kMicClock, Pins::kMicData);

  if (!microphone.begin(I2S_MODE_PDM_RX,
                        kAudioSampleRate,
                        I2S_DATA_BIT_WIDTH_16BIT,
                        I2S_SLOT_MODE_MONO)) {
    Serial.println("failed to start PDM microphone");
    return false;
  }

  microphone.setTimeout(20);
  Serial.println("microphone ready");
  return true;
}

bool initSpeaker() {
  // 扬声器使用标准 I2S TX。这里先按默认采样率启动，
  // 真正收到下行音频时可通过 ensureSpeakerFormat 重新配置。
  speaker.setPins(Pins::kSpeakerBclk, Pins::kSpeakerWs, Pins::kSpeakerData);

  if (!speaker.begin(I2S_MODE_STD,
                     kSpeakerDefaultSampleRate,
                     I2S_DATA_BIT_WIDTH_16BIT,
                     I2S_SLOT_MODE_STEREO)) {
    Serial.println("failed to start speaker output");
    return false;
  }

  speaker.setTimeout(20);
  lastSpeakerSampleRate = static_cast<uint16_t>(kSpeakerDefaultSampleRate);
  Serial.printf("speaker ready bclk=%d ws=%d dout=%d\n",
                Pins::kSpeakerBclk,
                Pins::kSpeakerWs,
                Pins::kSpeakerData);
  return true;
}

bool ensureSpeakerFormat(uint16_t sampleRate) {
  // 允许服务端不填采样率；此时回退到默认 24kHz。
  const uint16_t targetRate =
      sampleRate > 0 ? sampleRate
                     : static_cast<uint16_t>(kSpeakerDefaultSampleRate);
  if (lastSpeakerSampleRate == targetRate) {
    return true;
  }

  if (!speaker.configureTX(
          targetRate, I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_STEREO)) {
    Serial.printf("speaker reconfigure failed: %u Hz\n",
                  static_cast<unsigned int>(targetRate));
    return false;
  }

  lastSpeakerSampleRate = targetRate;
  return true;
}

void playSpeakerStartupTone() {
  // 播放一个很短的 880Hz 提示音，便于现场确认扬声器初始化成功。
  if (!speakerReady ||
      !ensureSpeakerFormat(static_cast<uint16_t>(kSpeakerDefaultSampleRate))) {
    return;
  }

  constexpr float kToneHz = 880.0f;
  constexpr uint16_t kToneDurationMs = 120;
  constexpr int16_t kAmplitude = 7000;

  int16_t buffer[kSpeakerTestBatchSamples * 2];
  size_t totalSamples = (kSpeakerDefaultSampleRate * kToneDurationMs) / 1000;
  float phase = 0.0f;
  const float phaseStep =
      kTwoPi * kToneHz / static_cast<float>(kSpeakerDefaultSampleRate);

  while (totalSamples > 0) {
    size_t batchSamples = totalSamples;
    if (batchSamples > kSpeakerTestBatchSamples) {
      batchSamples = kSpeakerTestBatchSamples;
    }

    for (size_t i = 0; i < batchSamples; ++i) {
      const int16_t sample = static_cast<int16_t>(sinf(phase) * kAmplitude);
      buffer[i * 2] = sample;
      buffer[i * 2 + 1] = sample;
      phase += phaseStep;
      if (phase >= kTwoPi) {
        phase -= kTwoPi;
      }
    }

    const size_t bytesToWrite = batchSamples * 2 * sizeof(int16_t);
    const size_t written =
        speaker.write(reinterpret_cast<uint8_t*>(buffer), bytesToWrite);
    if (written != bytesToWrite) {
      Serial.printf("speaker startup tone short write: %u/%u\n",
                    static_cast<unsigned int>(written),
                    static_cast<unsigned int>(bytesToWrite));
      return;
    }

    totalSamples -= batchSamples;
  }

  Serial.println("speaker startup tone played");
}

uint16_t applyAudioProcessing(int16_t* samples, size_t sampleCount) {
  // 采集端 DSP 刻意保持轻量：先做去直流和噪声门，再做慢速 AGC，
  // 最后软限幅。这样既能提高 ASR 输入音量，也不会让 ESP32 负担过重。
  uint16_t filteredPeak = 0;
  uint32_t filteredMagnitudeSum = 0;
  for (size_t i = 0; i < sampleCount; ++i) {
    // 一阶高通滤波：去掉麦克风直流偏移，避免静音时波形整体偏移。
    const int32_t input = samples[i];
    int32_t filtered =
        input - previousAudioInput + ((previousAudioOutput * kAudioDcKeep) >> 8);
    previousAudioInput = static_cast<int16_t>(input);
    previousAudioOutput = filtered;

    if (filtered < kAudioNoiseFloor && filtered > -kAudioNoiseFloor) {
      filtered = 0;
    }

    samples[i] = clampToInt16(filtered);

    int32_t magnitude = samples[i];
    if (magnitude < 0) {
      magnitude = -magnitude;
    }
    filteredMagnitudeSum += static_cast<uint32_t>(magnitude);
    if (magnitude > filteredPeak) {
      filteredPeak = static_cast<uint16_t>(magnitude);
    }
  }

  // 如果峰值和平均能量都很低，认为是背景噪声，直接输出静音并释放增益。
  lastAudioAverage = static_cast<uint16_t>(filteredMagnitudeSum / sampleCount);

  if (filteredPeak < kAudioActivationPeak &&
      lastAudioAverage < kAudioActivationAverage) {
    memset(samples, 0, sampleCount * sizeof(int16_t));
    if (currentAudioGainQ8 > 256) {
      currentAudioGainQ8 -= (currentAudioGainQ8 - 256 + 3) >> 2;
    }
    lastAudioAverage = 0;
    return 0;
  }

  uint32_t controlLevel = filteredPeak;
  const uint32_t averageDrivenLevel =
      static_cast<uint32_t>(lastAudioAverage) * 3;
  if (averageDrivenLevel > controlLevel) {
    controlLevel = averageDrivenLevel;
  }
  if (controlLevel == 0) {
    controlLevel = 1;
  }

  // 根据当前能量估算目标增益，随后用 attack/release 平滑，避免音量忽大忽小。
  uint32_t targetGainQ8 =
      (static_cast<uint32_t>(kAudioTargetPeak) << 8) / controlLevel;
  if (targetGainQ8 < kAudioMinGainQ8) {
    targetGainQ8 = kAudioMinGainQ8;
  } else if (targetGainQ8 > kAudioMaxGainQ8) {
    targetGainQ8 = kAudioMaxGainQ8;
  }

  if (targetGainQ8 > currentAudioGainQ8) {
    currentAudioGainQ8 +=
        (targetGainQ8 - currentAudioGainQ8 +
         ((1u << kAudioAgcAttackShift) - 1)) >>
        kAudioAgcAttackShift;
  } else if (targetGainQ8 < currentAudioGainQ8) {
    currentAudioGainQ8 -=
        (currentAudioGainQ8 - targetGainQ8 +
         ((1u << kAudioAgcReleaseShift) - 1)) >>
        kAudioAgcReleaseShift;
  }

  uint16_t outputPeak = 0;
  uint32_t outputMagnitudeSum = 0;
  for (size_t i = 0; i < sampleCount; ++i) {
    // 应用 AGC 增益并做二次小噪声门，最后限幅到 int16。
    int32_t amplified = (static_cast<int32_t>(samples[i]) *
                         static_cast<int32_t>(currentAudioGainQ8)) >>
                        8;
    if (amplified < kAudioOutputFloor && amplified > -kAudioOutputFloor) {
      amplified = 0;
    }

    samples[i] = applySoftLimiter(amplified);

    int32_t magnitude = samples[i];
    if (magnitude < 0) {
      magnitude = -magnitude;
    }
    outputMagnitudeSum += static_cast<uint32_t>(magnitude);
    if (magnitude > outputPeak) {
      outputPeak = static_cast<uint16_t>(magnitude);
    }
  }

  lastAudioAverage = static_cast<uint16_t>(outputMagnitudeSum / sampleCount);
  return outputPeak;
}
