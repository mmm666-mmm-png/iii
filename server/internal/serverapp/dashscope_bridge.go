package serverapp

import (
	"context"
	"encoding/base64"
	"encoding/binary"
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"net/http"
	"strings"
	"sync"
	"time"

	"github.com/gorilla/websocket"
)

const (
	// DashScope Realtime 需要 16k PCM 输入；ESP32 上行可能是 22050Hz，因此发送前重采样。
	dashScopeTargetSampleRate    = 16000
	dashScopeOutputSampleRate    = 24000
	dashScopeImageSendInterval   = time.Second
	dashScopeReconnectDelay      = 3 * time.Second
	dashScopeMaxImageBytes       = 500 * 1024
	dashScopeAudioQueueCapacity  = 24
	dashScopeInputUnlockTail     = 350 * time.Millisecond
	dashScopeSkillSuppressWindow = 10 * time.Second
	dashScopeFallbackVoice       = "Ethan"
)

var errDashScopeNotConnected = errors.New("DashScope bridge not connected")

type dashScopeConfig struct {
	APIKey       string
	Region       string
	Model        string
	Voice        string
	Instructions string
	EnableSearch bool
}

type aiStateMessage struct {
	// 前端 AI 状态面板使用的 WebSocket 消息。
	Type          string            `json:"type"`
	Enabled       bool              `json:"enabled"`
	Connected     bool              `json:"connected"`
	Mode          string            `json:"mode,omitempty"`
	InputPaused   bool              `json:"inputPaused"`
	VoiceMode     string            `json:"voiceMode,omitempty"`
	Model         string            `json:"model,omitempty"`
	Voice         string            `json:"voice,omitempty"`
	Region        string            `json:"region,omitempty"`
	SearchEnabled bool              `json:"searchEnabled,omitempty"`
	SessionID     string            `json:"sessionId,omitempty"`
	LastError     string            `json:"lastError,omitempty"`
	LastEventAt   string            `json:"lastEventAt,omitempty"`
	Skills        []skillDescriptor `json:"skills,omitempty"`
}

type aiTranscriptMessage struct {
	Type       string `json:"type"`
	Role       string `json:"role"`
	Text       string `json:"text"`
	Final      bool   `json:"final,omitempty"`
	ResponseID string `json:"responseId,omitempty"`
}

type aiEventMessage struct {
	Type       string `json:"type"`
	Event      string `json:"event"`
	ResponseID string `json:"responseId,omitempty"`
}

type dashScopeAudioChunk struct {
	// 设备上行 PCM 音频块，进入 audioLoop 后重采样并推给 DashScope。
	payload    []byte
	sampleRate int
	channels   int
	bits       int
}

type dashScopeBridge struct {
	// dashScopeBridge 连接阿里云百炼 Realtime WebSocket：
	// - audioLoop 发送设备麦克风；
	// - imageLoop 周期发送最新摄像头帧；
	// - readLoop 接收 ASR、文本、音频和工具相关事件。
	server *server
	cfg    dashScopeConfig

	audioCh chan dashScopeAudioChunk

	connMu sync.RWMutex
	conn   *websocket.Conn

	sendMu sync.Mutex

	stateMu sync.RWMutex
	// inputPaused 表示前端/视觉导航临时关闭 AI 问答输入，避免导航时被环境声打断。
	connected     bool
	sessionID     string
	lastError     string
	lastEventAt   time.Time
	hasSentAudio  bool
	inputPaused   bool
	activeVoice   string
	responding    bool
	responseID    string
	audioUnlockAt time.Time

	// 技能抑制相关：当本地技能命中时，需要取消模型原始回答，
	// 执行本地工具后再让模型根据工具结果生成一句自然回复。
	suppressAssistantUntil      time.Time
	suppressAssistantPending    bool
	suppressAssistantResponseID string
	restoreInstructionsPending  bool
	restoreInstructionsResponse string
	lastTranscriptResponseID    string

	frameMu        sync.RWMutex
	latestFrame    []byte
	latestFrameSeq uint64

	assistantAudioSeq uint64
}

func newDashScopeBridge(server *server, cfg dashScopeConfig) *dashScopeBridge {
	// 创建后立即启动三个后台循环。连接失败时 run 会自动重连。
	bridge := &dashScopeBridge{
		server:  server,
		cfg:     cfg,
		audioCh: make(chan dashScopeAudioChunk, dashScopeAudioQueueCapacity),
	}
	if bridge.cfg.Voice == "" {
		bridge.cfg.Voice = dashScopeFallbackVoice
	}
	bridge.activeVoice = bridge.cfg.Voice
	go bridge.audioLoop()
	go bridge.imageLoop()
	go bridge.run()
	return bridge
}

func (b *dashScopeBridge) snapshot() aiStateMessage {
	b.stateMu.RLock()
	defer b.stateMu.RUnlock()

	message := aiStateMessage{
		Type:          "ai_state",
		Enabled:       true,
		Connected:     b.connected,
		InputPaused:   b.inputPaused,
		Model:         b.cfg.Model,
		Voice:         b.activeVoice,
		Region:        b.cfg.Region,
		SearchEnabled: b.cfg.EnableSearch,
		SessionID:     b.sessionID,
		LastError:     b.lastError,
	}
	if b.inputPaused {
		message.Mode = "navigation"
	} else {
		message.Mode = "qa"
	}
	if b.server != nil {
		message.VoiceMode = b.server.getVoiceMode()
	}
	if !b.lastEventAt.IsZero() {
		message.LastEventAt = b.lastEventAt.Format(time.RFC3339)
	}
	if b.server != nil && b.server.skills != nil {
		message.Skills = b.server.skills.descriptors()
	}
	return message
}

func (b *dashScopeBridge) ingestPacket(meta *PacketMeta, payload []byte) {
	// 导航模式暂停 AI 时，音视频仍给前端/视觉 worker，但不送入 DashScope。
	if b.isInputPaused() {
		return
	}
	switch meta.Type {
	case "audio":
		b.ingestAudio(meta, payload)
	case "video":
		b.ingestVideo(meta, payload)
	}
}

func (b *dashScopeBridge) ingestAudio(meta *PacketMeta, payload []byte) {
	// AI 正在回复或刚播完时锁住输入，避免设备扬声器声音被麦克风回采后再次触发。
	if meta.Channels != 1 || meta.BitsPerSample != 16 || len(payload) == 0 {
		return
	}
	if b.isInputLocked() {
		return
	}

	chunk := dashScopeAudioChunk{
		payload:    append([]byte(nil), payload...),
		sampleRate: meta.SampleRate,
		channels:   meta.Channels,
		bits:       meta.BitsPerSample,
	}

	select {
	case b.audioCh <- chunk:
	default:
		// 音频队列满时丢最旧块，宁可短暂缺音，也不要把 ASR 延迟越堆越大。
		select {
		case <-b.audioCh:
		default:
		}
		select {
		case b.audioCh <- chunk:
		default:
		}
	}
}

func (b *dashScopeBridge) ingestVideo(meta *PacketMeta, payload []byte) {
	// 只保存最新一帧，imageLoop 按固定间隔发送，降低多模态输入带宽。
	if len(payload) == 0 {
		return
	}
	b.frameMu.Lock()
	defer b.frameMu.Unlock()

	b.latestFrame = append(b.latestFrame[:0], payload...)
	b.latestFrameSeq = meta.Seq
}

func (b *dashScopeBridge) run() {
	// 永久重连循环。每次连接成功后 readLoop 会阻塞到连接断开。
	b.broadcastState()

	for {
		conn, err := b.connect()
		if err != nil {
			b.markDisconnected(nil, err)
			time.Sleep(dashScopeReconnectDelay)
			continue
		}

		err = b.readLoop(conn)
		b.markDisconnected(conn, err)
		time.Sleep(dashScopeReconnectDelay)
	}
}

func (b *dashScopeBridge) connect() (*websocket.Conn, error) {
	// 连接后第一件事是 session.update，声明输入/输出格式、VAD 和系统提示词。
	headers := http.Header{}
	headers.Set("Authorization", "Bearer "+b.cfg.APIKey)

	conn, resp, err := websocket.DefaultDialer.Dial(b.websocketURL(), headers)
	if resp != nil && resp.Body != nil {
		_ = resp.Body.Close()
	}
	if err != nil {
		return nil, err
	}

	conn.SetReadLimit(8 << 20)
	if err := b.sendEventOn(conn, b.sessionUpdateEvent()); err != nil {
		_ = conn.Close()
		return nil, err
	}

	b.markConnected(conn)
	log.Printf("DashScope bridge connected: model=%s region=%s", b.cfg.Model, b.cfg.Region)
	return conn, nil
}

func (b *dashScopeBridge) readLoop(conn *websocket.Conn) error {
	for {
		messageType, payload, err := conn.ReadMessage()
		if err != nil {
			return err
		}
		if messageType != websocket.TextMessage {
			continue
		}
		b.handleEvent(payload)
	}
}

func (b *dashScopeBridge) audioLoop() {
	// 音频发送循环：把设备上行 PCM 转成 DashScope 需要的 16k mono PCM。
	for chunk := range b.audioCh {
		if b.isInputPaused() {
			continue
		}
		if chunk.channels != 1 || chunk.bits != 16 {
			continue
		}
		if b.isInputLocked() {
			continue
		}

		pcm16, err := resamplePCM16MonoTo16k(chunk.payload, chunk.sampleRate)
		if err != nil || len(pcm16) == 0 {
			continue
		}
		if b.isInputLocked() {
			continue
		}

		err = b.sendEvent(map[string]any{
			"type":  "input_audio_buffer.append",
			"audio": base64.StdEncoding.EncodeToString(pcm16),
		})
		if err == nil {
			b.markAudioSent()
		}
	}
}

func (b *dashScopeBridge) imageLoop() {
	// 图像发送循环：只有收到过音频后才发送图片，避免无人说话时持续消耗模型输入。
	ticker := time.NewTicker(dashScopeImageSendInterval)
	defer ticker.Stop()

	var lastSentSeq uint64
	for range ticker.C {
		if b.isInputPaused() || !b.audioWasSent() || b.isInputLocked() {
			continue
		}

		frame, seq := b.latestFrameSnapshot()
		if len(frame) == 0 || seq == 0 || seq == lastSentSeq || len(frame) > dashScopeMaxImageBytes {
			continue
		}

		err := b.sendEvent(map[string]any{
			"type":  "input_image_buffer.append",
			"image": base64.StdEncoding.EncodeToString(frame),
		})
		if err == nil {
			lastSentSeq = seq
		}
	}
}

func (b *dashScopeBridge) handleEvent(payload []byte) {
	// DashScope Realtime 的所有回包都在这里按 type 分发。
	var event map[string]any
	if err := json.Unmarshal(payload, &event); err != nil {
		log.Printf("DashScope event decode failed: %v", err)
		return
	}

	b.markEventSeen()
	eventType := asString(event["type"])

	if eventType == "session.created" {
		if session := asMap(event["session"]); session != nil {
			b.setSessionID(asString(session["id"]))
		}
		return
	}
	if b.isInputPaused() && eventType != "error" {
		// 导航暂停时忽略普通模型事件，只保留错误用于诊断。
		return
	}

	switch eventType {
	case "input_audio_buffer.speech_started":
		b.handleSpeechStarted(event)
	case "conversation.item.input_audio_transcription.completed":
		b.handleInputTranscriptCompleted(event)
	case "response.created":
		b.handleResponseCreated(event)
	case "response.audio_transcript.delta", "response.text.delta":
		delta := asString(event["delta"])
		if delta != "" {
			if b.shouldSuppressAssistantOutput(extractResponseID(event)) {
				return
			}
			b.server.broadcastJSON(aiTranscriptMessage{
				Type: "ai_transcript",
				Role: "assistant",
				Text: delta,
			})
		}
	case "response.audio_transcript.done", "response.text.done":
		transcript := strings.TrimSpace(asString(event["transcript"]))
		if transcript != "" {
			if b.shouldSuppressAssistantOutput(extractResponseID(event)) {
				return
			}
			b.server.broadcastJSON(aiTranscriptMessage{
				Type:       "ai_transcript",
				Role:       "assistant",
				Text:       transcript,
				Final:      true,
				ResponseID: extractResponseID(event),
			})
		}
	case "response.audio.done":
		b.handleResponseAudioDone(event)
	case "response.done":
		b.handleResponseDone(event)
	case "response.audio.delta":
		// 模型音频增量是 base64 PCM16 24k，转成 ai_audio 包广播给浏览器和设备回放。
		delta := asString(event["delta"])
		if delta == "" {
			return
		}
		responseID := extractResponseID(event)
		suppress := b.shouldSuppressAssistantOutput(responseID)
		log.Printf("audio.delta: responseID=%s suppress=%v inputLocked=%v", responseID, suppress, b.isInputLocked())
		if suppress {
			return
		}
		audioBytes, err := base64.StdEncoding.DecodeString(delta)
		if err != nil {
			log.Printf("DashScope audio delta decode failed: %v", err)
			return
		}
		b.extendInputLock(playbackDuration(len(audioBytes), dashScopeOutputSampleRate, 1, 16))
		b.assistantAudioSeq++
		b.server.publishPacket(&PacketMeta{
			Type:          "ai_audio",
			Seq:           b.assistantAudioSeq,
			TimestampMs:   uint64(time.Now().UnixMilli()),
			Bytes:         len(audioBytes),
			ResponseID:    responseID,
			SampleRate:    dashScopeOutputSampleRate,
			Channels:      1,
			BitsPerSample: 16,
		}, audioBytes)
	case "input_audio_buffer.speech_stopped":
		b.broadcastAIEvent("input_audio_buffer.speech_stopped", event)
	case "conversation.item.created":
		// log for diagnostics but no action needed
		log.Printf("DashScope conversation.item.created: item_type=%s", asString(asMap(event["item"])["type"]))
	case "error":
		message := asString(event["message"])
		if message == "" {
			if errMap := asMap(event["error"]); errMap != nil {
				message = asString(errMap["message"])
			}
		}
		if message == "" {
			message = "unknown DashScope error"
		}
		if b.isInputPaused() && strings.Contains(strings.ToLower(message), "none active response") {
			log.Printf("DashScope benign pause event ignored: %s", message)
			return
		}
		log.Printf("DashScope error event: %s", message)
		b.maybeFallbackVoice(message)
		b.setLastError(message)
	default:
		eventType := asString(event["type"])
		if strings.Contains(eventType, "transcription") {
			log.Printf("DashScope unhandled transcription event: type=%s payload=%s", eventType, string(payload))
			b.handleInputTranscriptCompleted(event)
		} else if eventType != "session.updated" && eventType != "rate_limits.updated" {
			log.Printf("DashScope unhandled event: type=%s", eventType)
		}
	}
}

func (b *dashScopeBridge) sessionUpdateEvent() map[string]any {
	return b.sessionUpdateEventWithInstructions(b.cfg.Instructions)
}

func (b *dashScopeBridge) sessionUpdateEventWithInstructions(instructions string) map[string]any {
	// 这里配置 server_vad，让 DashScope 侧判断用户何时开始/结束讲话。
	session := map[string]any{
		"modalities":          []string{"text", "audio"},
		"voice":               b.currentVoice(),
		"instructions":        instructions,
		"input_audio_format":  "pcm",
		"output_audio_format": "pcm",
		"input_audio_transcription": map[string]any{
			"model": "gummy-realtime-v1",
		},
		"turn_detection": map[string]any{
			"type":                "server_vad",
			"threshold":           0.5,
			"prefix_padding_ms":   500,
			"silence_duration_ms": 800,
		},
	}
	if b.cfg.EnableSearch {
		session["enable_search"] = true
		session["search_options"] = map[string]any{
			"enable_source": true,
		}
	}

	return map[string]any{
		"type":    "session.update",
		"session": session,
	}
}

func (b *dashScopeBridge) handleSpeechStarted(event map[string]any) {
	if b.isInputLocked() {
		return
	}
	b.broadcastAIEvent("input_audio_buffer.speech_started", event)
}

func (b *dashScopeBridge) handleInputTranscriptCompleted(event map[string]any) {
	// 用户 ASR final 文本到达后，先广播到前端，再尝试匹配本地技能。
	transcript := strings.TrimSpace(asString(event["transcript"]))
	log.Printf("handleInputTranscriptCompleted: transcript=%q", transcript)
	if transcript == "" {
		return
	}

	responseID := extractResponseID(event)

	b.stateMu.Lock()
	if responseID != "" && responseID == b.lastTranscriptResponseID {
		b.stateMu.Unlock()
		log.Printf("handleInputTranscriptCompleted: skipping duplicate responseID=%s", responseID)
		return
	}
	if responseID != "" {
		b.lastTranscriptResponseID = responseID
	}
	b.stateMu.Unlock()
	b.server.broadcastJSON(aiTranscriptMessage{
		Type:       "ai_transcript",
		Role:       "user",
		Text:       transcript,
		Final:      true,
		ResponseID: responseID,
	})

	intent, ok := b.server.skillIntentFromTranscript(transcript)
	if !ok {
		return
	}

	// 命中本地技能时，先抑制/取消模型原本的自由回答，避免用户听到两套回答。
	if !b.suppressActiveResponse() {
		b.beginSkillResponseSuppression()
	}
	if err := b.cancelResponse(); err != nil {
		log.Printf("DashScope response cancel failed: %v", err)
	}
	go b.runSkillAndRequestResponse(transcript, responseID, intent)
}

func (b *dashScopeBridge) runSkillAndRequestResponse(transcript, responseID string, intent skillIntent) {
	// 后台执行技能，再构造一个“工具结果摘要” prompt 让模型自然复述给用户。
	args, err := json.Marshal(intent.Args)
	if err != nil {
		log.Printf("skill intent marshal failed: %v", err)
		return
	}

	callID := fmt.Sprintf("transcript_skill_%d", time.Now().UnixNano())
	message := b.server.executeSkillCall(context.Background(), "transcript", intent.Name, callID, responseID, args, false)
	if message.Name == "" {
		return
	}

	prompt := buildSkillResponsePrompt(transcript, message)
	if b.isResponding() {
		if err := b.cancelResponse(); err != nil {
			log.Printf("DashScope response cancel failed before skill reply: %v", err)
		}
	}
	log.Printf("skill reply: sending requestResponse, responding=%v", b.isResponding())
	_ = b.sendEvent(b.sessionUpdateEvent())
	b.clearAllSkillSuppression()
	if err := b.requestResponse(prompt); err != nil {
		log.Printf("DashScope skill response request failed: %v", err)
		if message.Summary != "" {
			b.server.broadcastJSON(aiTranscriptMessage{
				Type:       "ai_transcript",
				Role:       "assistant",
				Text:       message.Summary,
				Final:      true,
				ResponseID: responseID,
			})
		}
	}
}

func (b *dashScopeBridge) handleResponseCreated(event map[string]any) {
	// 新回复开始时清掉旧的下行音频，避免前一轮残留继续播放。
	responseID := extractResponseID(event)
	b.markResponseStarted(responseID)
	b.bindInstructionRestoreResponse(responseID)
	b.discardPendingAudio()
	b.server.clearDevicePlaybackQueue()
	b.broadcastAIEvent("response.created", event)
}

func (b *dashScopeBridge) handleResponseAudioDone(event map[string]any) {
	responseID := extractResponseID(event)
	b.finishInputLock()
	b.markResponseStopped(responseID)
	b.broadcastAIEvent("response.audio.done", event)
}

func (b *dashScopeBridge) handleResponseDone(event map[string]any) {
	responseID := extractResponseID(event)
	b.finishInputLock()
	b.markResponseStopped(responseID)
	b.finishSkillResponseSuppression(responseID)
	b.broadcastAIEvent("response.done", event)
	if b.consumeInstructionRestore(responseID) {
		if err := b.restoreSessionInstructions(); err != nil {
			log.Printf("DashScope instruction restore failed: %v", err)
		}
	}
	b.tryExtractUserTranscript(event, responseID)
}

// tryExtractUserTranscript 从 response.done 的 output 中兜底提取用户转写。
// 有些 DashScope 模型不会单独发 transcription.completed，而是把用户文本嵌在 response 输出里。
func (b *dashScopeBridge) tryExtractUserTranscript(event map[string]any, responseID string) {
	response := asMap(event["response"])
	if response == nil {
		return
	}
	outputItems, _ := response["output"].([]any)
	for _, item := range outputItems {
		itemMap, _ := item.(map[string]any)
		if itemMap == nil {
			continue
		}
		if asString(itemMap["role"]) != "user" {
			continue
		}
		contentList, _ := itemMap["content"].([]any)
		for _, c := range contentList {
			cMap, _ := c.(map[string]any)
			if cMap == nil {
				continue
			}
			contentType := asString(cMap["type"])
			if contentType == "input_text" {
				continue
			}
			transcript := strings.TrimSpace(asString(cMap["transcript"]))
			if transcript == "" {
				continue
			}
			log.Printf("extracted user transcript from response.done: %q", transcript)
			syntheticEvent := map[string]any{
				"transcript":  transcript,
				"response_id": responseID,
			}
			b.handleInputTranscriptCompleted(syntheticEvent)
			return
		}
	}
}

func (b *dashScopeBridge) broadcastAIEvent(eventType string, event map[string]any) {
	b.server.broadcastJSON(aiEventMessage{
		Type:       "ai_event",
		Event:      eventType,
		ResponseID: extractResponseID(event),
	})
}

func (b *dashScopeBridge) websocketURL() string {
	baseDomain := "dashscope.aliyuncs.com"
	if strings.EqualFold(b.cfg.Region, "intl") {
		baseDomain = "dashscope-intl.aliyuncs.com"
	}
	return fmt.Sprintf("wss://%s/api-ws/v1/realtime?model=%s", baseDomain, b.cfg.Model)
}

func (b *dashScopeBridge) sendEvent(event map[string]any) error {
	conn := b.currentConn()
	if conn == nil {
		return errDashScopeNotConnected
	}
	if err := b.sendEventOn(conn, event); err != nil {
		b.setLastError(err.Error())
		_ = conn.Close()
		return err
	}
	return nil
}

func (b *dashScopeBridge) sendEventOn(conn *websocket.Conn, event map[string]any) error {
	// WebSocket 写必须串行化，避免 audioLoop/imageLoop/readLoop 同时写导致帧交错。
	event["event_id"] = fmt.Sprintf("event_%d", time.Now().UnixNano())

	b.sendMu.Lock()
	defer b.sendMu.Unlock()

	_ = conn.SetWriteDeadline(time.Now().Add(5 * time.Second))
	return conn.WriteJSON(event)
}

func (b *dashScopeBridge) requestResponse(userPrompt string) error {
	// 可选先创建一条 user 文本消息，再显式 response.create 要求模型回复。
	if b.isInputPaused() {
		return nil
	}
	userPrompt = strings.TrimSpace(userPrompt)
	if userPrompt != "" {
		if err := b.sendEvent(map[string]any{
			"type": "conversation.item.create",
			"item": map[string]any{
				"type": "message",
				"role": "user",
				"content": []map[string]any{
					{
						"type": "input_text",
						"text": userPrompt,
					},
				},
			},
		}); err != nil {
			return err
		}
	}

	event := map[string]any{
		"type": "response.create",
	}
	event["response"] = map[string]any{
		"modalities": []string{"text", "audio"},
	}
	return b.sendEvent(event)
}

func (b *dashScopeBridge) cancelResponse() error {
	return b.sendEvent(map[string]any{
		"type": "response.cancel",
	})
}

func (b *dashScopeBridge) restoreSessionInstructions() error {
	return b.sendEvent(b.sessionUpdateEventWithInstructions(b.cfg.Instructions))
}

func (b *dashScopeBridge) currentConn() *websocket.Conn {
	b.connMu.RLock()
	defer b.connMu.RUnlock()
	return b.conn
}

func (b *dashScopeBridge) latestFrameSnapshot() ([]byte, uint64) {
	b.frameMu.RLock()
	defer b.frameMu.RUnlock()
	if len(b.latestFrame) == 0 {
		return nil, 0
	}
	return append([]byte(nil), b.latestFrame...), b.latestFrameSeq
}

func (b *dashScopeBridge) audioWasSent() bool {
	b.stateMu.RLock()
	defer b.stateMu.RUnlock()
	return b.hasSentAudio
}

func (b *dashScopeBridge) currentVoice() string {
	b.stateMu.RLock()
	defer b.stateMu.RUnlock()

	if b.activeVoice != "" {
		return b.activeVoice
	}
	if b.cfg.Voice != "" {
		return b.cfg.Voice
	}
	return dashScopeFallbackVoice
}

func (b *dashScopeBridge) markAudioSent() {
	b.stateMu.Lock()
	defer b.stateMu.Unlock()
	b.hasSentAudio = true
}

func (b *dashScopeBridge) isResponding() bool {
	b.stateMu.RLock()
	defer b.stateMu.RUnlock()
	return b.responding
}

func (b *dashScopeBridge) markResponseStarted(responseID string) {
	b.stateMu.Lock()
	defer b.stateMu.Unlock()
	b.responding = true
	b.responseID = responseID
	if b.skillResponseSuppressionActiveLocked(time.Now()) && b.suppressAssistantPending && responseID != "" {
		b.suppressAssistantResponseID = responseID
		b.suppressAssistantPending = false
	}
}

func (b *dashScopeBridge) markResponseStopped(responseID string) {
	b.stateMu.Lock()
	defer b.stateMu.Unlock()
	b.responding = false
	if responseID != "" {
		b.responseID = responseID
	}
}

func (b *dashScopeBridge) isInputLocked() bool {
	b.stateMu.RLock()
	defer b.stateMu.RUnlock()
	return b.inputLockedAt(time.Now())
}

func (b *dashScopeBridge) isInputPaused() bool {
	b.stateMu.RLock()
	defer b.stateMu.RUnlock()
	return b.inputPaused
}

func (b *dashScopeBridge) setInputPaused(paused bool) {
	// 进入导航模式时清理回答状态、音频队列和技能抑制状态；退出后恢复正常问答。
	b.stateMu.Lock()
	changed := b.inputPaused != paused
	wasResponding := b.responding
	b.inputPaused = paused
	if paused {
		b.hasSentAudio = false
		b.responding = false
		b.responseID = ""
		b.audioUnlockAt = time.Time{}
		b.suppressAssistantUntil = time.Time{}
		b.suppressAssistantPending = false
		b.suppressAssistantResponseID = ""
	}
	b.stateMu.Unlock()

	if paused {
		b.discardPendingAudio()
		if wasResponding {
			if err := b.cancelResponse(); err != nil && !errors.Is(err, errDashScopeNotConnected) {
				log.Printf("DashScope response cancel while pausing failed: %v", err)
			}
		}
		if b.server != nil {
			b.server.clearDevicePlaybackQueue()
		}
	}
	if changed {
		b.broadcastState()
	}
}

// InterruptAudio 中断当前 AI 语音输出：取消 DashScope 回答、丢弃待发音频并清空
// 设备播放队列。由 Python 语音会话管理器的 stop_tts 经 POST /api/voice/interrupt 触发。
func (b *dashScopeBridge) InterruptAudio() {
	if b == nil {
		return
	}
	if b.isResponding() {
		if err := b.cancelResponse(); err != nil && !errors.Is(err, errDashScopeNotConnected) {
			log.Printf("DashScope response cancel on interrupt failed: %v", err)
		}
	}
	b.discardPendingAudio()
	if b.server != nil {
		b.server.clearDevicePlaybackQueue()
	}
}

func (b *dashScopeBridge) inputLockedAt(now time.Time) bool {
	// 模型正在回答，或刚收到下行音频尚未播放完时，都不接收新的麦克风输入。
	if b.responding {
		return true
	}
	return !b.audioUnlockAt.IsZero() && now.Before(b.audioUnlockAt)
}

func (b *dashScopeBridge) extendInputLock(duration time.Duration) {
	// 根据模型音频片段时长延长输入锁，防止“AI 自己说话”被再次识别。
	if duration <= 0 {
		return
	}

	b.stateMu.Lock()
	defer b.stateMu.Unlock()

	base := time.Now()
	if b.audioUnlockAt.After(base) {
		base = b.audioUnlockAt
	}
	b.audioUnlockAt = base.Add(duration)
}

func (b *dashScopeBridge) finishInputLock() {
	b.stateMu.Lock()
	defer b.stateMu.Unlock()

	minUnlockAt := time.Now().Add(dashScopeInputUnlockTail)
	if b.audioUnlockAt.Before(minUnlockAt) {
		b.audioUnlockAt = minUnlockAt
	}
}

func (b *dashScopeBridge) beginSkillResponseSuppression() {
	// 在技能调用窗口内，抑制模型原本的回答，等待工具结果驱动的新回答。
	b.stateMu.Lock()
	defer b.stateMu.Unlock()

	b.suppressAssistantUntil = time.Now().Add(dashScopeSkillSuppressWindow)
	b.suppressAssistantPending = true
	b.suppressAssistantResponseID = ""
	if b.responding && b.responseID != "" {
		b.suppressAssistantResponseID = b.responseID
		b.suppressAssistantPending = false
	}
}

func (b *dashScopeBridge) suppressActiveResponse() bool {
	b.stateMu.Lock()
	defer b.stateMu.Unlock()

	if !b.responding || b.responseID == "" {
		return false
	}
	b.suppressAssistantUntil = time.Now().Add(dashScopeSkillSuppressWindow)
	b.suppressAssistantPending = false
	b.suppressAssistantResponseID = b.responseID
	return true
}

func (b *dashScopeBridge) shouldSuppressAssistantOutput(responseID string) bool {
	// responseID 为空时保守抑制，直到绑定到确定的响应 ID。
	b.stateMu.Lock()
	defer b.stateMu.Unlock()

	if !b.skillResponseSuppressionActiveLocked(time.Now()) {
		return false
	}

	if b.suppressAssistantPending {
		if responseID != "" {
			b.suppressAssistantResponseID = responseID
			b.suppressAssistantPending = false
		}
		return true
	}

	if b.suppressAssistantResponseID == "" || responseID == "" {
		return true
	}
	return b.suppressAssistantResponseID == responseID
}

func (b *dashScopeBridge) markInstructionRestorePending() {
	// 如果为了工具调用临时改过 instructions，标记下一轮回复结束后恢复。
	b.stateMu.Lock()
	defer b.stateMu.Unlock()
	b.restoreInstructionsPending = true
	b.restoreInstructionsResponse = ""
}

func (b *dashScopeBridge) bindInstructionRestoreResponse(responseID string) {
	if responseID == "" {
		return
	}
	b.stateMu.Lock()
	defer b.stateMu.Unlock()
	if b.restoreInstructionsPending && b.restoreInstructionsResponse == "" {
		b.restoreInstructionsResponse = responseID
	}
}

func (b *dashScopeBridge) consumeInstructionRestore(responseID string) bool {
	b.stateMu.Lock()
	defer b.stateMu.Unlock()

	if !b.restoreInstructionsPending {
		return false
	}
	if b.restoreInstructionsResponse != "" && responseID != "" && responseID != b.restoreInstructionsResponse {
		return false
	}
	b.restoreInstructionsPending = false
	b.restoreInstructionsResponse = ""
	return true
}

func (b *dashScopeBridge) clearAllSkillSuppression() {
	b.stateMu.Lock()
	defer b.stateMu.Unlock()
	b.suppressAssistantUntil = time.Time{}
	b.suppressAssistantPending = false
	b.suppressAssistantResponseID = ""
}

func (b *dashScopeBridge) clearUnboundSkillSuppression() {
	b.stateMu.Lock()
	defer b.stateMu.Unlock()

	if b.suppressAssistantPending && b.suppressAssistantResponseID == "" {
		b.suppressAssistantUntil = time.Time{}
		b.suppressAssistantPending = false
	}
}

func (b *dashScopeBridge) finishSkillResponseSuppression(responseID string) {
	b.stateMu.Lock()
	defer b.stateMu.Unlock()
	b.clearSkillResponseSuppressionLocked(responseID)
}

func (b *dashScopeBridge) skillResponseSuppressionActiveLocked(now time.Time) bool {
	if b.suppressAssistantUntil.IsZero() {
		return false
	}
	if now.After(b.suppressAssistantUntil) {
		b.clearSkillResponseSuppressionLocked("")
		return false
	}
	return true
}

func (b *dashScopeBridge) clearSkillResponseSuppressionLocked(responseID string) {
	if b.suppressAssistantUntil.IsZero() {
		return
	}
	if responseID != "" && b.suppressAssistantResponseID != "" && responseID != b.suppressAssistantResponseID {
		return
	}
	b.suppressAssistantUntil = time.Time{}
	b.suppressAssistantPending = false
	b.suppressAssistantResponseID = ""
}

func (b *dashScopeBridge) discardPendingAudio() {
	// 清空尚未发送到 DashScope 的麦克风块。
	for {
		select {
		case <-b.audioCh:
		default:
			return
		}
	}
}

func (b *dashScopeBridge) markConnected(conn *websocket.Conn) {
	// 连接成功后重置会话级状态；业务配置如 inputPaused 保留。
	b.connMu.Lock()
	b.conn = conn
	b.connMu.Unlock()

	b.stateMu.Lock()
	b.connected = true
	b.sessionID = ""
	b.lastError = ""
	b.lastEventAt = time.Now()
	b.hasSentAudio = false
	if b.activeVoice == "" {
		b.activeVoice = b.cfg.Voice
	}
	b.responding = false
	b.responseID = ""
	b.audioUnlockAt = time.Time{}
	b.suppressAssistantUntil = time.Time{}
	b.suppressAssistantPending = false
	b.suppressAssistantResponseID = ""
	b.restoreInstructionsPending = false
	b.restoreInstructionsResponse = ""
	b.stateMu.Unlock()

	b.broadcastState()
}

func (b *dashScopeBridge) markDisconnected(conn *websocket.Conn, err error) {
	if conn != nil {
		_ = conn.Close()
	}

	b.connMu.Lock()
	if conn == nil || b.conn == conn {
		b.conn = nil
	}
	b.connMu.Unlock()

	b.stateMu.Lock()
	b.connected = false
	b.sessionID = ""
	b.hasSentAudio = false
	b.responding = false
	b.responseID = ""
	b.audioUnlockAt = time.Time{}
	b.suppressAssistantUntil = time.Time{}
	b.suppressAssistantPending = false
	b.suppressAssistantResponseID = ""
	b.restoreInstructionsPending = false
	b.restoreInstructionsResponse = ""
	if err != nil && !errors.Is(err, errDashScopeNotConnected) {
		b.lastError = err.Error()
	}
	b.stateMu.Unlock()

	b.broadcastState()
}

func (b *dashScopeBridge) markEventSeen() {
	b.stateMu.Lock()
	b.lastEventAt = time.Now()
	b.stateMu.Unlock()
}

func (b *dashScopeBridge) setSessionID(sessionID string) {
	if sessionID == "" {
		return
	}

	b.stateMu.Lock()
	b.sessionID = sessionID
	b.stateMu.Unlock()

	b.broadcastState()
}

func (b *dashScopeBridge) setLastError(message string) {
	if message == "" {
		return
	}

	b.stateMu.Lock()
	b.lastError = message
	b.stateMu.Unlock()

	b.broadcastState()
}

func (b *dashScopeBridge) maybeFallbackVoice(message string) {
	// 某些区域/模型不支持配置的 voice，收到错误后降级到 Ethan。
	if !strings.Contains(message, "Voice '") || !strings.Contains(message, "is not supported") {
		return
	}

	b.stateMu.Lock()
	defer b.stateMu.Unlock()

	if b.activeVoice == dashScopeFallbackVoice {
		return
	}

	log.Printf("DashScope voice %q is unsupported, falling back to %q", b.activeVoice, dashScopeFallbackVoice)
	b.activeVoice = dashScopeFallbackVoice
}

func (b *dashScopeBridge) broadcastState() {
	b.server.broadcastJSON(b.snapshot())
}

func asMap(value any) map[string]any {
	if m, ok := value.(map[string]any); ok {
		return m
	}
	return nil
}

func extractResponseID(event map[string]any) string {
	if responseID := asString(event["response_id"]); responseID != "" {
		return responseID
	}
	if response := asMap(event["response"]); response != nil {
		if responseID := asString(response["id"]); responseID != "" {
			return responseID
		}
	}
	return ""
}

func asString(value any) string {
	switch v := value.(type) {
	case string:
		return v
	case fmt.Stringer:
		return v.String()
	default:
		return ""
	}
}

func resamplePCM16MonoTo16k(payload []byte, sampleRate int) ([]byte, error) {
	// 线性插值重采样，足够语音 ASR 使用；避免引入额外音频库依赖。
	if sampleRate <= 0 {
		return nil, fmt.Errorf("invalid sample rate %d", sampleRate)
	}
	if len(payload)%2 != 0 {
		return nil, fmt.Errorf("invalid pcm payload length %d", len(payload))
	}
	if len(payload) == 0 {
		return nil, nil
	}
	if sampleRate == dashScopeTargetSampleRate {
		return append([]byte(nil), payload...), nil
	}

	inputSamples := len(payload) / 2
	outputSamples := int(float64(inputSamples)*float64(dashScopeTargetSampleRate)/float64(sampleRate) + 0.5)
	if outputSamples < 1 {
		outputSamples = 1
	}

	input := make([]int16, inputSamples)
	for index := 0; index < inputSamples; index++ {
		offset := index * 2
		input[index] = int16(binary.LittleEndian.Uint16(payload[offset : offset+2]))
	}

	output := make([]byte, outputSamples*2)
	for index := 0; index < outputSamples; index++ {
		sourcePos := float64(index) * float64(sampleRate) / float64(dashScopeTargetSampleRate)
		left := int(sourcePos)
		if left >= inputSamples {
			left = inputSamples - 1
		}
		right := left + 1
		if right >= inputSamples {
			right = inputSamples - 1
		}

		frac := sourcePos - float64(left)
		sample := float64(input[left])*(1-frac) + float64(input[right])*frac
		binary.LittleEndian.PutUint16(output[index*2:], uint16(int16(sample)))
	}

	return output, nil
}

func playbackDuration(payloadBytes, sampleRate, channels, bitsPerSample int) time.Duration {
	// 根据 PCM 字节数估算播放时长，用于设置输入锁时间。
	if payloadBytes <= 0 || sampleRate <= 0 || channels <= 0 || bitsPerSample <= 0 {
		return 0
	}

	bytesPerFrame := channels * (bitsPerSample / 8)
	if bytesPerFrame <= 0 {
		return 0
	}

	frameCount := payloadBytes / bytesPerFrame
	if frameCount <= 0 {
		return 0
	}

	return time.Duration(frameCount) * time.Second / time.Duration(sampleRate)
}
