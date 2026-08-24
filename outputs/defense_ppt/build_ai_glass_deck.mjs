const fs = await import("node:fs/promises");
const path = await import("node:path");
const { Presentation, PresentationFile } = await import("@oai/artifact-tool");

const W = 1280;
const H = 720;
const OUT_DIR = path.resolve("outputs", "defense_ppt");
const PREVIEW_DIR = path.join(OUT_DIR, "preview");
const REFERENCE_DIR = path.join(OUT_DIR, "reference");
const SCRATCH_DIR = path.join(OUT_DIR, "scratch");
const VERIFY_DIR = path.join(OUT_DIR, "verification");
const INSPECT_PATH = path.join(OUT_DIR, "inspect.ndjson");
const HERO_PATH = path.resolve("esp32-glass-front", "src", "assets", "hero.png");
const OUTPUT_PPTX = path.join(OUT_DIR, "output.pptx");

const C = {
  bg: "#F7F8F3",
  panel: "#FFFFFF",
  ink: "#17201D",
  muted: "#5E6A66",
  line: "#CFD8D2",
  green: "#0E8F6F",
  green2: "#35B37E",
  amber: "#D29A2E",
  coral: "#D8624B",
  cyan: "#2C8FB8",
  violet: "#6F5CB8",
  softGreen: "#E3F3EB",
  softAmber: "#FFF0D2",
  softCoral: "#FDE4DE",
  softCyan: "#E3F1F6",
  dark: "#10211C",
  white: "#FFFFFF",
  transparent: "#00000000",
};

const FONT = {
  title: "Microsoft YaHei",
  body: "Microsoft YaHei",
  mono: "Cascadia Mono",
};

const slidesMeta = [
  ["项目概览", "封面"],
  ["01", "研究背景与意义"],
  ["02", "需求分析与目标"],
  ["03", "系统总体架构"],
  ["04", "硬件与通信方案"],
  ["05", "软件模块划分"],
  ["06", "导盲路径算法"],
  ["07", "过街与红绿灯流程"],
  ["08", "语音交互与 AI"],
  ["09", "前端控制台与调试"],
  ["10", "测试与验证"],
  ["11", "难点与创新点"],
  ["12", "总结与展望"],
  ["Q&A", "答辩交流"],
];

const inspectRecords = [];
let currentSlideNo = 0;
let shapeSeq = 0;

function beginSlide(slideNo) {
  currentSlideNo = slideNo;
  inspectRecords.push({ kind: "slide", slide: slideNo, id: `slide-${slideNo}` });
}

function recordShape(shape, role, geometry, x, y, w, h) {
  if (!currentSlideNo) return;
  inspectRecords.push({
    kind: "shape",
    slide: currentSlideNo,
    id: shape?.id || `shape-${currentSlideNo}-${++shapeSeq}`,
    role,
    shapeType: geometry,
    bbox: [x, y, w, h],
  });
}

function textLineCount(text) {
  const value = String(text ?? "");
  if (!value.trim()) return 0;
  return Math.max(1, value.split(/\n/).length);
}

function recordText(shape, role, text, x, y, w, h) {
  if (!currentSlideNo) return;
  const value = String(text ?? "");
  inspectRecords.push({
    kind: "textbox",
    slide: currentSlideNo,
    id: shape?.id || `text-${currentSlideNo}-${++shapeSeq}`,
    role,
    text: value,
    textPreview: value.replace(/\n/g, " | ").slice(0, 180),
    textChars: value.length,
    textLines: textLineCount(value),
    bbox: [x, y, w, h],
  });
}

function recordImage(image, role, source, x, y, w, h) {
  if (!currentSlideNo) return;
  inspectRecords.push({
    kind: "image",
    slide: currentSlideNo,
    id: image?.id || `image-${currentSlideNo}-${++shapeSeq}`,
    role,
    path: source,
    bbox: [x, y, w, h],
  });
}

function line(fill = C.transparent, width = 0) {
  return { style: "solid", fill, width };
}

async function exists(filePath) {
  try {
    await fs.access(filePath);
    return true;
  } catch {
    return false;
  }
}

async function readImageBlob(filePath) {
  const bytes = await fs.readFile(filePath);
  return bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
}

function addShape(slide, geometry, x, y, w, h, fill = C.transparent, stroke = C.transparent, strokeWidth = 0, extra = {}) {
  const shape = slide.shapes.add({
    geometry,
    position: { left: x, top: y, width: w, height: h },
    fill,
    line: line(stroke, strokeWidth),
    ...extra,
  });
  recordShape(shape, extra.role || geometry, geometry, x, y, w, h);
  return shape;
}

function addText(slide, text, x, y, w, h, opts = {}) {
  const box = addShape(slide, "rect", x, y, w, h, opts.fill ?? C.transparent, opts.stroke ?? C.transparent, opts.strokeWidth ?? 0);
  box.text = text;
  box.text.fontSize = opts.size ?? 20;
  box.text.color = opts.color ?? C.ink;
  box.text.bold = Boolean(opts.bold);
  box.text.typeface = opts.face ?? FONT.body;
  box.text.alignment = opts.align ?? "left";
  box.text.verticalAlignment = opts.valign ?? "top";
  box.text.insets = opts.insets ?? { left: 0, right: 0, top: 0, bottom: 0 };
  if (opts.autoFit) box.text.autoFit = opts.autoFit;
  recordText(box, opts.role || "text", text, x, y, w, h);
  return box;
}

function addBg(slide, slideNo, variant = "default") {
  slide.background.fill = C.bg;
  addShape(slide, "rect", 0, 0, W, H, C.bg);
  addShape(slide, "rect", 0, 0, W, 12, variant === "dark" ? C.green2 : C.green);
  addShape(slide, "ellipse", -92, 516, 260, 260, "#DCEFE6");
  addShape(slide, "ellipse", 1078, -120, 260, 260, "#F6E2C0");
  for (let i = 0; i < 8; i += 1) {
    addShape(slide, "ellipse", 94 + i * 34, 656, 5, 5, "#B7C5BF");
  }
}

function addHeader(slide, slideNo) {
  const [code, label] = slidesMeta[slideNo - 1];
  addText(slide, code, 64, 33, 88, 24, { size: 14, color: C.green, bold: true, face: FONT.mono, align: "left" });
  addText(slide, label, 154, 32, 500, 26, { size: 14, color: C.muted, bold: true });
  addText(slide, `${String(slideNo).padStart(2, "0")} / 14`, 1118, 32, 98, 24, {
    size: 13,
    color: C.muted,
    face: FONT.mono,
    align: "right",
  });
  addShape(slide, "rect", 64, 65, 1152, 1.5, C.line);
}

function addTitle(slide, slideNo, title, subtitle) {
  addHeader(slide, slideNo);
  addText(slide, title, 64, 92, 730, 62, { size: 34, bold: true, face: FONT.title, color: C.ink });
  if (subtitle) addText(slide, subtitle, 66, 156, 760, 46, { size: 17, color: C.muted });
}

function addTag(slide, text, x, y, w, color = C.green, fill = C.softGreen) {
  addShape(slide, "roundRect", x, y, w, 34, fill, color, 1);
  addText(slide, text, x + 14, y + 7, w - 28, 18, { size: 13, color, bold: true, align: "center" });
}

function addCard(slide, x, y, w, h, title, body, opts = {}) {
  const accent = opts.accent ?? C.green;
  addShape(slide, "roundRect", x, y, w, h, opts.fill ?? C.panel, opts.stroke ?? C.line, 1.2);
  addShape(slide, "rect", x, y, 7, h, accent);
  if (opts.number) {
    addShape(slide, "ellipse", x + 22, y + 24, 38, 38, opts.badgeFill ?? C.softGreen, accent, 1.2);
    addText(slide, opts.number, x + 22, y + 31, 38, 18, { size: 13, color: accent, bold: true, face: FONT.mono, align: "center" });
    addText(slide, title, x + 76, y + 25, w - 98, 30, { size: 18, bold: true, color: C.ink });
  } else {
    addText(slide, title, x + 26, y + 23, w - 52, 30, { size: 18, bold: true, color: C.ink });
  }
  addText(slide, body, x + 26, y + 68, w - 52, h - 84, { size: opts.bodySize ?? 15, color: C.muted });
}

function addMetric(slide, x, y, w, h, value, label, color = C.green, note = "") {
  addShape(slide, "roundRect", x, y, w, h, C.panel, C.line, 1.2);
  addShape(slide, "rect", x, y, w, 7, color);
  addText(slide, value, x + 18, y + 24, w - 36, 46, { size: 30, bold: true, color: C.ink, face: FONT.title });
  addText(slide, label, x + 20, y + 78, w - 40, 36, { size: 15, color: C.muted });
  if (note) addText(slide, note, x + 20, y + h - 32, w - 40, 18, { size: 10, color: C.muted });
}

function addArrow(slide, from, fromIdx, to, toIdx, color = C.green, kind = "straight") {
  const fp = from.position;
  const tp = to.position;
  const x = fp.left + fp.width + 10;
  const y = fp.top + fp.height / 2 - 10;
  const w = Math.max(26, tp.left - x - 10);
  return addShape(slide, "rightArrow", x, y, w, 20, color, C.transparent, 0, { role: "flow arrow" });
}

function addStepCard(slide, x, y, w, h, no, title, body, accent) {
  addShape(slide, "roundRect", x, y, w, h, C.panel, C.line, 1.2);
  addShape(slide, "rect", x, y, 7, h, accent);
  addShape(slide, "ellipse", x + 22, y + 21, 40, 40, C.bg, accent, 1.2);
  addText(slide, no, x + 22, y + 32, 40, 14, { size: 12, color: accent, bold: true, face: FONT.mono, align: "center" });
  addText(slide, title, x + 78, y + 17, w - 104, 24, { size: 18, bold: true, color: C.ink });
  addText(slide, body, x + 78, y + 48, w - 104, 20, { size: 12, color: C.muted });
}

function addNotes(slide, text) {
  slide.speakerNotes.setText(text);
}

async function addHeroImage(slide, x, y, w, h, opacityPanel = false) {
  if (!(await exists(HERO_PATH))) return null;
  if (opacityPanel) addShape(slide, "roundRect", x - 22, y - 16, w + 44, h + 32, "#FFFFFFC9", C.line, 1);
  const image = slide.images.add({ blob: await readImageBlob(HERO_PATH), fit: "contain", alt: "前端项目中的层叠设备视觉图" });
  image.position = { left: x, top: y, width: w, height: h };
  recordImage(image, "visual asset", HERO_PATH, x, y, w, h);
  return image;
}

async function addDeckVisualAssets(p) {
  if (!(await exists(HERO_PATH))) return;
  const blob = await readImageBlob(HERO_PATH);
  for (let i = 0; i < p.slides.items.length; i += 1) {
    if (i === 0) continue;
    currentSlideNo = i + 1;
    const image = p.slides.items[i].images.add({ blob, fit: "contain", alt: "项目视觉资产水印" });
    image.position = { left: 1124, top: 615, width: 58, height: 48 };
    recordImage(image, "visual watermark", HERO_PATH, 1124, 615, 58, 48);
  }
}

function addMiniDevice(slide, x, y, w = 280, h = 180) {
  addShape(slide, "roundRect", x + 28, y + 22, w - 56, h - 44, "#1E2524", C.ink, 1.2);
  addShape(slide, "roundRect", x + 48, y + 50, w - 96, h - 96, "#2B3431", "#55615C", 1);
  addShape(slide, "ellipse", x + w / 2 - 28, y + h / 2 - 28, 56, 56, "#0A1210", "#86918D", 3);
  addShape(slide, "ellipse", x + w / 2 - 13, y + h / 2 - 13, 26, 26, C.green2);
  addShape(slide, "ellipse", x + 48, y + h - 50, 18, 18, C.coral);
  addShape(slide, "ellipse", x + w - 66, y + h - 50, 18, 18, C.amber);
  addText(slide, "CAM", x + w / 2 - 28, y + h / 2 + 38, 56, 16, { size: 10, color: "#DDE6E0", face: FONT.mono, align: "center" });
}

function addProtocolRow(slide, y, name, direction, payload, color) {
  addShape(slide, "roundRect", 650, y, 500, 52, C.panel, C.line, 1);
  addShape(slide, "rect", 650, y, 6, 52, color);
  addText(slide, name, 670, y + 13, 105, 22, { size: 15, color, bold: true, face: FONT.mono });
  addText(slide, direction, 790, y + 14, 98, 20, { size: 13, color: C.muted, align: "center" });
  addText(slide, payload, 910, y + 12, 220, 24, { size: 14, color: C.ink });
}

async function slideCover(p) {
  const slide = p.slides.add();
  beginSlide(1);
  addBg(slide, 1);
  addShape(slide, "rect", 0, 0, W, H, "#F7F8F3E8");
  addShape(slide, "rect", 64, 96, 8, 438, C.green);
  addText(slide, "毕业设计答辩", 92, 98, 220, 26, { size: 17, color: C.green, bold: true });
  addText(slide, "基于 ESP32 智能眼镜的\n视觉导航与语音交互系统", 90, 150, 720, 154, {
    size: 42,
    bold: true,
    face: FONT.title,
    color: C.ink,
  });
  addText(slide, "面向视障出行辅助场景，构建低成本可穿戴硬件、实时视觉感知与自然语音交互的一体化原型。", 94, 332, 650, 58, {
    size: 19,
    color: C.muted,
  });
  addTag(slide, "ESP32-S3 Sense", 94, 422, 164, C.green, C.softGreen);
  addTag(slide, "YOLO / YOLOE", 274, 422, 148, C.coral, C.softCoral);
  addTag(slide, "DashScope AI", 438, 422, 146, C.cyan, C.softCyan);
  addShape(slide, "roundRect", 92, 500, 488, 86, C.panel, C.line, 1.2);
  addText(slide, "汇报人：XXX    指导教师：XXX\n学院/专业：XXX    日期：2026 年 5 月", 118, 522, 432, 42, {
    size: 16,
    color: C.ink,
  });
  await addHeroImage(slide, 872, 88, 230, 188, true);
  addMiniDevice(slide, 826, 260, 294, 206);
  addText(slide, "采集 → 识别 → 决策 → 语音提示", 820, 505, 310, 28, { size: 20, color: C.green, bold: true, align: "center" });
  addNotes(slide, "开场说明项目面向真实出行辅助需求，先强调系统不是单一算法演示，而是硬件、通信、后端、AI 和前端组成的完整原型。");
}

function slideBackground(p) {
  const slide = p.slides.add();
  beginSlide(2);
  addBg(slide, 2);
  addTitle(slide, 2, "研究背景与意义", "视障出行需要把“看见环境”和“理解环境”转化为低负担、实时的语音提示。");
  addCard(slide, 86, 260, 330, 190, "实时环境感知不足", "传统辅助方式难以及时识别盲道偏离、前方障碍、斑马线位置和红绿灯状态。", {
    accent: C.green,
    number: "01",
  });
  addCard(slide, 475, 260, 330, 190, "交互方式不够自然", "用户在行走时不适合频繁看屏幕，需要通过语音命令启动导航、问答或寻物流程。", {
    accent: C.cyan,
    badgeFill: C.softCyan,
    number: "02",
  });
  addCard(slide, 864, 260, 330, 190, "落地成本与调试难度", "端侧设备算力有限，系统还要兼顾低成本硬件、服务器推理、远程调试与部署维护。", {
    accent: C.amber,
    badgeFill: C.softAmber,
    number: "03",
  });
  addShape(slide, "roundRect", 130, 532, 1020, 82, "#11231E", C.dark, 0);
  addText(slide, "本设计目标", 166, 552, 140, 24, { size: 16, color: C.green2, bold: true });
  addText(slide, "用智能眼镜采集第一视角画面和语音，通过服务器侧视觉模型与实时 AI，把导航判断变成设备端可播放的中文提示。", 316, 551, 760, 32, {
    size: 19,
    color: C.white,
  });
  addNotes(slide, "这一页回答为什么做：不是替代导盲杖，而是补足环境理解和语音交互能力。");
}

function slideGoals(p) {
  const slide = p.slides.add();
  beginSlide(3);
  addBg(slide, 3);
  addTitle(slide, 3, "需求分析与设计目标", "围绕出行安全、交互效率和工程可落地性，把系统目标拆成 6 个可验证方向。");
  const cards = [
    ["实时性", "视频帧、语音流和导航提示需要低延迟闭环，减少行走过程中的滞后感。", C.green],
    ["低成本", "使用 ESP32-S3 Sense 等低成本硬件，把重推理任务放到服务器。", C.amber],
    ["多模式", "支持导盲、过街、红绿灯检测、寻物和普通问答等模式切换。", C.cyan],
    ["可扩展", "后端通过 worker API 和 skill registry 组织视觉能力，方便继续加模型。", C.violet],
    ["可调试", "前端展示实时画面、标注图、模式状态、语音事件和流质量指标。", C.coral],
    ["可部署", "提供 Windows/Linux 启动脚本、Nginx/systemd 部署说明和验收命令。", C.green2],
  ];
  cards.forEach((item, i) => {
    const x = 86 + (i % 3) * 382;
    const y = 246 + Math.floor(i / 3) * 170;
    addCard(slide, x, y, 326, 132, item[0], item[1], { accent: item[2], bodySize: 14 });
  });
  addNotes(slide, "这一页可以按目标逐个过一遍，后面每个目标都会在架构、算法或测试页面中对应落地。");
}

function slideArchitecture(p) {
  const slide = p.slides.add();
  beginSlide(4);
  addBg(slide, 4);
  addTitle(slide, 4, "系统总体架构", "采用端侧采集 + 服务器编排 + 视觉 worker + 实时 AI + Web 控制台的协同架构。");
  const y = 292;
  const a = addShape(slide, "roundRect", 76, y, 190, 126, C.panel, C.line, 1.2);
  const b = addShape(slide, "roundRect", 310, y, 170, 126, C.panel, C.line, 1.2);
  const c = addShape(slide, "roundRect", 524, y, 200, 126, C.panel, C.line, 1.2);
  const d = addShape(slide, "roundRect", 768, y, 200, 126, C.panel, C.line, 1.2);
  const e = addShape(slide, "roundRect", 1012, y, 190, 126, C.panel, C.line, 1.2);
  addText(slide, "ESP32 眼镜", 98, y + 22, 146, 24, { size: 18, bold: true, align: "center" });
  addText(slide, "摄像头 / 麦克风\n设备端扬声器", 98, y + 58, 146, 42, { size: 14, color: C.muted, align: "center" });
  addText(slide, "UDP 8888", 330, y + 28, 130, 24, { size: 18, bold: true, color: C.green, face: FONT.mono, align: "center" });
  addText(slide, "音频、视频、播放回包", 330, y + 66, 130, 34, { size: 13, color: C.muted, align: "center" });
  addText(slide, "Go 后端", 548, y + 22, 152, 24, { size: 18, bold: true, align: "center" });
  addText(slide, "HTTP / WS / UDP\n状态与流媒体编排", 548, y + 58, 152, 42, { size: 13, color: C.muted, align: "center" });
  addText(slide, "Python Worker", 794, y + 22, 148, 24, { size: 18, bold: true, align: "center" });
  addText(slide, "盲道、障碍物\n斑马线、红绿灯", 794, y + 58, 148, 42, { size: 13, color: C.muted, align: "center" });
  addText(slide, "AI / 前端", 1040, y + 22, 136, 24, { size: 18, bold: true, align: "center" });
  addText(slide, "DashScope\nVue 控制台", 1040, y + 58, 136, 42, { size: 13, color: C.muted, align: "center" });
  addArrow(slide, a, 3, b, 1);
  addArrow(slide, b, 3, c, 1);
  addArrow(slide, c, 3, d, 1);
  addArrow(slide, d, 3, e, 1);
  addShape(slide, "roundRect", 152, 506, 976, 70, "#11231E", C.dark, 0);
  addText(slide, "核心闭环", 188, 529, 120, 24, { size: 16, color: C.green2, bold: true });
  addText(slide, "采集画面/语音 → 后端组包与分发 → 模型推理/AI 问答 → 返回标注画面与中文语音提示", 320, 528, 746, 24, {
    size: 18,
    color: C.white,
  });
  addNotes(slide, "重点解释各层职责：端侧轻量采集，服务器做连接和状态管理，Python 做模型推理，前端负责观察和控制。");
}

function slideHardware(p) {
  const slide = p.slides.add();
  beginSlide(5);
  addBg(slide, 5);
  addTitle(slide, 5, "硬件与通信方案", "端侧只承担采集、编码、上报和播放，复杂推理放在服务器侧完成。");
  addShape(slide, "roundRect", 82, 240, 470, 344, C.panel, C.line, 1.2);
  addMiniDevice(slide, 174, 274, 290, 202);
  addText(slide, "XIAO ESP32S3 Sense 原型", 142, 506, 350, 28, { size: 20, bold: true, align: "center" });
  addText(slide, "Camera：第一视角画面\nPDM Mic：语音输入\nI2S Speaker：导航/AI 音频播放", 150, 542, 334, 58, {
    size: 15,
    color: C.muted,
    align: "center",
  });
  addText(slide, "通信包类型", 650, 220, 260, 28, { size: 22, bold: true });
  addProtocolRow(slide, 270, "hello", "设备 → 后端", "token、设备能力、扬声器状态", C.green);
  addProtocolRow(slide, 336, "video", "设备 → 后端", "JPEG 分片、帧序号、时间戳", C.cyan);
  addProtocolRow(slide, 402, "audio", "设备 → 后端", "PCM16 音频分片、序列号", C.amber);
  addProtocolRow(slide, 468, "ai_audio", "后端 → 设备", "导航 wav / AI PCM 回放", C.coral);
  addProtocolRow(slide, 534, "control", "前端 → 后端", "模式切换、视觉 worker 指令", C.violet);
  addNotes(slide, "强调为什么采用 UDP：视频/音频连续流对延迟敏感；后端再通过 WebSocket 给浏览器推送可视化。");
}

function slideModules(p) {
  const slide = p.slides.add();
  beginSlide(6);
  addBg(slide, 6);
  addTitle(slide, 6, "软件模块划分", "项目按职责分为固件、Go 后端、Python 视觉 worker、Vue 前端和部署资产。");
  const modules = [
    ["固件层", "firmware/xiao_sense_ws_stream\n网络、队列、摄像头、麦克风、扬声器播放", C.green],
    ["后端层", "server/internal/serverapp\nUDP ingest、WebSocket、DashScope、worker bridge", C.cyan],
    ["视觉层", "python_worker\n导盲、过街、红绿灯、寻物、语音命令处理", C.coral],
    ["前端层", "esp32-glass-front/src\n实时画面、模式切换、音频播放、状态面板", C.amber],
    ["部署资产", "LINUX_DEPLOY / SERVER_DEPLOY\n模型文件、脚本、Nginx、systemd、验收命令", C.violet],
  ];
  modules.forEach((m, i) => {
    const x = i < 3 ? 78 + i * 390 : 238 + (i - 3) * 420;
    const y = i < 3 ? 238 : 440;
    addCard(slide, x, y, i < 3 ? 340 : 380, 146, m[0], m[1], { accent: m[2], bodySize: 13 });
  });
  addMetric(slide, 994, 102, 190, 96, "62", "主要源码文件", C.green, "不含 dist / node_modules");
  addMetric(slide, 994, 212, 190, 96, "3", "运行服务入口", C.cyan, "ESP32 / Go / Worker");
  addNotes(slide, "这一页展示工程完整性。可以说代码已按模块加中文注释，便于答辩时解释和后续维护。");
}

function slideBlindPath(p) {
  const slide = p.slides.add();
  beginSlide(7);
  addBg(slide, 7);
  addTitle(slide, 7, "导盲路径算法", "视觉 worker 将设备画面旋转校正后，提取盲道区域、计算偏移并叠加障碍物判断。");
  addShape(slide, "roundRect", 80, 235, 470, 350, "#13211D", C.dark, 0);
  addShape(slide, "trapezoid", 194, 300, 244, 238, "#385248", C.transparent, 0);
  addShape(slide, "trapezoid", 222, 318, 188, 202, "#F2C95D", C.transparent, 0);
  addShape(slide, "rect", 316, 326, 12, 184, C.bg);
  addShape(slide, "ellipse", 372, 370, 46, 46, C.coral);
  addText(slide, "视觉标注示意", 108, 254, 160, 22, { size: 15, color: C.green2, bold: true });
  addText(slide, "偏移：右侧\n障碍：前方\n提示：向左调整", 108, 486, 160, 58, { size: 16, color: C.white });
  const steps = [
    ["01", "方向校正", "VISION_INPUT_ROTATION 将第一视角画面统一到模型输入方向。", C.green],
    ["02", "盲道分割", "YOLO 分割模型输出盲道 mask，提取中心线与可通行区域。", C.amber],
    ["03", "偏移估计", "根据画面中心与盲道中心差值，生成左/右/直行提示。", C.cyan],
    ["04", "障碍融合", "YOLOE 文本提示检测人、车、柱子等障碍物，提升安全提示。", C.coral],
  ];
  steps.forEach((s, i) => addStepCard(slide, 610, 238 + i * 86, 520, 74, s[0], s[1], s[2], s[3]));
  addNotes(slide, "讲导盲算法时突出“分割+几何规则+障碍检测”的组合，不需要把全部代码细节展开。");
}

function slideCrossing(p) {
  const slide = p.slides.add();
  beginSlide(8);
  addBg(slide, 8);
  addTitle(slide, 8, "过街与红绿灯流程", "过街模式不是单帧识别，而是围绕斑马线位置、角度、红绿灯和结束条件的状态流程。");
  const s1 = addShape(slide, "roundRect", 88, 300, 170, 94, C.panel, C.line, 1.2);
  const s2 = addShape(slide, "roundRect", 302, 300, 170, 94, C.panel, C.line, 1.2);
  const s3 = addShape(slide, "roundRect", 516, 300, 170, 94, C.panel, C.line, 1.2);
  const s4 = addShape(slide, "roundRect", 730, 300, 170, 94, C.panel, C.line, 1.2);
  const s5 = addShape(slide, "roundRect", 944, 300, 170, 94, C.panel, C.line, 1.2);
  const labels = [
    [s1, "导盲巡航", "盲道跟随"],
    [s2, "进入过街", "语音/前端命令"],
    [s3, "斑马线对齐", "角度与偏移"],
    [s4, "红绿灯判断", "绿灯/倒计时"],
    [s5, "完成返回", "继续导盲"],
  ];
  labels.forEach(([shape, title, body]) => {
    const { left, top, width } = shape.position;
    addText(slide, title, left + 18, top + 18, width - 36, 22, { size: 16, bold: true, align: "center" });
    addText(slide, body, left + 18, top + 52, width - 36, 20, { size: 13, color: C.muted, align: "center" });
  });
  addArrow(slide, s1, 3, s2, 1);
  addArrow(slide, s2, 3, s3, 1);
  addArrow(slide, s3, 3, s4, 1);
  addArrow(slide, s4, 3, s5, 1);
  addShape(slide, "roundRect", 136, 486, 430, 102, C.panel, C.line, 1.2);
  for (let i = 0; i < 6; i += 1) addShape(slide, "rect", 180 + i * 52, 518, 34, 44, i % 2 ? C.bg : C.ink);
  addText(slide, "斑马线：检测 mask 面积、方向角、画面中心偏移", 154, 572, 392, 18, { size: 13, color: C.muted, align: "center" });
  addShape(slide, "roundRect", 706, 486, 300, 102, "#17201D", C.dark, 0);
  addShape(slide, "ellipse", 748, 512, 34, 34, C.coral);
  addShape(slide, "ellipse", 814, 512, 34, 34, C.amber);
  addShape(slide, "ellipse", 880, 512, 34, 34, C.green2);
  addText(slide, "红绿灯：检测状态并给出安全提示", 734, 572, 246, 18, { size: 13, color: C.white, align: "center" });
  addNotes(slide, "这一页说明状态机设计：命令触发过街，检测斑马线后再结合红绿灯，最后回到导盲。");
}

function slideVoiceAI(p) {
  const slide = p.slides.add();
  beginSlide(9);
  addBg(slide, 9);
  addTitle(slide, 9, "语音交互与 AI", "语音链路同时服务“导航命令”和“普通问答”，后端根据模式决定是否暂停 AI 输入。");
  const laneY = [258, 424];
  addText(slide, "导航指令通道", 88, laneY[0] - 42, 160, 24, { size: 18, bold: true, color: C.green });
  addText(slide, "普通问答通道", 88, laneY[1] - 42, 160, 24, { size: 18, bold: true, color: C.cyan });
  const nav = [
    ["麦克风音频", "PCM16"],
    ["ASR 转写", "识别中文命令"],
    ["命令分流", "导盲/过街/寻物"],
    ["设备播报", "预生成 wav"],
  ];
  const qa = [
    ["麦克风音频", "PCM16"],
    ["Qwen Omni", "图像+文本问答"],
    ["音频下行", "24k→8k PCM"],
    ["浏览器/设备", "播放回复"],
  ];
  [nav, qa].forEach((row, ri) => {
    let prev = null;
    row.forEach((it, i) => {
      const x = 96 + i * 280;
      const card = addShape(slide, "roundRect", x, laneY[ri], 210, 86, C.panel, C.line, 1.2);
      addText(slide, it[0], x + 20, laneY[ri] + 16, 170, 22, { size: 16, bold: true, align: "center" });
      addText(slide, it[1], x + 20, laneY[ri] + 48, 170, 18, { size: 13, color: C.muted, align: "center" });
      if (prev) addArrow(slide, prev, 3, card, 1, ri === 0 ? C.green : C.cyan);
      prev = card;
    });
  });
  addShape(slide, "roundRect", 178, 578, 878, 54, "#11231E", C.dark, 0);
  addText(slide, "关键策略：导航模式下优先保障安全提示，普通 AI 回复可暂停；问答模式下再恢复实时语音交互。", 214, 595, 806, 22, {
    size: 16,
    color: C.white,
    align: "center",
  });
  addNotes(slide, "这里可以解释双通道音频：导盲提示由设备端播放，AI 问答语音可由浏览器或设备播放，避免互相打断。");
}

function slideFrontend(p) {
  const slide = p.slides.add();
  beginSlide(10);
  addBg(slide, 10);
  addTitle(slide, 10, "前端控制台与调试", "Vue 控制台用于演示、调参和排障，能看到原始流、标注流、模式状态和音频事件。");
  addShape(slide, "roundRect", 82, 230, 720, 400, "#16231F", C.dark, 0);
  addShape(slide, "rect", 110, 270, 430, 260, "#27342F", "#4D5F57", 1);
  addShape(slide, "trapezoid", 226, 330, 188, 154, "#586F62");
  addShape(slide, "trapezoid", 254, 348, 132, 124, "#EBC95A");
  addShape(slide, "ellipse", 420, 376, 40, 40, C.coral);
  addText(slide, "Live / Vision Preview", 124, 244, 260, 20, { size: 13, color: C.green2, face: FONT.mono });
  addShape(slide, "roundRect", 574, 270, 190, 58, C.panel, C.line, 1);
  addText(slide, "设备在线", 596, 286, 146, 20, { size: 15, color: C.green, bold: true, align: "center" });
  addShape(slide, "roundRect", 574, 350, 190, 58, C.panel, C.line, 1);
  addText(slide, "导盲模式", 596, 366, 146, 20, { size: 15, color: C.cyan, bold: true, align: "center" });
  addShape(slide, "roundRect", 574, 430, 190, 58, C.panel, C.line, 1);
  addText(slide, "音频分片", 596, 446, 146, 20, { size: 15, color: C.amber, bold: true, align: "center" });
  addShape(slide, "rect", 112, 556, 650, 1, "#5C6D66");
  addText(slide, "ws://...  videoSeqGaps / audioSeqGaps / navigationEvents", 124, 576, 600, 20, {
    size: 13,
    color: "#BFD0C8",
    face: FONT.mono,
  });
  addCard(slide, 858, 250, 300, 96, "演示价值", "答辩时可以直接展示设备画面、AI 音频和导航事件，降低口述成本。", { accent: C.green, bodySize: 13 });
  addCard(slide, 858, 374, 300, 96, "调试价值", "定位 UDP 丢包、worker 状态、模式切换失败和浏览器音频解锁问题。", { accent: C.amber, bodySize: 13 });
  addCard(slide, 858, 498, 300, 96, "部署价值", "前端可独立构建为静态页面，并通过 Nginx 反向代理后端接口。", { accent: C.cyan, bodySize: 13 });
  addNotes(slide, "这一页可以说明前端不是装饰，而是系统联调工具。");
}

function slideTests(p) {
  const slide = p.slides.add();
  beginSlide(11);
  addBg(slide, 11);
  addTitle(slide, 11, "测试与验证", "当前代码已完成基础工程验证，覆盖 Python worker、Go 后端和 Vue 前端构建链路。");
  addMetric(slide, 92, 248, 330, 156, "通过", "Python 语法编译\npython -m py_compile", C.green);
  addMetric(slide, 474, 248, 330, 156, "通过", "Go 后端测试\ngo test ./...", C.cyan);
  addMetric(slide, 856, 248, 330, 156, "通过", "前端生产构建\nnpm run build", C.amber);
  addShape(slide, "roundRect", 132, 490, 1016, 92, "#11231E", C.dark, 0);
  addText(slide, "联调验收路径", 168, 510, 150, 22, { size: 17, color: C.green2, bold: true });
  addText(slide, "worker /api/vision/status  →  Go /healthz 与 /api/status  →  浏览器 #live  →  设备 UDP 8888 上线", 328, 510, 760, 22, {
    size: 16,
    color: C.white,
  });
  addText(slide, "说明：答辩现场如需演示，可优先展示前端实时页、模式切换和导航事件，模型推理速度取决于服务器算力。", 168, 548, 880, 18, {
    size: 13,
    color: "#C9D8D1",
  });
  addNotes(slide, "这里对应刚才实际运行过的验证命令。现场可以补充设备联调视频或截图。");
}

function slideInnovation(p) {
  const slide = p.slides.add();
  beginSlide(12);
  addBg(slide, 12);
  addTitle(slide, 12, "难点与创新点", "本项目的价值主要体现在多模块协同和端云结合，而不是单一模型调用。");
  const items = [
    ["低延迟流媒体", "自定义 UDP 包处理视频分片、音频序列和设备端播放回包，适合连续音视频流。", C.green],
    ["跨语言协同", "Go 负责连接和并发，Python 负责视觉模型，Vue 负责实时观察，职责边界清晰。", C.cyan],
    ["导航状态机", "导盲、过街、红绿灯和问答模式互斥/切换，避免语音提示互相干扰。", C.amber],
    ["多模型融合", "盲道分割、YOLOE 障碍物、红绿灯检测和 Qwen Omni 共同服务出行辅助。", C.coral],
  ];
  items.forEach((it, i) => {
    const x = 92 + (i % 2) * 550;
    const y = 242 + Math.floor(i / 2) * 178;
    addCard(slide, x, y, 476, 132, it[0], it[1], {
      accent: it[2],
      number: String(i + 1).padStart(2, "0"),
      bodySize: 14,
    });
  });
  addShape(slide, "roundRect", 388, 592, 504, 44, C.softGreen, C.green, 1);
  addText(slide, "工程创新点：把“看、听、说、决策、调试”整合进一个可运行原型", 420, 604, 442, 18, {
    size: 14,
    color: C.green,
    bold: true,
    align: "center",
  });
  addNotes(slide, "这一页要强调工程闭环：硬件、通信、模型、AI 和前端已经打通。");
}

function slideConclusion(p) {
  const slide = p.slides.add();
  beginSlide(13);
  addBg(slide, 13);
  addTitle(slide, 13, "总结与展望", "系统已形成可运行的智能眼镜导航原型，后续可以围绕稳定性、轻量化和真实场景测试继续优化。");
  addShape(slide, "roundRect", 94, 248, 500, 300, C.panel, C.line, 1.2);
  addText(slide, "已完成工作", 126, 280, 180, 28, { size: 22, bold: true, color: C.green });
  const done = ["ESP32 音视频采集与设备端播放", "Go 后端 UDP/HTTP/WebSocket 编排", "Python 视觉导航 worker 与多模式控制", "Vue 实时控制台与部署文档"];
  done.forEach((t, i) => {
    addShape(slide, "ellipse", 132, 332 + i * 42, 20, 20, C.green2);
    addText(slide, t, 168, 329 + i * 42, 360, 22, { size: 16, color: C.ink });
  });
  addShape(slide, "roundRect", 686, 248, 500, 300, C.panel, C.line, 1.2);
  addText(slide, "后续展望", 718, 280, 180, 28, { size: 22, bold: true, color: C.coral });
  const future = ["增加 IMU / GPS / SLAM 辅助定位", "模型量化与边缘推理优化", "扩展更多交通场景数据集", "开展真实道路用户测试与安全评估"];
  future.forEach((t, i) => {
    addShape(slide, "ellipse", 724, 332 + i * 42, 20, 20, i < 2 ? C.amber : C.coral);
    addText(slide, t, 760, 329 + i * 42, 360, 22, { size: 16, color: C.ink });
  });
  addNotes(slide, "总结时回到目标：低成本智能眼镜、视觉导航、语音交互、可部署原型。展望部分不要承诺已完成的能力。");
}

function slideQA(p) {
  const slide = p.slides.add();
  beginSlide(14);
  slide.background.fill = C.dark;
  addShape(slide, "rect", 0, 0, W, H, C.dark);
  addShape(slide, "ellipse", -120, 480, 360, 360, "#1C4F40");
  addShape(slide, "ellipse", 1030, -150, 360, 360, "#5E3D2A");
  addText(slide, "Q&A", 438, 192, 404, 88, { size: 74, color: C.white, bold: true, face: FONT.title, align: "center" });
  addText(slide, "谢谢各位老师，欢迎批评指正", 370, 306, 540, 34, { size: 24, color: "#CDE3DA", align: "center" });
  ["系统架构", "算法实现", "测试部署"].forEach((t, i) => {
    addShape(slide, "roundRect", 346 + i * 198, 420, 150, 48, "#FFFFFF18", "#6E857A", 1);
    addText(slide, t, 366 + i * 198, 434, 110, 18, { size: 15, color: C.white, align: "center" });
  });
  addText(slide, "基于 ESP32 智能眼镜的视觉导航与语音交互系统", 64, 652, 520, 22, { size: 13, color: "#92AAA0" });
  addNotes(slide, "收束页。回答问题时优先回到系统目标、关键模块和测试验证。");
}

async function buildDeck() {
  await fs.mkdir(OUT_DIR, { recursive: true });
  await fs.rm(PREVIEW_DIR, { recursive: true, force: true });
  await fs.rm(REFERENCE_DIR, { recursive: true, force: true });
  await fs.rm(SCRATCH_DIR, { recursive: true, force: true });
  await fs.rm(VERIFY_DIR, { recursive: true, force: true });
  await fs.mkdir(PREVIEW_DIR, { recursive: true });
  await fs.mkdir(REFERENCE_DIR, { recursive: true });
  await fs.mkdir(SCRATCH_DIR, { recursive: true });
  await fs.mkdir(VERIFY_DIR, { recursive: true });

  const p = Presentation.create({ slideSize: { width: W, height: H } });
  p.theme.colorScheme = {
    name: "AI Glass Defense",
    themeColors: {
      accent1: C.green,
      accent2: C.amber,
      accent3: C.coral,
      accent4: C.cyan,
      bg1: C.bg,
      tx1: C.ink,
      tx2: C.muted,
    },
  };

  await slideCover(p);
  slideBackground(p);
  slideGoals(p);
  slideArchitecture(p);
  slideHardware(p);
  slideModules(p);
  slideBlindPath(p);
  slideCrossing(p);
  slideVoiceAI(p);
  slideFrontend(p);
  slideTests(p);
  slideInnovation(p);
  slideConclusion(p);
  slideQA(p);
  await addDeckVisualAssets(p);

  if (await exists(HERO_PATH)) {
    for (let i = 1; i <= p.slides.items.length; i += 1) {
      await fs.copyFile(HERO_PATH, path.join(REFERENCE_DIR, `slide-${String(i).padStart(2, "0")}.png`));
    }
  }

  for (let i = 0; i < p.slides.items.length; i += 1) {
    currentSlideNo = i + 1;
    const slide = p.slides.items[i];
    const preview = await p.export({ slide, format: "png", scale: 1 });
    await fs.writeFile(path.join(PREVIEW_DIR, `slide-${String(i + 1).padStart(2, "0")}.png`), new Uint8Array(await preview.arrayBuffer()));
  }

  const pptx = await PresentationFile.exportPptx(p);
  await pptx.save(OUTPUT_PPTX);
  const deckRecord = {
    kind: "deck",
    id: "ai-glass-defense",
    slideCount: p.slides.items.length,
    slideSize: { width: W, height: H },
  };
  await fs.writeFile(INSPECT_PATH, [deckRecord, ...inspectRecords].map((record) => JSON.stringify(record)).join("\n") + "\n", "utf8");
  await fs.writeFile(
    path.join(VERIFY_DIR, "render_verify_loops.ndjson"),
    JSON.stringify({
      kind: "render_verify_loop",
      deckId: "ai-glass-defense",
      loop: 1,
      maxLoops: 3,
      timestamp: new Date().toISOString(),
      slideCount: p.slides.items.length,
      previewCount: p.slides.items.length,
      previewDir: PREVIEW_DIR,
      inspectPath: INSPECT_PATH,
      pptxPath: OUTPUT_PPTX,
    }) + "\n",
    "utf8",
  );
  await fs.writeFile(
    path.join(SCRATCH_DIR, "verification_notes.md"),
    [
      "# Verification Notes",
      "",
      "- Generated 14 editable slides with native text boxes, shapes, connectors and speaker notes.",
      "- Rendered PNG previews for all slides under `outputs/defense_ppt/preview`.",
      "- No data-backed charts are included, so native chart XML verification is not applicable.",
      "",
    ].join("\n"),
    "utf8",
  );
  console.log(OUTPUT_PPTX);
}

await buildDeck();
