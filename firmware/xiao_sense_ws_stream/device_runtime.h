#pragma once

#include "app_types.h"

// 初始化 XIAO ESP32S3 Sense 摄像头，输出 JPEG 帧供 UDP 分片上报。
bool initCamera();
// 初始化板载 PDM 麦克风，按 PCM16 mono 采样。
bool initMicrophone();
// 初始化 I2S 扬声器输出，用于播放服务端下发的 AI 语音。
bool initSpeaker();
// 根据下行音频采样率动态调整 I2S 输出格式。
bool ensureSpeakerFormat(uint16_t sampleRate);
// 开机提示音，用来确认功放/扬声器链路可用。
void playSpeakerStartupTone();
// 对麦克风 PCM 做去直流、噪声门、AGC、限幅，返回处理后的峰值。
uint16_t applyAudioProcessing(int16_t* samples, size_t sampleCount);
