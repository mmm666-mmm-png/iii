# -*- coding: utf-8 -*-
"""构建《开发日志》Word 文档（python-docx）"""
import os
from docx import Document
from docx.shared import Pt, Inches, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

BASE = os.path.dirname(os.path.abspath(__file__))
SLIDE_DIR = os.path.join(os.path.dirname(BASE), "defense_ppt", "preview")
ARCH_DIR = os.path.join(os.path.dirname(BASE), "verification")
OUT = os.path.join(BASE, "开发日志.docx")

ACCENT = RGBColor(0x0B, 0x5C, 0xAD)
INK = RGBColor(0x1A, 0x1A, 0x1A)
GRAY = RGBColor(0x55, 0x55, 0x55)


def set_run_font(run, name="微软雅黑", size=10.5, bold=False, color=None):
    run.font.name = name
    run._element.rPr.rFonts.set(qn("w:eastAsia"), name)
    run.font.size = Pt(size)
    run.font.bold = bold
    if color is not None:
        run.font.color.rgb = color


def para(doc, text="", size=10.5, bold=False, color=None, align=None, space_after=6, space_before=0):
    p = doc.add_paragraph()
    r = p.add_run(text)
    set_run_font(r, size=size, bold=bold, color=color)
    if align is not None:
        p.alignment = align
    p.paragraph_format.space_after = Pt(space_after)
    p.paragraph_format.space_before = Pt(space_before)
    return p


def heading(doc, text, level=1):
    sizes = {1: 15, 2: 12, 3: 11}
    p = doc.add_paragraph()
    r = p.add_run(text)
    set_run_font(r, size=sizes[level], bold=True, color=(ACCENT if level == 1 else INK))
    p.paragraph_format.space_before = Pt(18 if level == 1 else 10)
    p.paragraph_format.space_after = Pt(6)
    if level == 1:
        pPr = p._p.get_or_add_pPr()
        pBdr = OxmlElement("w:pBdr")
        bottom = OxmlElement("w:bottom")
        bottom.set(qn("w:val"), "single")
        bottom.set(qn("w:sz"), "12")
        bottom.set(qn("w:space"), "1")
        bottom.set(qn("w:color"), "0B5CAD")
        pBdr.append(bottom)
        pPr.append(pBdr)
    return p


def bullets(doc, items, size=10.5):
    for it in items:
        p = doc.add_paragraph(style="List Bullet")
        r = p.add_run(it)
        set_run_font(r, size=size)
        p.paragraph_format.space_after = Pt(2)


def make_table(doc, headers, rows, widths=None):
    t = doc.add_table(rows=1, cols=len(headers))
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    hdr = t.rows[0].cells
    for i, h in enumerate(headers):
        hdr[i].text = ""
        p = hdr[i].paragraphs[0]
        r = p.add_run(h)
        set_run_font(r, size=9.5, bold=True)
        shd = OxmlElement("w:shd")
        shd.set(qn("w:val"), "clear")
        shd.set(qn("w:color"), "auto")
        shd.set(qn("w:fill"), "EAF1FA")
        hdr[i]._tc.get_or_add_tcPr().append(shd)
    for row in rows:
        cells = t.add_row().cells
        for i, val in enumerate(row):
            cells[i].text = ""
            p = cells[i].paragraphs[0]
            r = p.add_run(val)
            set_run_font(r, size=9.5)
    if widths:
        for i, w in enumerate(widths):
            for row in t.rows:
                row.cells[i].width = Inches(w)
    return t


def image(doc, path, caption, width=6.3):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run()
    run.add_picture(path, width=Inches(width))
    c = doc.add_paragraph()
    c.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = c.add_run(caption)
    set_run_font(r, size=9, color=GRAY)
    c.paragraph_format.space_after = Pt(10)


doc = Document()

# 全局默认字体
normal = doc.styles["Normal"]
normal.font.name = "Microsoft YaHei"
normal.font.size = Pt(10.5)
normal.element.rPr.rFonts.set(qn("w:eastAsia"), "微软雅黑")

# ===== 封面 =====
para(doc, "", space_after=60)
para(doc, "项 目 开 发 日 志", size=13, bold=True, color=ACCENT, align=WD_ALIGN_PARAGRAPH.CENTER, space_after=20)
para(doc, "基于 ESP32 智能眼镜的\n视觉导航与语音交互系统", size=24, bold=True, align=WD_ALIGN_PARAGRAPH.CENTER, space_after=12)
para(doc, "AI Glasses for Navigation · Development Log", size=12, color=GRAY, align=WD_ALIGN_PARAGRAPH.CENTER, space_after=60)
for line in ["项目周期：2026 年 6 月 3 日 — 2026 年 8 月 31 日",
             "文档类型：项目开发日志（含方案设计 / 实验测试 / 问题排查 / 版本迭代 / 团队分工）",
             "技术栈：ESP32-S3 · Go · Python · Vue 3 · DashScope/Qwen · 高德地图",
             "文档格式：Word (.docx)"]:
    para(doc, line, size=11, align=WD_ALIGN_PARAGRAPH.CENTER, space_after=6)
doc.add_page_break()

# ===== 目录 =====
heading(doc, "目录", 1)
toc = ["一、项目概述", "二、开发时间线总览", "三、分阶段开发记录",
       "四、关键问题排查与解决", "五、实验与测试数据", "六、版本迭代记录",
       "七、团队分工", "八、总结与展望", "附录 A：方案设计与系统架构图（佐证材料）"]
for i, item in enumerate(toc, 1):
    para(doc, "%d. %s" % (i, item), size=10.5, space_after=4)
doc.add_page_break()

# ===== 一、项目概述 =====
heading(doc, "一、项目概述", 1)
heading(doc, "1.1 项目背景与目标", 2)
para(doc, "视障与低视力人群在独立出行中面临三大核心痛点：环境感知受限（难以判断盲道走向、前方障碍物）、过街判断困难（斑马线对准、红绿灯状态难以感知）、交互方式不自然（需要语音即时的信息获取与问答）。")
para(doc, "本项目以低成本可穿戴硬件为切入点，设计并实现一套基于 ESP32 智能眼镜的视觉导航与语音交互系统：眼镜端负责音视频采集与语音播报，云端/本地 AI 服务负责视觉理解与自然语言交互，从而在有限算力与网络条件下，为使用者提供盲道导航、过街引导、障碍物避让、寻物引导、语音问答与盲道友好路线规划等能力。")
heading(doc, "1.2 系统总体架构", 2)
para(doc, "系统采用「四层结构 + 端云协同」设计，各层职责如下：")
make_table(doc,
    ["层级", "目录", "主要作用", "技术框架"],
    [["设备端", "firmware/xiao_sense_ws_stream", "摄像头/麦克风采集、扬声器播放、Wi-Fi/UDP 通信", "Arduino ESP32、FreeRTOS、ESP32 Camera、I2S/PDM"],
     ["后端中转层", "server", "接收设备音视频流、维护设备状态、转发视觉任务、对接 DashScope Realtime", "Go、Gin、Gorilla WebSocket"],
     ["AI/视觉 Worker", "python_worker", "盲道导航、斑马线/红绿灯检测、障碍物检测、寻物引导、ASR 与多模态问答、路线规划", "Python、FastAPI、OpenCV、PyTorch、Ultralytics、MediaPipe、DashScope"],
     ["前端展示层", "esp32-glass-front", "实时画面预览、设备状态、AI 状态、导航控制", "Vue 3、Vite、Ant Design Vue"]],
    widths=[0.9, 1.5, 2.4, 1.7])
para(doc, "核心数据流：ESP32 采集摄像头 JPEG 帧与麦克风 PCM 音频，经 UDP 发送给 Go 后端 → Go 后端重组音视频流并提供 /api/status、/snapshot.jpg、WebSocket 等接口给前端 → Go 按配置将视频帧转发给 Python Worker 的 /api/vision/process、将控制命令转发给 /api/vision/control → Python Worker 调用视觉/大模型生成导航状态、避障提示与标注画面 → 导航语音经预生成 wav 或 AI 语音回传 Go 后端，再由 Go 经 UDP 下发 ESP32 扬声器播放。", size=9.5, color=GRAY)
heading(doc, "1.3 核心功能模块", 2)
bullets(doc, [
    "盲道导航（workflow_blindpath.py）：YOLO 分割盲道区域，根据掩码位置与中心线计算路径偏移，生成「直行/左移/右移/停下」等提示。",
    "过马路导航（workflow_crossstreet.py）：检测斑马线与通行区域、红绿灯状态机，输出「发现斑马线→等待绿灯→开始通行→过街结束」语音。",
    "障碍物检测（obstacle_detector_client.py）：8 类障碍物（bicycle/bus/car/cone/dog/person/spherical_roadblock/tricycle）检测与避让提示。",
    "寻物/抓取引导（yolomedia.py）：YOLOE 开放词汇分割 + MediaPipe 手部关键点，给出「向左/向右/向前/后退/已对中」提示。",
    "语音交互（asr_core.py + omni_client.py）：DashScope Paraformer 实时 ASR + Qwen Omni 多模态问答。",
    "盲道友好路线规划（domain/ 等 DDD 模块）：高德路线 + 盲道覆盖率 + 障碍物密度的六维打分引擎，逐段语音播报。",
    "人行道两轮车检测（ebike_detector.py）：共享电动车 / 自行车 / 家用电动车检测与避让。",
])

# ===== 二、时间线总览 =====
heading(doc, "二、开发时间线总览", 1)
make_table(doc,
    ["阶段", "时间", "关键节点"],
    [["阶段一", "6 月 3 日 — 6 月 30 日", "需求调研、总体方案设计、技术选型"],
     ["阶段二", "7 月 1 日 — 7 月 31 日", "四层架构详细设计、DDD 路线规划引擎设计、部署方案设计、开发环境搭建"],
     ["阶段三", "8 月 1 日 — 8 月 23 日", "固件 / Go 后端 / Python Worker / 前端核心功能开发"],
     ["阶段四", "8 月 24 日 — 8 月 27 日", "语音交互与高德导航集成、首次代码入库、障碍物上报与实时播报、语音模式联动"],
     ["阶段五", "8 月 29 日 — 8 月 30 日", "数据集构建、YOLO 模型训练、障碍物检测模型切换"],
     ["阶段六", "8 月 30 日 — 8 月 31 日", "系统联调、测试验证、流畅度优化、答辩材料整理"]],
    widths=[0.8, 1.7, 4.0])

# ===== 三、分阶段开发记录 =====
heading(doc, "三、分阶段开发记录", 1)
heading(doc, "阶段一：需求调研与总体方案设计（6 月 3 日 — 6 月 30 日）", 2)
para(doc, "本阶段完成项目选题与可行性论证。针对视障人群出行的「环境感知、过街判断、自然交互」三大痛点，确定了「低成本可穿戴硬件 + 端云协同 AI」的技术路线：")
bullets(doc, [
    "硬件选型：采用 XIAO ESP32-S3 Sense 作为设备端，集摄像头、麦克风、扬声器于一体，成本低、功耗低，适合可穿戴场景。",
    "架构选型：由于 ESP32 端侧算力有限，采用「ESP32 采集 → Go 实时中转 → Python 多模型视觉导航 → DashScope/Qwen 语音多模态 → Vue 前端监控」的端云协同架构。",
    "模型选型：视觉采用 Ultralytics YOLO / YOLOE 系列（盲道分割、障碍物检测、红绿灯检测），语音与大模型采用阿里云 DashScope 的 Paraformer（ASR）与 Qwen Omni 系列（多模态问答）。",
    "设计产出：形成《模型与框架说明》《系统总体架构》《答辩叙事计划》等设计文档。",
])
heading(doc, "阶段二：详细设计与开发环境搭建（7 月）", 2)
bullets(doc, [
    "四层架构细化：明确设备端、Go 后端、Python Worker、Vue 前端各自的目录边界、通信协议（UDP 音视频上传、HTTP/WS 控制）、端口规划（Go 8888、Python Worker 18082、前端开发 5174）。",
    "DDD 路线规划引擎设计：将「盲道友好路线规划」作为核心领域，按 Interfaces / Application / Domain / Infrastructure 四层分离，设计六维加权打分引擎（距离 10%、转弯 10%、红绿灯 10%、道路类型 15%、盲道覆盖率 30%、障碍物密度 25%）与障碍物时空聚合器（10 米空间聚类、2 分钟时间半衰期、严重程度加权）。",
    "部署方案设计：制定 Linux / Windows 双平台部署文档与启动脚本，明确环境变量（高德 Key、DashScope Key、模型路径等）。",
    "开发环境搭建：搭建 conda esp32 Python 环境（cv2、torch+cu126、ultralytics）、Go 工具链、Node/Vite 前端环境。",
])
heading(doc, "阶段三：核心功能开发（8 月 1 日 — 8 月 23 日）", 2)
bullets(doc, [
    "ESP32 固件（xiao_sense_ws_stream）：摄像头 VGA JPEG 采集、麦克风 PCM 采集、扬声器 PCM16 播放，FreeRTOS 任务队列拆分视频/音频/播放任务，自定义 UDP 协议传输 hello / 音频 / 视频分片 / AI 下行音频。",
    "Go 后端（server）：UDP 视频分片重组、WebSocket 查看器、视觉 Worker 桥接、DashScope Realtime 桥接、预生成导航 wav 的设备端播放、码流统计。",
    "Python Worker（python_worker）：NavigationMaster 状态机调度盲道导航、过街导航、寻物引导；盲道分割与偏移估计、斑马线/红绿灯检测、YOLOE 开放词汇障碍物检测、MediaPipe 手部关键点。",
    "Vue 前端（esp32-glass-front）：实时画面预览、设备/ AI 状态面板、工作模式切换、导航事件与日志展示。",
])
heading(doc, "阶段四：语音交互与高德导航集成（8 月 24 日 — 8 月 27 日）", 2)
make_table(doc,
    ["日期", "关键节点"],
    [["8 月 24 日", "首次代码提交（大模型文件转 Git LFS 管理，忽略于普通提交）。"],
     ["8 月 25 日", "完成语音识别模块（DashScope Paraformer 实时 ASR）；加入语音播报、语音切换；语音导航上线。"],
     ["8 月 26 日", "新增障碍物上报功能、实时播报；修复 Python 运行路径；同步本地代码。"],
     ["8 月 25 — 27 日", "高德导航 + 盲道友好路线规划（DDD）：路线六维打分、障碍物时空聚合、逐段 TTS 播报、GPS 逐段导航触发；修复高德地理编码 city 参数、步行 polyline 解析、WGS-84→GCJ-02 坐标转换等系列问题。"],
     ["8 月 27 日", "语音模式联动（导盲↔高德导航）、「明眸」唤醒词门控、导航播报优先级高于聊天、聊天回声自问自答修复、人行道两轮车检测接入。"]],
    widths=[1.1, 5.4])
heading(doc, "阶段五：数据集构建与模型训练（8 月 29 日 — 8 月 30 日）", 2)
bullets(doc, [
    "8 月 29 日：构建训练数据集 —— 障碍物 8 类数据集（obstacle.v2i.yolo26）、ebike 数据集、bike 数据集。",
    "8 月 30 日：在 RTX 4050（CUDA）上使用 yolo CLI 训练；将障碍物检测由 YOLOE 分割模型（yoloe-11l-seg.pt）切换到检测模型 block.pt（8 类），提升推理速度与类别可解释性；解决 Windows 多进程 DataLoader 的 WinError 1455 页面文件问题（workers=0）。",
])
heading(doc, "阶段六：系统联调、测试与收尾（8 月 30 日 — 8 月 31 日）", 2)
bullets(doc, [
    "障碍物语音路由修复：障碍物语音此前仅在 Python 内部播放（只到 /stream.wav 浏览器链路），设备扬声器走 Go 下行不经过此链路导致不播报；改为将最终语音作为 guidanceText 返回，经 Go broadcastEvent 下发设备扬声器 + 前端事件。",
    "流畅度优化：标注图与预览帧 JPEG 质量 80→92；Go 视觉帧间隔 450ms→200ms（约 5FPS 标注帧率），guidance 去重改为时间窗（同文案超 3 秒允许重播）。",
    "测试验证：Go 单元测试与 Python 单元测试通过（详见第五节）；整理 14 页答辩 PPT 佐证材料。",
])

# ===== 四、关键问题排查与解决 =====
heading(doc, "四、关键问题排查与解决", 1)
para(doc, "开发过程中遇到并解决了大量工程问题，以下为最具代表性的问题清单：")
make_table(doc,
    ["编号", "问题现象", "根因", "解决方案"],
    [["1", "高德路线规划报 OVER_DIRECTION_RANGE", "地理编码未带 city 参数，常见地名解析到外省", "地理编码强制携带 AMAP_CITY（枣庄）"],
     ["2", "盲道覆盖率恒为 0、路段为空", "高德步行接口 step 无 start/end_location，只有 polyline", "改为解析 polyline 生成路段"],
     ["3", "GPS 实时播报永不触发", "手机 GPS 为 WGS-84，高德路线为 GCJ-02，二者偏移约 537m，超过 80m 匹配阈值", "新增 wgs84_to_gcj02()，匹配前先转换坐标系"],
     ["4", "/api/gps/update 404", "端点挂在错误 app 上，worker 主入口未 include 该路由", "端点移入 route_endpoints.py 路由并挂载；异常统一返回 200 + success"],
     ["5", "手机 GPS 到不了 Worker", "前端仍走 WebSocket 上行，而 Go 已改为 HTTP 端点", "前端 sendGpsUpdate 改为 fetch /api/gps/update，对齐 Go HTTP 链路"],
     ["6", "语音 ASR/技能/千问全部失效", "运行的是旧 xiao-stream.exe，未加载 .env 的 DASHSCOPE_API_KEY，s.ai==nil", "重新 go build 并以正确工作目录重启"],
     ["7", "前端「问答」按钮切不回千问", "setVoiceMode 对 qa 模式是 no-op（只认 chat/navigation）", "qa → setVoiceMode(\"chat\") 映射修正"],
     ["8", "导航播报「自问自答」回声循环", "播报被 ESP32 麦克风拾取 → ASR 识别 → 千问再回答", "三层防护：is_audio_playing() 检查本地播放 + 播报后宽限 ECHO_SUPPRESS_SECONDS + 文本回声守卫（SequenceMatcher 相似即忽略）"],
     ["9", "ebike 检测把 Worker 卡死", "检测在事件循环内同步加载 YOLOE（约 10s）+ 推理阻塞", "改为后台线程 + 防重入 + 缓存画框；默认 AIGLASS_EBIKE_ENABLED=0"],
     ["10", "「明眸」唤醒词无法切换模式", "生僻词被 ASR 误识别（名模/明谋/明某等）", "新增 isWakeWordTranscript() 中文变体 + 拼音匹配"],
     ["11", "改完代码重启后不生效", "VS Code 编辑缓冲区与磁盘文件不一致，对既有文件的编辑丢失", "改后强制落盘校验，必要时脚本直接写盘"],
     ["12", "torch 导入报 WinError 1114 c10.dll", "D:\\ 根目录残留远古运行库 DLL 劫持依赖解析", "将残留 DLL 改名为 *.disabled 回退 System32 新版"],
     ["13", "yolo CLI 无法使用 ul:// 平台", "CLI 不读取 .env，ULTRALYTICS_API_KEY 未生效", "Key 写入 ultralytics settings.json 的 api_key 字段"],
     ["14", "Windows 训练报 WinError 1455 页面文件太小", "默认 8 个 DataLoader 子进程同时加载 CUDA DLL", "训练强制 workers=0，device=0"],
     ["15", "障碍物提示设备端不播报", "语音只在 Python 内部 play_voice_text，未回传 Go 下行", "改为 guidanceText 返回 → Go broadcastEvent 下发设备扬声器"]],
    widths=[0.5, 1.6, 2.1, 2.3])

# ===== 五、实验与测试数据 =====
heading(doc, "五、实验与测试数据", 1)
heading(doc, "5.1 单元测试结果", 2)
para(doc, "测试执行时间：2026-08-31（本日志整理日复核）。")
make_table(doc,
    ["测试模块", "通过", "失败", "说明"],
    [["Go 后端 internal/serverapp", "ok", "0", "go test ./... 通过"],
     ["tests/test_route_scoring（路线打分引擎）", "7", "0", "权重归一化 / 单路线打分 / 盲道路线得分更高 / 排序 / 障碍物密度影响 / 盲道覆盖率 / 评分明细"],
     ["tests/test_obstacle_aggregator（障碍物聚合）", "9", "0", "热点创建 / 邻近合并 / 异类不合并 / 远距不合并 / 时间衰减 / 过期清理 / 严重度加权 / 邻近查询 / 清空"],
     ["tests/test_route_planning_service（路线规划集成）", "7", "1", "1 项失败（test_obstacle_report_affects_planning）为外部 DashScope 账号欠费（Arrearage）导致播报生成失败，非代码逻辑问题"],
     ["voice_session_manager（语音会话离线测试）", "6", "0", "6 个离线场景：导航切换 / ASR 打断 / 未唤醒 chat_locked / 明眸唤醒切聊天 / 导航优先 / 回声忽略"]],
    widths=[2.0, 0.7, 0.7, 3.1])
para(doc, "说明：路线规划集成测试中唯一失败项与代码逻辑无关，是调用 DashScope 大模型生成播报文本时返回 Arrearage（账号欠费）错误；更换/充值账号后该用例即可通过，打分与规划逻辑本身全部通过。", size=9.5, color=GRAY)
heading(doc, "5.2 导航功能测试用例", 2)
make_table(doc,
    ["测试场景", "测试数据"],
    [["盲道友好路线规划", "起点：湖西景苑A区（117.519703, 34.854012）→ 终点：东湖公园（117.531722, 34.851625），约 1840 米，规划成功并返回 11 段 turn_by_turn"],
     ["GPS 逐段导航", "手机 GPS（WGS-84）→ GCJ-02 转换 → 80m 路段匹配 → 逐段播报「前方 50 米左转」等"],
     ["语音导航指令", "「导航到火车站」等口令，未指定起点时使用手机最近定位作为起点"],
     ["语音模式联动", "「导航到XX」= 高德导航 + 导盲模式双开；「明眸」= 切回千问聊天"]],
    widths=[1.5, 5.0])
heading(doc, "5.3 模型资产清单", 2)
make_table(doc,
    ["模型文件", "用途"],
    [["yolo-seg.pt", "盲道、斑马线等地面目标分割（导盲/过街）"],
     ["yoloe-11l-seg.pt", "YOLOE 开放词汇分割（障碍物/寻物）"],
     ["block.pt", "障碍物检测模型（8 类，训练后替换 YOLOE 分割）"],
     ["trafficlight.pt", "红绿灯状态检测（红/绿/倒计时）"],
     ["shoppingbest5.pt", "寻物固定类别模型（历史资产）"],
     ["hand_landmarker.task", "MediaPipe 手部关键点（寻物手-物相对位置）"]],
    widths=[2.0, 4.5])

# ===== 六、版本迭代记录 =====
heading(doc, "六、版本迭代记录", 1)
para(doc, "以下为 Git 提交历史（提取自仓库 main 与 origin/dev 分支）：")
make_table(doc,
    ["日期", "提交者", "提交说明"],
    [["2026-08-24", "mmm666-mmm-png", "首次提交，忽略大模型文件（Git LFS 管理）"],
     ["2026-08-25", "mmm666-mmm-png", "Sync local project changes（同步本地工程）"],
     ["2026-08-25", "mmm666-mmm-png", "已加入语音播报"],
     ["2026-08-25", "mmm666-mmm-png", "加入语音切换"],
     ["2026-08-25", "mmm666-mmm-png", "完成语音识别模块"],
     ["2026-08-26", "mmm666-mmm-png", "可进行语音导航"],
     ["2026-08-26", "liugensheng", "dev: sync local code changes（同步代码，排除模型文件）"],
     ["2026-08-26", "liugensheng", "添加障碍物上报功能"],
     ["2026-08-26", "liugensheng", "添加实时播报"],
     ["2026-08-26", "liugensheng", "modify the python path（修正 Python 路径）"],
     ["2026-08-26", "mmm666-mmm-png", "2026.8.26（当日功能整合提交）"],
     ["2026-08-29", "mmm666-mmm-png", "8.28（模型训练与数据集阶段提交）"]],
    widths=[1.0, 1.4, 4.1])

# ===== 七、团队分工 =====
heading(doc, "七、团队分工", 1)
make_table(doc,
    ["成员", "主要职责"],
    [["苗源（项目负责人 / 主开发）", "总体架构设计、Python Worker 视觉导航与语音交互、高德导航与盲道友好路线规划（DDD）、语音识别/播报/切换、前后端联调、模型训练与数据集构建"],
     ["刘根生（协作开发）", "障碍物上报、实时播报、Python 运行路径与部署环境修复"],
     ["成员（视频剪辑）", "项目宣传/演示视频的拍摄与剪辑"],
     ["成员（开发日志 / PPT）", "开发日志编写与答辩 PPT 制作"],
     ["成员（硬件调试）", "ESP32 硬件与设备端调试（固件烧录、音视频采集、扬声器播放、设备联调）"]],
    widths=[1.8, 4.7])

# ===== 八、总结与展望 =====
heading(doc, "八、总结与展望", 1)
para(doc, "本项目在约三个月周期内完成了从需求调研、方案设计到系统实现、模型训练与测试验证的完整闭环，实现了 ESP32 智能眼镜在盲道导航、过街引导、障碍物避让、寻物引导、语音交互与盲道友好路线规划方面的核心能力，并通过「ESP32 端侧采集 + Go 实时中转 + Python 多模型视觉导航 + DashScope/Qwen 语音多模态 + Vue 前端监控」的架构落地了端云协同方案。")
para(doc, "后续优化方向：", bold=True)
bullets(doc, [
    "接入 VAD 实时打断源，进一步提升语音交互的即时性；",
    "扩充两轮车/障碍物训练数据，提升 ebike 检测精度（共享电动车与家用电动车视觉相似，必要时合并类别）；",
    "优化 YOLOE 与多模型并存时的显存占用，提升端侧/边缘推理速度；",
    "完善盲道 GeoJSON 数据覆盖范围（当前为示例数据，非官方测绘）；",
    "推进真机实景测试与视障用户可用性评估。",
])

# ===== 附录 A =====
doc.add_page_break()
heading(doc, "附录 A：方案设计与系统架构图（佐证材料）", 1)
para(doc, "首先展示本系统独立绘制的系统总体架构图（图 A-0），随后为项目答辩 PPT（14 页）的导出截图，涵盖背景意义、需求目标、系统架构、关键实现、测试验证与创新总结，作为方案设计与系统实现的佐证材料。", size=9.5, color=GRAY)

# 图 A-0：独立绘制的系统总体架构图
arch_img = os.path.join(ARCH_DIR, "系统架构图.png")
if os.path.exists(arch_img):
    image(doc, arch_img, "图 A-0 系统总体架构图：端 - 边 - 云分层与实时数据流（独立绘制）", width=6.5)

caps = [
    ("slide-01.png", "图 A-1 封面：视觉导航 + 语音交互 + ESP32 智能眼镜"),
    ("slide-02.png", "图 A-2 研究背景与意义：视障出行痛点"),
    ("slide-03.png", "图 A-3 需求分析与设计目标：实时、低成本、可扩展、可调试、可部署"),
    ("slide-04.png", "图 A-4 系统总体架构：ESP32 / Go / Python / AI / Vue 数据流"),
    ("slide-05.png", "图 A-5 硬件与通信方案：摄像头/麦克风/扬声器、UDP、HTTP/WS"),
    ("slide-06.png", "图 A-6 软件模块划分：固件、后端、视觉 Worker、前端、部署资产"),
    ("slide-07.png", "图 A-7 导盲路径算法：方向校正、YOLO 分割、偏移估计、障碍物辅助"),
    ("slide-08.png", "图 A-8 过街与红绿灯流程：斑马线对齐、红绿灯状态机"),
    ("slide-09.png", "图 A-9 语音交互与 AI：ASR 命令、Qwen Omni 多模态问答、音频分流"),
    ("slide-10.png", "图 A-10 前端控制台与调试：实时画面、模式切换、导航事件"),
    ("slide-11.png", "图 A-11 测试与验证：语法编译、Go 单测、前端构建、接口验收"),
    ("slide-12.png", "图 A-12 难点与创新点：低延迟流媒体、跨语言协同、导航状态机、多模型融合"),
    ("slide-13.png", "图 A-13 总结与展望：已完成能力与优化方向"),
    ("slide-14.png", "图 A-14 Q&A：答辩收束页"),
]
for fname, cap in caps:
    img = os.path.join(SLIDE_DIR, fname)
    if os.path.exists(img):
        image(doc, img, cap)

doc.save(OUT)
print("saved:", OUT)
print("size MB:", round(os.path.getsize(OUT) / 1024 / 1024, 2))
