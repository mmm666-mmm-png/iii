package serverapp

import (
	"context"
	"encoding/json"
	"io"
	"log"
	"net"
	"net/http"
	"strings"
	"sync"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/gorilla/websocket"
)

const deviceReadTimeout = 2 * time.Minute
const deviceActiveWindow = 6 * time.Second

// 浏览器 viewer WebSocket keepalive：定期 ping，浏览器会自动回 pong，
// 借此识别半开连接并干净关闭，避免连接被 RST 后代理端报 ECONNRESET。
const (
	viewerPongWait   = 60 * time.Second
	viewerPingPeriod = 30 * time.Second
	viewerWriteWait  = 10 * time.Second
)

// DeviceHello 是设备 hello JSON 的服务端结构。ESP32 UDP hello 与旧 WebSocket
// hello 都会被归一化到这个结构，再广播给前端。
type DeviceHello struct {
	Type     string        `json:"type"`
	DeviceID string        `json:"deviceId"`
	Firmware string        `json:"firmware"`
	Video    HelloVideo    `json:"video"`
	Audio    HelloAudio    `json:"audio"`
	Speaker  *HelloSpeaker `json:"speaker,omitempty"`
}

type HelloVideo struct {
	Format     string `json:"format"`
	Width      int    `json:"width"`
	Height     int    `json:"height"`
	Quality    int    `json:"quality"`
	IntervalMs int    `json:"intervalMs"`
}

type HelloAudio struct {
	SampleRate    int `json:"sampleRate"`
	Channels      int `json:"channels"`
	BitsPerSample int `json:"bitsPerSample"`
	ChunkSamples  int `json:"chunkSamples"`
}

type HelloSpeaker struct {
	Enabled       bool `json:"enabled"`
	AudioPort     int  `json:"audioPort"`
	SampleRate    int  `json:"sampleRate"`
	Channels      int  `json:"channels"`
	BitsPerSample int  `json:"bitsPerSample"`
}

type PacketMeta struct {
	// Type 取值包括 video、audio、ai_audio、vision_video。
	// 前端收到 JSON 元数据后，会等待紧跟着的二进制载荷。
	Type          string `json:"type"`
	Seq           uint64 `json:"seq"`
	TimestampMs   uint64 `json:"timestampMs"`
	Bytes         int    `json:"bytes"`
	ResponseID    string `json:"responseId,omitempty"`
	Width         int    `json:"width,omitempty"`
	Height        int    `json:"height,omitempty"`
	Format        string `json:"format,omitempty"`
	SampleRate    int    `json:"sampleRate,omitempty"`
	Channels      int    `json:"channels,omitempty"`
	BitsPerSample int    `json:"bitsPerSample,omitempty"`
}

type aiModeRequest struct {
	Mode string `json:"mode"`
}

type ServerState struct {
	// Type 固定为 server_state，方便浏览器 WebSocket 按事件类型分发。
	Type             string              `json:"type"`
	DeviceConnected  bool                `json:"deviceConnected"`
	Device           *DeviceHello        `json:"device,omitempty"`
	LastDeviceSeen   string              `json:"lastDeviceSeen,omitempty"`
	ViewerCount      int                 `json:"viewerCount"`
	VideoFramesSeen  uint64              `json:"videoFramesSeen"`
	AudioChunksSeen  uint64              `json:"audioChunksSeen"`
	LatestVideoBytes int                 `json:"latestVideoBytes,omitempty"`
	Quality          streamQualityStats  `json:"quality"`
	Vision           *visionStateMessage `json:"vision,omitempty"`
	AI               *aiStateMessage     `json:"ai,omitempty"`
}

type viewerClient struct {
	conn *websocket.Conn
	mu   sync.Mutex
}

// send 为单个 viewer 连接串行化写操作，避免多个 goroutine 广播时把
// 状态、元数据和二进制消息写乱顺序。
func (v *viewerClient) send(messageType int, payload []byte) error {
	v.mu.Lock()
	defer v.mu.Unlock()

	_ = v.conn.SetWriteDeadline(time.Now().Add(5 * time.Second))
	return v.conn.WriteMessage(messageType, payload)
}

type streamHub struct {
	mu               sync.RWMutex
	viewers          map[*viewerClient]struct{}
	device           *websocket.Conn
	deviceHello      *DeviceHello
	lastDeviceSeen   time.Time
	videoFramesSeen  uint64
	audioChunksSeen  uint64
	latestVideoMeta  *PacketMeta
	latestJPEG       []byte
	latestVisionMeta *PacketMeta
	latestVisionJPEG []byte
}

// streamHub 持有整条直播链路的共享状态，并统一扇出给 HTTP 和
// WebSocket 客户端。设备端和浏览器端都通过它交换数据。
func newStreamHub() *streamHub {
	return &streamHub{
		viewers: make(map[*viewerClient]struct{}),
	}
}

func (h *streamHub) addViewer(viewer *viewerClient) {
	h.mu.Lock()
	defer h.mu.Unlock()
	h.viewers[viewer] = struct{}{}
}

func (h *streamHub) removeViewer(viewer *viewerClient) {
	h.mu.Lock()
	defer h.mu.Unlock()
	delete(h.viewers, viewer)
}

func (h *streamHub) snapshot() ServerState {
	// snapshot 只返回 hub 内部状态；质量统计和 vision 状态由 server.currentState 追加。
	h.mu.RLock()
	defer h.mu.RUnlock()

	state := ServerState{
		Type:            "server_state",
		DeviceConnected: h.deviceConnectedLocked(),
		Device:          h.deviceHello,
		ViewerCount:     len(h.viewers),
		VideoFramesSeen: h.videoFramesSeen,
		AudioChunksSeen: h.audioChunksSeen,
	}
	if !h.lastDeviceSeen.IsZero() {
		state.LastDeviceSeen = h.lastDeviceSeen.Format(time.RFC3339)
	}
	if h.latestVideoMeta != nil {
		state.LatestVideoBytes = h.latestVideoMeta.Bytes
	}

	return state
}

func (h *streamHub) deviceConnectedLocked() bool {
	if h.device != nil {
		return true
	}
	if h.lastDeviceSeen.IsZero() {
		return false
	}
	return time.Since(h.lastDeviceSeen) < deviceActiveWindow
}

func (h *streamHub) snapshotLatestVideo() (*PacketMeta, []byte) {
	h.mu.RLock()
	defer h.mu.RUnlock()

	if h.latestVideoMeta == nil || len(h.latestJPEG) == 0 {
		return nil, nil
	}

	// 返回副本，避免调用方在取快照或回传图片时与下一帧写入发生竞争。
	metaCopy := *h.latestVideoMeta
	frameCopy := append([]byte(nil), h.latestJPEG...)
	return &metaCopy, frameCopy
}

func (h *streamHub) snapshotLatestVisionVideo() (*PacketMeta, []byte) {
	h.mu.RLock()
	defer h.mu.RUnlock()

	if h.latestVisionMeta == nil || len(h.latestVisionJPEG) == 0 {
		return nil, nil
	}

	metaCopy := *h.latestVisionMeta
	frameCopy := append([]byte(nil), h.latestVisionJPEG...)
	return &metaCopy, frameCopy
}

func (h *streamHub) setDevice(conn *websocket.Conn) (old *websocket.Conn) {
	h.mu.Lock()
	defer h.mu.Unlock()
	old = h.device
	h.device = conn
	h.lastDeviceSeen = time.Now()
	return old
}

func (h *streamHub) clearDevice(conn *websocket.Conn) {
	h.mu.Lock()
	defer h.mu.Unlock()
	if h.device == conn {
		h.device = nil
	}
}

func (h *streamHub) storeHello(hello *DeviceHello) {
	h.mu.Lock()
	defer h.mu.Unlock()
	h.deviceHello = hello
	h.lastDeviceSeen = time.Now()
}

func (h *streamHub) storePacket(meta *PacketMeta, payload []byte) {
	h.mu.Lock()
	defer h.mu.Unlock()

	h.lastDeviceSeen = time.Now()

	switch meta.Type {
	case "video":
		// 保存最近一帧 JPEG，供 /snapshot.jpg 和新 viewer 首帧补发使用。
		h.videoFramesSeen++
		metaCopy := *meta
		h.latestVideoMeta = &metaCopy
		h.latestJPEG = append(h.latestJPEG[:0], payload...)
	case "audio":
		h.audioChunksSeen++
	}
}

func (h *streamHub) storeVisionFrame(meta *PacketMeta, payload []byte) {
	// Python worker 返回的标注图单独保存，避免覆盖原始设备画面。
	h.mu.Lock()
	defer h.mu.Unlock()
	metaCopy := *meta
	h.latestVisionMeta = &metaCopy
	h.latestVisionJPEG = append(h.latestVisionJPEG[:0], payload...)
}

func (h *streamHub) viewersSnapshot() []*viewerClient {
	h.mu.RLock()
	defer h.mu.RUnlock()

	viewers := make([]*viewerClient, 0, len(h.viewers))
	for viewer := range h.viewers {
		viewers = append(viewers, viewer)
	}
	return viewers
}

func (h *streamHub) broadcastText(payload []byte) {
	// 广播失败通常说明浏览器已断开，关闭并移除即可。
	for _, viewer := range h.viewersSnapshot() {
		if err := viewer.send(websocket.TextMessage, payload); err != nil {
			log.Printf("viewer text write failed: %v", err)
			_ = viewer.conn.Close()
			h.removeViewer(viewer)
		}
	}
}

func (h *streamHub) broadcastBinary(payload []byte) {
	for _, viewer := range h.viewersSnapshot() {
		if err := viewer.send(websocket.BinaryMessage, payload); err != nil {
			log.Printf("viewer binary write failed: %v", err)
			_ = viewer.conn.Close()
			h.removeViewer(viewer)
		}
	}
}

func (h *streamHub) broadcastState() {
	h.mu.RLock()
	hasViewers := len(h.viewers) > 0
	h.mu.RUnlock()
	if !hasViewers {
		return
	}

	stateBytes, err := json.Marshal(h.snapshot())
	if err != nil {
		log.Printf("failed to marshal state: %v", err)
		return
	}
	h.broadcastText(stateBytes)
}

type server struct {
	// server 聚合所有子系统：设备/浏览器 hub、DashScope 实时桥、Python 视觉 worker、
	// 本地工具技能、质量统计和设备端语音回放。
	hub                   *streamHub
	deviceToken           string
	allowOrigin           string
	ai                    *dashScopeBridge
	vision                *visionWorker
	skills                *skillRegistry
	stats                 *streamStatsTracker
	navigationVoice       *navigationVoiceLibrary
	upgrader              websocket.Upgrader
	devicePlaybackMu      sync.RWMutex
	devicePlaybackConn    net.PacketConn
	devicePlaybackAddr    *net.UDPAddr
	devicePlaybackSeq     uint32
	devicePlaybackCh      chan devicePlaybackChunk
	devicePlaybackQueueMu sync.Mutex
	// publishMu 保证发给浏览器的顺序始终是“JSON 元数据在前，二进制载荷在后”，
	// 前端就是靠这个顺序把视频帧和音频块配对起来的。
	publishMu sync.Mutex
	// voiceMode 区分语音交互当前处于哪种模式：
	//   chat        —— 自由文本交给 Qwen Omni 聊天；
	//   navigation  —— 自由文本交给高德路线规划。
	// 用户可用语音口令（“聊天”/“导航模式”）自由切换，两种模式互不干扰。
	voiceModeMu sync.RWMutex
	voiceMode   string
}

func newServer(deviceToken, allowOrigin string) *server {
	// 创建时立即启动状态心跳和设备回放循环；HTTP/UDP 监听在 Run 中启动。
	s := &server{
		hub:              newStreamHub(),
		deviceToken:      deviceToken,
		allowOrigin:      allowOrigin,
		stats:            newStreamStatsTracker(),
		devicePlaybackCh: make(chan devicePlaybackChunk, 256),
		voiceMode:        "chat",
		upgrader: websocket.Upgrader{
			CheckOrigin: func(r *http.Request) bool { return true },
		},
	}
	s.skills = newSkillRegistry(s)
	go s.stateTicker()
	go s.devicePlaybackLoop()
	return s
}

func Run(cfg Config) error {
	// 按配置选择性启用视觉 worker、DashScope AI 和本地导航预录语音。
	app := newServer(cfg.DeviceToken, cfg.CORSAllowOrigin)
	if cfg.VisionWorkerURL != "" {
		vision, err := newVisionWorker(app, cfg.VisionWorkerURL, time.Duration(cfg.VisionFrameIntervalMs)*time.Millisecond)
		if err != nil {
			return err
		}
		app.vision = vision
	}
	if cfg.DashScopeAPIKey != "" {
		app.ai = newDashScopeBridge(app, dashScopeConfig{
			APIKey:       cfg.DashScopeAPIKey,
			Region:       cfg.DashScopeRegion,
			Model:        cfg.DashScopeModel,
			Voice:        cfg.DashScopeVoice,
			Instructions: cfg.DashScopeInstructions,
			EnableSearch: cfg.DashScopeEnableSearch,
		})
	}
	if cfg.NavigationVoiceDir != "" {
		navigationVoice, err := newNavigationVoiceLibrary(cfg.NavigationVoiceDir)
		if err != nil {
			log.Printf("navigation voice disabled: %v", err)
		} else {
			app.navigationVoice = navigationVoice
			log.Printf("navigation voice loaded from %s", cfg.NavigationVoiceDir)
		}
	}

	go app.listenUDP(cfg.UDPAddr)

	router := app.newRouter()
	log.Printf("http listening on %s", cfg.HTTPAddr)
	return router.Run(cfg.HTTPAddr)
}

func (s *server) broadcastJSON(message any) {
	// 所有服务端事件最终都通过 viewer WebSocket 文本消息广播。
	payload, err := json.Marshal(message)
	if err != nil {
		log.Printf("failed to marshal broadcast message: %v", err)
		return
	}
	s.hub.broadcastText(payload)
}

func (s *server) stateTicker() {
	// 定时推送状态，前端即使没有新帧也能刷新在线/质量信息。
	ticker := time.NewTicker(time.Second)
	defer ticker.Stop()

	for range ticker.C {
		s.hub.broadcastState()
	}
}

func (s *server) publishHello(hello *DeviceHello) {
	s.hub.storeHello(hello)
	if helloBytes, err := json.Marshal(hello); err == nil {
		s.hub.broadcastText(helloBytes)
	}
	s.hub.broadcastState()
}

func (s *server) publishPacket(meta *PacketMeta, payload []byte) {
	// 导航模式暂停 AI 输入时，丢弃 AI 下行音频，避免设备端继续播放问答语音。
	if meta != nil && meta.Type == "ai_audio" && s.ai != nil && s.ai.isInputPaused() {
		return
	}

	s.publishMu.Lock()
	defer s.publishMu.Unlock()

	s.hub.storePacket(meta, payload)

	metaBytes, err := json.Marshal(meta)
	if err != nil {
		log.Printf("failed to marshal packet meta: %v", err)
		return
	}

	s.hub.broadcastText(metaBytes)
	s.hub.broadcastBinary(payload)

	if meta.Type == "ai_audio" {
		// 浏览器播放 AI 音频的同时，也把 PCM16 分片转发给 ESP32 扬声器。
		s.enqueueAIAudioForDevice(meta, payload)
	}

	if s.ai != nil && (meta.Type == "audio" || meta.Type == "video") {
		// 原始设备音视频送入 DashScope Realtime；它负责语音识别和多模态回答。
		s.ai.ingestPacket(meta, payload)
	}
	if s.vision != nil && meta.Type == "video" {
		// 原始视频帧按限速送给 Python worker 做导航视觉处理。
		s.vision.ingestFrame(meta, payload)
	}
}

func (s *server) publishVisionFrame(meta *PacketMeta, payload []byte) {
	// 视觉标注帧使用 vision_video 类型，前端可以在原始流和标注流之间切换。
	if meta == nil || len(payload) == 0 {
		return
	}
	s.publishMu.Lock()
	defer s.publishMu.Unlock()

	s.hub.storeVisionFrame(meta, payload)
	metaBytes, err := json.Marshal(meta)
	if err != nil {
		log.Printf("failed to marshal vision frame meta: %v", err)
		return
	}
	s.hub.broadcastText(metaBytes)
	s.hub.broadcastBinary(payload)
}

func (s *server) currentState() ServerState {
	// 组合 hub、质量统计和 vision 状态，作为 /api/status 与 WebSocket 心跳的统一响应。
	state := s.hub.snapshot()
	if s.stats != nil {
		state.Quality = s.stats.snapshot()
	}
	if s.vision != nil {
		visionState := s.vision.snapshot()
		state.Vision = &visionState
	}
	if s.ai != nil {
		aiState := s.ai.snapshot()
		state.AI = &aiState
	}
	return state
}

func (s *server) newRouter() *gin.Engine {
	// Gin 只负责 HTTP/WebSocket 边界；核心状态和流转逻辑在 server 方法中。
	router := gin.New()
	router.Use(gin.Recovery(), s.corsMiddleware())

	router.GET("/", s.handleRoot)
	router.GET("/healthz", s.handleHealth)
	router.GET("/api/status", s.handleStatus)
	router.GET("/api/skills", s.handleSkills)
	router.POST("/api/ai/mode", s.handleAIMode)
	router.POST("/api/navigation/plan", s.handleNavigationPlan)
	router.POST("/api/navigation/voice", s.handleNavigationVoice)
	router.POST("/api/navigation/broadcast", s.handleNavigationBroadcast)
	router.GET("/api/navigation/status", s.handleNavigationStatus)
	router.POST("/api/obstacle/report", s.handleObstacleReport)
	router.GET("/api/obstacle/hotspots", s.handleObstacleHotspots)
	router.GET("/api/vision/status", s.handleVisionStatus)
	router.POST("/api/vision/control", s.handleVisionControl)
	router.POST("/api/voice/interrupt", s.handleVoiceInterrupt)
	router.POST("/api/gps/update", s.handleGpsUpdate)
	router.GET("/api/debug/speaker-test", s.handleSpeakerTest)
	router.POST("/api/debug/speaker-test", s.handleSpeakerTest)
	router.GET("/snapshot.jpg", s.handleSnapshot)
	router.GET("/vision-snapshot.jpg", s.handleVisionSnapshot)
	router.GET("/ws/device", s.handleDeviceWS)
	router.GET("/ws/view", s.handleViewerWS)

	return router
}

func (s *server) handleRoot(c *gin.Context) {
	c.JSON(http.StatusOK, map[string]any{
		"name":    "xiao-stream-backend",
		"message": "Frontend is served separately. Use /api/status, /snapshot.jpg, /ws/view and /ws/device.",
		"routes": map[string]string{
			"status":    "/api/status",
			"snapshot":  "/snapshot.jpg",
			"viewer_ws": "/ws/view",
			"device_ws": "/ws/device",
			"health":    "/healthz",
		},
	})
}

func (s *server) handleHealth(c *gin.Context) {
	c.JSON(http.StatusOK, map[string]any{
		"ok":   true,
		"time": time.Now().Format(time.RFC3339),
	})
}

func (s *server) handleSnapshot(c *gin.Context) {
	meta, frame := s.hub.snapshotLatestVideo()
	if meta == nil || len(frame) == 0 {
		c.String(http.StatusNotFound, "no frame yet")
		return
	}

	if meta.Format == "jpeg" {
		c.Header("Content-Type", "image/jpeg")
	} else {
		c.Header("Content-Type", "application/octet-stream")
	}
	c.Header("Cache-Control", "no-store")
	_, _ = c.Writer.Write(frame)
}

func (s *server) handleVisionSnapshot(c *gin.Context) {
	meta, frame := s.hub.snapshotLatestVisionVideo()
	if meta == nil || len(frame) == 0 {
		c.String(http.StatusNotFound, "no vision frame yet")
		return
	}

	if meta.Format == "jpeg" {
		c.Header("Content-Type", "image/jpeg")
	} else {
		c.Header("Content-Type", "application/octet-stream")
	}
	c.Header("Cache-Control", "no-store")
	_, _ = c.Writer.Write(frame)
}

func (s *server) handleStatus(c *gin.Context) {
	c.JSON(http.StatusOK, s.currentState())
}

func (s *server) handleSkills(c *gin.Context) {
	c.JSON(http.StatusOK, map[string]any{
		"items": s.skills.descriptors(),
	})
}

func (s *server) handleAIMode(c *gin.Context) {
	// navigation 表示导盲模式：保留 ASR，仅屏蔽普通 AI 回答，允许识别模式切换命令。
	var payload aiModeRequest
	if err := c.ShouldBindJSON(&payload); err != nil && err != io.EOF {
		c.String(http.StatusBadRequest, err.Error())
		return
	}

	mode := normalizeWorkMode(payload.Mode)
	if mode == "" {
		c.JSON(http.StatusBadRequest, map[string]any{
			"ok":    false,
			"error": "mode must be qa or navigation",
		})
		return
	}

	paused := mode == "navigation"
	s.setVoiceMode(mode)
	s.setAIInputPaused(paused)
	if s.ai == nil {
		c.JSON(http.StatusOK, map[string]any{
			"ok":   true,
			"mode": mode,
			"ai": aiStateMessage{
				Type:        "ai_state",
				Enabled:     false,
				Mode:        mode,
				InputPaused: paused,
			},
		})
		return
	}

	state := s.ai.snapshot()
	c.JSON(http.StatusOK, map[string]any{
		"ok":   true,
		"mode": state.Mode,
		"ai":   state,
	})
}

func (s *server) handleVisionStatus(c *gin.Context) {
	if s.vision == nil {
		c.JSON(http.StatusOK, visionStateMessage{Type: "vision_state", Enabled: false})
		return
	}
	c.JSON(http.StatusOK, s.vision.snapshot())
}

func (s *server) handleVisionControl(c *gin.Context) {
	// 转发前端视觉控制到 Python worker；AI 是否暂停由语音工作模式统一决定。
	if s.vision == nil {
		c.String(http.StatusServiceUnavailable, "vision worker is not configured")
		return
	}
	var payload visionControlRequest
	if err := c.ShouldBindJSON(&payload); err != nil && err != io.EOF {
		c.String(http.StatusBadRequest, err.Error())
		return
	}
	command := strings.TrimSpace(payload.Command)
	resumesAI := isStopVisionCommand(command)
	ctx, cancel := context.WithTimeout(c.Request.Context(), visionRequestTimeout)
	defer cancel()
	response, err := s.vision.control(ctx, command, payload.Target)
	if err != nil {
		c.JSON(http.StatusBadGateway, map[string]any{
			"ok":    false,
			"error": err.Error(),
			"state": s.vision.snapshot(),
		})
		return
	}
	if resumesAI {
		s.setAIInputPaused(false)
	}
	c.JSON(http.StatusOK, response)
}

func (s *server) handleVoiceInterrupt(c *gin.Context) {
	// Python 语音会话管理器的 stop_tts 会 POST 到这里，请求中断当前 AI 语音输出：
	// 取消 DashScope 回答、丢弃待发音频并清空设备播放队列。
	if s.ai != nil {
		s.ai.InterruptAudio()
	}
	c.JSON(http.StatusOK, map[string]any{"ok": true})
}

func (s *server) handleGpsUpdate(c *gin.Context) {
	// 手机浏览器上报的实时 GPS 坐标（WGS-84），转发给 Python worker 做路段匹配与逐段播报。
	// 始终返回 200，避免偶发异常导致手机端定位上报断开。
	if s.vision == nil {
		c.JSON(http.StatusOK, map[string]any{"ok": true})
		return
	}
	var payload struct {
		Lat      float64 `json:"lat"`
		Lng      float64 `json:"lng"`
		Accuracy float64 `json:"accuracy"`
	}
	if err := c.ShouldBindJSON(&payload); err != nil && err != io.EOF {
		c.JSON(http.StatusOK, map[string]any{"ok": false})
		return
	}
	result, err := s.vision.gpsUpdate(payload.Lat, payload.Lng, payload.Accuracy)
	if err != nil {
		c.JSON(http.StatusOK, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	c.JSON(http.StatusOK, map[string]any{"ok": true, "data": result})
}

func (s *server) setAIInputPaused(paused bool) {
	if s.ai != nil {
		s.ai.setInputPaused(paused)
	}
}

func (s *server) getVoiceMode() string {
	s.voiceModeMu.RLock()
	defer s.voiceModeMu.RUnlock()
	if s.voiceMode == "" {
		return "chat"
	}
	return s.voiceMode
}

func (s *server) setVoiceMode(mode string) {
	if mode != "chat" && mode != "navigation" {
		return
	}
	s.voiceModeMu.Lock()
	changed := s.voiceMode != mode
	s.voiceMode = mode
	s.voiceModeMu.Unlock()
	// 语音模式变化后立即广播 ai_state，让前端“语音交互”卡片实时切换为千问聊天/高德导航。
	if changed && s.ai != nil {
		s.ai.broadcastState()
	}
}

func normalizeVisionCommand(command string) string {
	// 命令归一化：前端可用 start-blind-navigation 或 start_blind_navigation。
	return strings.ToLower(strings.ReplaceAll(strings.TrimSpace(command), "-", "_"))
}

func isNavigationVisionCommand(command string) bool {
	switch normalizeVisionCommand(command) {
	case "start_blind_navigation", "blind_navigation", "blind", "start_blind",
		"start_crossing", "crossing", "crosswalk",
		"detect_traffic_light", "traffic_light",
		"find_object", "item_search", "search_item":
		return true
	default:
		return false
	}
}

func isStopVisionCommand(command string) bool {
	switch normalizeVisionCommand(command) {
	case "stop", "stop_navigation", "idle", "chat", "reset", "clear":
		return true
	default:
		return false
	}
}

func normalizeWorkMode(mode string) string {
	switch strings.ToLower(strings.ReplaceAll(strings.TrimSpace(mode), "-", "_")) {
	case "qa", "chat", "question", "question_answering":
		return "qa"
	case "navigation", "nav", "blind_navigation", "crossing":
		return "navigation"
	default:
		return ""
	}
}

func (s *server) handleDeviceWS(c *gin.Context) {
	// 旧版设备 WebSocket 入口。当前 ESP32 固件主要走 UDP，但保留此入口用于兼容调试。
	if s.deviceToken != "" && c.Query("token") != s.deviceToken {
		c.String(http.StatusUnauthorized, "invalid device token")
		return
	}

	conn, err := s.upgrader.Upgrade(c.Writer, c.Request, nil)
	if err != nil {
		log.Printf("device upgrade failed: %v", err)
		return
	}
	defer conn.Close()

	conn.SetReadLimit(4 << 20)
	conn.SetReadDeadline(time.Now().Add(deviceReadTimeout))
	conn.SetPongHandler(func(string) error {
		_ = conn.SetReadDeadline(time.Now().Add(deviceReadTimeout))
		return nil
	})

	if old := s.hub.setDevice(conn); old != nil && old != conn {
		_ = old.Close()
	}
	s.hub.broadcastState()

	deviceID := c.Query("id")
	log.Printf("device connected: id=%s remote=%s", deviceID, c.Request.RemoteAddr)
	if err := conn.WriteMessage(websocket.TextMessage, []byte(`{"type":"server_ready"}`)); err != nil {
		log.Printf("failed to send server_ready: %v", err)
	}

	// 设备会在每个二进制载荷之前先发一条文本元数据，所以这里先暂存上一条
	// 元数据，等对应的二进制内容到达后再一起发布。
	var pending *PacketMeta

	for {
		messageType, payload, err := conn.ReadMessage()
		if err != nil {
			log.Printf("device read ended: %v", err)
			break
		}
		_ = conn.SetReadDeadline(time.Now().Add(deviceReadTimeout))

		switch messageType {
		case websocket.TextMessage:
			var hello DeviceHello
			if err := json.Unmarshal(payload, &hello); err == nil && hello.Type == "hello" {
				if hello.DeviceID == "" {
					hello.DeviceID = deviceID
				}
				if err := conn.WriteMessage(websocket.TextMessage, []byte(`{"type":"hello_ack"}`)); err != nil {
					log.Printf("failed to send hello_ack: %v", err)
				}
				s.publishHello(&hello)
				log.Printf("hello received from %s (%s)", hello.DeviceID, hello.Firmware)
				continue
			}

			var meta PacketMeta
			if err := json.Unmarshal(payload, &meta); err != nil {
				log.Printf("invalid text payload: %v", err)
				continue
			}
			if meta.Type != "video" && meta.Type != "audio" {
				log.Printf("ignored text payload type %q", meta.Type)
				continue
			}
			pending = &meta
		case websocket.BinaryMessage:
			if pending == nil {
				log.Printf("binary payload dropped because no metadata preceded it")
				continue
			}

			pending.Bytes = len(payload)
			s.publishPacket(pending, payload)
			if pending.Type == "video" && pending.Seq%25 == 0 {
				log.Printf("video packet seq=%d bytes=%d", pending.Seq, pending.Bytes)
			}
			if pending.Type == "audio" && pending.Seq%100 == 0 {
				log.Printf("audio packet seq=%d bytes=%d", pending.Seq, pending.Bytes)
			}
			pending = nil
		}
	}

	s.hub.clearDevice(conn)
	s.hub.broadcastState()
}

func (s *server) handleViewerWS(c *gin.Context) {
	// 浏览器 viewer WebSocket：文本消息是状态/元数据，二进制消息是 JPEG 或 PCM。
	conn, err := s.upgrader.Upgrade(c.Writer, c.Request, nil)
	if err != nil {
		log.Printf("viewer upgrade failed: %v", err)
		return
	}
	defer conn.Close()

	conn.SetReadLimit(64 << 10)

	// keepalive：浏览器 viewer 是纯接收端，只有协议层 pong 会回来。
	// 通过 ping/pong 把读超时不断续期，检测到失效连接后干净关闭。
	_ = conn.SetReadDeadline(time.Now().Add(viewerPongWait))
	conn.SetPongHandler(func(string) error {
		_ = conn.SetReadDeadline(time.Now().Add(viewerPongWait))
		return nil
	})
	pingDone := make(chan struct{})
	pingTicker := time.NewTicker(viewerPingPeriod)
	go func() {
		defer pingTicker.Stop()
		for {
			select {
			case <-pingDone:
				return
			case <-pingTicker.C:
				if err := conn.WriteControl(websocket.PingMessage, nil, time.Now().Add(viewerWriteWait)); err != nil {
					_ = conn.Close()
					return
				}
			}
		}
	}()
	defer close(pingDone)

	viewer := &viewerClient{conn: conn}
	s.hub.addViewer(viewer)
	defer func() {
		s.hub.removeViewer(viewer)
		s.hub.broadcastState()
	}()

	// 新 viewer 连上来时先下发最近状态，这样页面不用等下一次定时广播也能立刻渲染。
	if stateBytes, err := json.Marshal(s.currentState()); err == nil {
		if err := viewer.send(websocket.TextMessage, stateBytes); err != nil {
			log.Printf("initial state write failed: %v", err)
			return
		}
	}

	s.hub.mu.RLock()
	deviceHello := s.hub.deviceHello
	s.hub.mu.RUnlock()
	if deviceHello != nil {
		if helloBytes, err := json.Marshal(deviceHello); err == nil {
			if err := viewer.send(websocket.TextMessage, helloBytes); err != nil {
				log.Printf("initial hello write failed: %v", err)
				return
			}
		}
	}

	if s.ai != nil {
		if aiStateBytes, err := json.Marshal(s.ai.snapshot()); err == nil {
			if err := viewer.send(websocket.TextMessage, aiStateBytes); err != nil {
				log.Printf("initial ai state write failed: %v", err)
				return
			}
		}
	}

	if latestMeta, latestFrame := s.hub.snapshotLatestVideo(); latestMeta != nil && len(latestFrame) > 0 {
		// 给后加入的 viewer 补发最近一帧，让页面即使还没等到新帧也能先看到画面。
		if metaBytes, err := json.Marshal(latestMeta); err == nil {
			if err := viewer.send(websocket.TextMessage, metaBytes); err != nil {
				log.Printf("initial video meta write failed: %v", err)
				return
			}
		}
		if err := viewer.send(websocket.BinaryMessage, latestFrame); err != nil {
			log.Printf("initial video frame write failed: %v", err)
			return
		}
	}
	if s.vision != nil {
		if visionStateBytes, err := json.Marshal(s.vision.snapshot()); err == nil {
			if err := viewer.send(websocket.TextMessage, visionStateBytes); err != nil {
				log.Printf("initial vision state write failed: %v", err)
				return
			}
		}
	}

	if latestMeta, latestFrame := s.hub.snapshotLatestVisionVideo(); latestMeta != nil && len(latestFrame) > 0 {
		// 如果视觉 worker 已经产生标注帧，新 viewer 也立即补发一帧。
		if metaBytes, err := json.Marshal(latestMeta); err == nil {
			if err := viewer.send(websocket.TextMessage, metaBytes); err != nil {
				log.Printf("initial vision video meta write failed: %v", err)
				return
			}
		}
		if err := viewer.send(websocket.BinaryMessage, latestFrame); err != nil {
			log.Printf("initial vision video frame write failed: %v", err)
			return
		}
	}

	s.hub.broadcastState()

	for {
		if _, _, err := conn.ReadMessage(); err != nil {
			return
		}
	}
}

func (s *server) corsMiddleware() gin.HandlerFunc {
	// 前端通常单独由 Vite/Nginx 提供，因此这里需要允许跨源访问 API 和 WebSocket。
	return func(c *gin.Context) {
		allowOrigin := strings.TrimSpace(s.allowOrigin)
		if allowOrigin == "" {
			allowOrigin = "*"
		}
		c.Header("Access-Control-Allow-Origin", allowOrigin)
		c.Header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
		c.Header("Access-Control-Allow-Headers", "Content-Type, Authorization")
		c.Header("Access-Control-Expose-Headers", "Content-Type")
		if c.Request.Method == http.MethodOptions {
			c.Status(http.StatusNoContent)
			c.Abort()
			return
		}
		c.Next()
	}
}
