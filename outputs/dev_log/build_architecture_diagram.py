# -*- coding: utf-8 -*-
"""绘制《开发日志》附录 A 佐证材料 —— 系统总体架构图

输出：outputs/verification/系统架构图.png
依赖：matplotlib（已装 3.11.1）
"""
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

# 中文字体
import matplotlib.font_manager as fm

_FONT_CANDIDATES = ["Microsoft YaHei", "SimHei", "Arial Unicode MS"]
_avail = {f.name for f in fm.fontManager.ttflist}
for name in _FONT_CANDIDATES:
    if name in _avail:
        plt.rcParams["font.sans-serif"] = [name]
        break
plt.rcParams["axes.unicode_minus"] = False

BASE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(os.path.dirname(BASE), "verification")
os.makedirs(OUT_DIR, exist_ok=True)
OUT = os.path.join(OUT_DIR, "系统架构图.png")

# ---------- 配色 ----------
C_USER = "#9AA0A6"      # 用户层 - 灰
C_ESP32 = "#4FC3F7"     # 设备端 - 浅蓝
C_GO = "#1565C0"        # Go 后端 - 深蓝（核心）
C_PY = "#2E7D32"        # Python Worker - 绿
C_VUE = "#EF6C00"       # Vue 前端 - 橙
C_CLOUD = "#6A1B9A"     # 云服务 - 紫
C_BG = "#F5F7FA"

FIG_W, FIG_H = 15.2, 10.2
fig, ax = plt.subplots(figsize=(FIG_W, FIG_H), dpi=200)
ax.set_xlim(0, FIG_W)
ax.set_ylim(0, FIG_H)
ax.axis("off")
fig.patch.set_facecolor(C_BG)


def box(x, y, w, h, text, fc, ec, tc="white", fs=13, radius=0.12, sub=None):
    """x,y 为左下角坐标；sub 为子项列表 [(名称, 是否强调), ...]"""
    p = FancyBboxPatch(
        (x, y), w, h,
        boxstyle=f"round,pad=0.06,rounding_size={radius}",
        linewidth=1.6, edgecolor=ec, facecolor=fc, zorder=3,
    )
    ax.add_patch(p)
    if sub is None:
        ax.text(x + w / 2, y + h / 2, text, ha="center", va="center",
                fontsize=fs, color=tc, fontweight="bold", zorder=4)
    else:
        ax.text(x + w / 2, y + h - 0.32, text, ha="center", va="center",
                fontsize=fs, color=tc, fontweight="bold", zorder=4)
        n = len(sub)
        inner_h = h - 0.72
        for i, (sub_name, emph) in enumerate(sub):
            yy = y + 0.3 + (n - 1 - i) * (inner_h / n)
            if emph:
                ax.text(x + w / 2, yy + inner_h / n / 2, sub_name,
                        ha="center", va="center", fontsize=9.6, color="#FFFDE7",
                        fontweight="bold", zorder=4,
                        bbox=dict(boxstyle="round,pad=0.18", fc=ec, ec="none"))
            else:
                ax.text(x + w / 2, yy + inner_h / n / 2, sub_name,
                        ha="center", va="center", fontsize=9.2, color="white",
                        zorder=4)


def arrow(x1, y1, x2, y2, label="", color="#37474F", style="-|>", lw=2.2,
          fs=10, label_dx=0, label_dy=0.12, connectionstyle="arc3,rad=0.0"):
    a = FancyArrowPatch(
        (x1, y1), (x2, y2), arrowstyle=style, mutation_scale=22,
        linewidth=lw, color=color, zorder=2,
        connectionstyle=connectionstyle,
    )
    ax.add_patch(a)
    if label:
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        ax.text(mx + label_dx, my + label_dy, label, ha="center", va="bottom",
                fontsize=fs, color="#263238", zorder=5,
                bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="#B0BEC5",
                          lw=0.8))


# ================= 布局（自顶向下） =================
# 1) 用户层
box(3.2, 8.9, 8.8, 1.0, "用户（视障人士）", C_USER, "#5F6368", sub=None)
ax.text(3.2, 9.0, "", fontsize=1)

# 2) 设备端 ESP32
esp_x, esp_y, esp_w, esp_h = 1.4, 6.7, 12.4, 1.7
box(esp_x, esp_y, esp_w, esp_h, "设备端 ESP32 智能眼镜（FreeRTOS，XIAO Sense）", C_ESP32, "#0288D1",
    sub=[
        ("摄像头：VGA JPEG 帧采集", True),
        ("麦克风：PCM 音频采集", False),
        ("扬声器：PCM16 语音播报", False),
    ])

# 3) Go 后端
go_x, go_y, go_w, go_h = 1.4, 4.7, 12.4, 1.5
box(go_x, go_y, go_w, go_h, "Go 后端（server :8888 —— 核心实时编排层）", C_GO, "#0D47A1",
    sub=[
        ("UDP 音/视频分片重组", True),
        ("HTTP/WS API（/api/status · /snapshot.jpg · /ws/view）", False),
        ("DashScope Realtime 桥接", True),
        ("视觉 Worker 桥接（/api/vision/*）", False),
        ("设备端语音下发（device_playback）", True),
        ("码流统计", False),
    ])

# 4) Python Worker 与 Vue 前端（左右并排）
py_x, py_y, py_w, py_h = 1.4, 2.4, 6.0, 1.9
box(py_x, py_y, py_w, py_h, "Python 视觉 Worker（:18082）", C_PY, "#1B5E20",
    sub=[
        ("盲道分割 YOLO-SEG", "障碍物检测 YOLOE"),
        ("斑马线/红绿灯检测", "两轮车检测（ebike）"),
        ("寻物 + 语音会话 + 高德导航规划", "TTS 合成（DashScope）"),
    ])

vue_x, vue_y, vue_w, vue_h = 7.8, 2.4, 6.0, 1.9
box(vue_x, vue_y, vue_w, vue_h, "Vue 前端控制台（:5174）", C_VUE, "#BF360C",
    sub=[
        ("实时画面预览", True),
        ("模式切换（导盲/过街/问答/导航）", False),
        ("导航事件与质量指标", True),
        ("日志查看 / GPS 上报", False),
        ("语音播报控制", True),
        ("盲道友好路线展示", False),
    ])

# 5) 云服务层
box(1.4, 0.5, 12.4, 1.5, "外部 AI / 地图服务（云端）", C_CLOUD, "#4A148C",
    sub=[
        ("DashScope：ASR 语音识别 / TTS 语音合成 / Qwen Omni 多模态问答 / 千问大模型", True),
        ("高德地图：步行路线规划 / 地理编码 / 盲道友好路线", True),
    ])

# ================= 数据流箭头 =================
# 用户 <-> ESP32
arrow(7.0, 8.9, 7.0, 8.4, "佩戴 · 说话 · 听", color="#546E7A", label_dx=-3.6, lw=2.0)

# ESP32 <-> Go（UDP 上行 / 下行）
arrow(3.4, 6.7, 3.4, 6.2, "UDP 上行：视频 JPEG + 音频 PCM", color="#00838F", label_dx=0.4)
arrow(5.4, 6.2, 5.4, 6.7, "UDP 下行：AI 导航语音 PCM", color="#D84315", label_dx=0.4)

# Go -> Python（HTTP 转发视觉任务） / Python -> Go（结果回传）
arrow(4.4, 4.7, 4.4, 4.3, "HTTP：/api/vision/process · /api/vision/control", color="#2E7D32", label_dx=-2.5)
arrow(6.4, 4.3, 6.4, 4.7, "标注帧 / 导航状态 / 播报文本", color="#00695C", label_dx=2.4)

# Go <-> Vue（HTTP/WS）
arrow(13.8, 5.45, 13.8, 4.35, "HTTP/WS：实时画面 · 控制 · 事件", color="#EF6C00",
      connectionstyle="arc3,rad=0.0", label_dx=0.5, label_dy=0.0)

# Python -> 云服务（DashScope / 高德）
arrow(4.4, 2.4, 4.4, 2.0, "HTTP：DashScope Realtime · 高德路线规划", color="#6A1B9A", label_dx=-2.6, label_dy=0.0)

# Vue GPS 上报 -> Go（双向由 HTTP/WS 覆盖，省略单独箭头避免交叉）

plt.tight_layout(pad=0.4)
plt.savefig(OUT, bbox_inches="tight", facecolor=C_BG)
print("saved:", OUT)
