#pragma once

// 本地网络配置（覆盖 app_config.h 的默认值，此文件不会提交到仓库）。
// 电脑当前连的 WiFi 热点为 "call"，电脑在该热点下的局域网 IP 是 192.168.174.5。
// 若更换网络后电脑 IP 变化，请同步修改 AIGLASS_UDP_HOST。
#define AIGLASS_UDP_HOST "192.168.174.5"
