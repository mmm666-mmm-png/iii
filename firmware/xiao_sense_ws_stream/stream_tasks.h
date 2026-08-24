#pragma once

// 创建所有采集、发送、接收和播放任务。
void startStreamingTasks();
// 周期性输出运行统计到串口，便于现场排查丢包和音频电平问题。
void logStreamingStats();
