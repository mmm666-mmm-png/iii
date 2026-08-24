#pragma once

#include <Arduino.h>

// 将 32 位中间值夹到 PCM16 可表示范围，防止溢出回绕。
int16_t clampToInt16(int32_t value);
// 对过大的音频样本做软限幅，比硬截断更不容易产生刺耳失真。
int16_t applySoftLimiter(int32_t value);
// UDP 协议统一使用小端序，下面这些函数负责写入/读取整数字段。
void writeU16LE(uint8_t* dst, uint16_t value);
uint16_t readU16LE(const uint8_t* src);
void writeU32LE(uint8_t* dst, uint32_t value);
uint32_t readU32LE(const uint8_t* src);
