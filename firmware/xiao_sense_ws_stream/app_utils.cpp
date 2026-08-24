#include "app_utils.h"

#include "app_config.h"

int16_t clampToInt16(int32_t value) {
  // PCM16 的合法范围是 [-32768, 32767]。
  if (value > 32767) {
    return 32767;
  }
  if (value < -32768) {
    return -32768;
  }
  return static_cast<int16_t>(value);
}

int16_t applySoftLimiter(int32_t value) {
  // 超过阈值的部分只保留 1/4，听感上比直接截断自然。
  const bool negative = value < 0;
  int32_t magnitude = negative ? -value : value;
  if (magnitude > kAudioLimiterThreshold) {
    int32_t excess = magnitude - kAudioLimiterThreshold;
    magnitude = kAudioLimiterThreshold + (excess >> 2);
    if (magnitude > 32767) {
      magnitude = 32767;
    }
  }
  return negative ? static_cast<int16_t>(-magnitude)
                  : static_cast<int16_t>(magnitude);
}

void writeU16LE(uint8_t* dst, uint16_t value) {
  // 低字节在前，便于 Go/Python 服务端按协议解析。
  dst[0] = static_cast<uint8_t>(value & 0xff);
  dst[1] = static_cast<uint8_t>((value >> 8) & 0xff);
}

uint16_t readU16LE(const uint8_t* src) {
  // 从网络包中读取小端 16 位整数。
  return static_cast<uint16_t>(src[0]) |
         (static_cast<uint16_t>(src[1]) << 8);
}

void writeU32LE(uint8_t* dst, uint32_t value) {
  // 写入小端 32 位整数，主要用于序号和时间戳。
  dst[0] = static_cast<uint8_t>(value & 0xff);
  dst[1] = static_cast<uint8_t>((value >> 8) & 0xff);
  dst[2] = static_cast<uint8_t>((value >> 16) & 0xff);
  dst[3] = static_cast<uint8_t>((value >> 24) & 0xff);
}

uint32_t readU32LE(const uint8_t* src) {
  // 从网络包中读取小端 32 位整数。
  return static_cast<uint32_t>(src[0]) |
         (static_cast<uint32_t>(src[1]) << 8) |
         (static_cast<uint32_t>(src[2]) << 16) |
         (static_cast<uint32_t>(src[3]) << 24);
}
