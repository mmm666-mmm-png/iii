package serverapp

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/url"
	"strings"
	"sync"
	"time"
)

const visionRequestTimeout = 12 * time.Second

// visionStateMessage 是前端看到的 Python 视觉 worker 状态。
type visionStateMessage struct {
	Type               string `json:"type"`
	Enabled            bool   `json:"enabled"`
	Connected          bool   `json:"connected"`
	Ready              bool   `json:"ready"`
	WorkerURL          string `json:"workerUrl,omitempty"`
	State              string `json:"state,omitempty"`
	LastGuidance       string `json:"lastGuidance,omitempty"`
	LastError          string `json:"lastError,omitempty"`
	LastProcessedAt    string `json:"lastProcessedAt,omitempty"`
	FramesProcessed    uint64 `json:"framesProcessed"`
	FramesFailed       uint64 `json:"framesFailed"`
	LatestFrameBytes   int    `json:"latestFrameBytes,omitempty"`
	AnnotatedAvailable bool   `json:"annotatedAvailable"`
}

type visionEventMessage struct {
	Type         string         `json:"type"`
	Event        string         `json:"event"`
	Command      string         `json:"command,omitempty"`
	Target       string         `json:"target,omitempty"`
	State        string         `json:"state,omitempty"`
	GuidanceText string         `json:"guidanceText,omitempty"`
	Error        string         `json:"error,omitempty"`
	Payload      map[string]any `json:"payload,omitempty"`
	CreatedAt    string         `json:"createdAt"`
}

type visionControlRequest struct {
	Command string `json:"command"`
	Target  string `json:"target,omitempty"`
}

type gpsUpdateRequest struct {
	Lat      float64 `json:"lat"`
	Lng      float64 `json:"lng"`
	Accuracy float64 `json:"accuracy,omitempty"`
}

type visionWorkerResponse struct {
	// 与 python_worker/app_main.py 的 /api/vision/* 响应字段保持一致。
	OK                   bool   `json:"ok"`
	Ready                bool   `json:"ready"`
	State                string `json:"state"`
	GuidanceText         string `json:"guidanceText"`
	Error                string `json:"error"`
	AnnotatedImageBase64 string `json:"annotatedImageBase64"`
	ImageFormat          string `json:"imageFormat"`
}

type visionWorker struct {
	// visionWorker 是 Go 服务端到 Python worker 的桥：
	// 原始视频帧进 /api/vision/process，控制命令进 /api/vision/control。
	server   *server
	baseURL  *url.URL
	client   *http.Client
	interval time.Duration

	mu                 sync.RWMutex
	connected          bool
	ready              bool
	state              string
	lastGuidance       string
	lastError          string
	lastProcessedAt    time.Time
	framesProcessed    uint64
	framesFailed       uint64
	latestFrameBytes   int
	annotatedAvailable bool

	processMu        sync.Mutex
	processing       bool
	lastSubmittedAt  time.Time
	lastGuidanceSent string
}

func newVisionWorker(server *server, rawURL string, interval time.Duration) (*visionWorker, error) {
	// interval 控制送入 Python 的最小帧间隔，避免模型推理拖垮整条直播链路。
	rawURL = strings.TrimSpace(rawURL)
	if rawURL == "" {
		return nil, nil
	}
	parsed, err := url.Parse(rawURL)
	if err != nil {
		return nil, err
	}
	if interval <= 0 {
		interval = 450 * time.Millisecond
	}
	worker := &visionWorker{
		server:   server,
		baseURL:  parsed,
		interval: interval,
		client: &http.Client{
			Timeout: visionRequestTimeout,
		},
		state: "IDLE",
	}
	go worker.refreshStatusLoop()
	return worker, nil
}

func (v *visionWorker) snapshot() visionStateMessage {
	if v == nil {
		return visionStateMessage{Type: "vision_state", Enabled: false}
	}
	v.mu.RLock()
	defer v.mu.RUnlock()
	out := visionStateMessage{
		Type:               "vision_state",
		Enabled:            true,
		Connected:          v.connected,
		Ready:              v.ready,
		WorkerURL:          v.baseURL.String(),
		State:              v.state,
		LastGuidance:       v.lastGuidance,
		LastError:          v.lastError,
		FramesProcessed:    v.framesProcessed,
		FramesFailed:       v.framesFailed,
		LatestFrameBytes:   v.latestFrameBytes,
		AnnotatedAvailable: v.annotatedAvailable,
	}
	if !v.lastProcessedAt.IsZero() {
		out.LastProcessedAt = v.lastProcessedAt.Format(time.RFC3339)
	}
	return out
}

func (v *visionWorker) control(ctx context.Context, command, target string) (visionWorkerResponse, error) {
	// 控制命令同步调用 Python worker；成功后广播 vision_event 供前端显示。
	if v == nil {
		return visionWorkerResponse{}, fmt.Errorf("vision worker is not configured")
	}
	command = strings.TrimSpace(command)
	if command == "" {
		return visionWorkerResponse{}, fmt.Errorf("command is required")
	}

	payload, _ := json.Marshal(visionControlRequest{Command: command, Target: strings.TrimSpace(target)})
	response, err := v.postJSON(ctx, "/api/vision/control", payload)
	if err != nil {
		v.markError(err)
		return visionWorkerResponse{}, err
	}
	v.applyResponse(response)
	v.broadcastEvent(visionEventMessage{
		Type:         "vision_event",
		Event:        "control",
		Command:      command,
		Target:       target,
		State:        response.State,
		GuidanceText: response.GuidanceText,
		Error:        response.Error,
		CreatedAt:    time.Now().Format(time.RFC3339),
	})
	v.server.broadcastJSON(v.snapshot())
	return response, nil
}

func (v *visionWorker) gpsUpdate(lat, lng, accuracy float64) (map[string]any, error) {
	// 手机定位经纬度转发给 Python worker 做路段匹配，并返回匹配结果。
	if v == nil {
		return nil, errors.New("vision worker is not configured")
	}
	payload, _ := json.Marshal(gpsUpdateRequest{Lat: lat, Lng: lng, Accuracy: accuracy})
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()

	request, err := http.NewRequestWithContext(ctx, http.MethodPost, v.endpoint("/api/gps/update"), bytes.NewReader(payload))
	if err != nil {
		return nil, err
	}
	request.Header.Set("Content-Type", "application/json")
	response, err := v.client.Do(request)
	if err != nil {
		log.Printf("gps update failed: %v", err)
		return nil, err
	}
	defer response.Body.Close()
	var result map[string]any
	if err := json.NewDecoder(io.LimitReader(response.Body, 1<<20)).Decode(&result); err != nil {
		return nil, err
	}
	return result, nil
}

func (v *visionWorker) ingestFrame(meta *PacketMeta, payload []byte) {
	// 非阻塞限速：如果上一帧仍在处理，或者距离上次提交太近，直接跳过当前帧。
	if v == nil || meta == nil || len(payload) == 0 {
		return
	}
	now := time.Now()

	v.processMu.Lock()
	if v.processing || now.Sub(v.lastSubmittedAt) < v.interval {
		v.processMu.Unlock()
		return
	}
	v.processing = true
	v.lastSubmittedAt = now
	v.processMu.Unlock()

	metaCopy := *meta
	frameCopy := append([]byte(nil), payload...)
	go func() {
		defer func() {
			v.processMu.Lock()
			v.processing = false
			v.processMu.Unlock()
		}()
		v.processFrame(&metaCopy, frameCopy)
	}()
}

func (v *visionWorker) processFrame(meta *PacketMeta, payload []byte) {
	// 真正的单帧 HTTP 调用在 goroutine 中执行，避免阻塞 UDP/WS 接收。
	ctx, cancel := context.WithTimeout(context.Background(), visionRequestTimeout)
	defer cancel()

	response, err := v.postImage(ctx, "/api/vision/process", payload)
	if err != nil {
		v.markError(err)
		v.server.broadcastJSON(v.snapshot())
		return
	}
	v.applyResponse(response)

	if response.AnnotatedImageBase64 != "" {
		// Python 返回 base64 JPEG 标注图，解码后作为 vision_video 广播。
		imageBytes, err := base64.StdEncoding.DecodeString(response.AnnotatedImageBase64)
		if err == nil && len(imageBytes) > 0 {
			visionMeta := &PacketMeta{
				Type:        "vision_video",
				Seq:         meta.Seq,
				TimestampMs: uint64(time.Now().UnixMilli()),
				Bytes:       len(imageBytes),
				Width:       meta.Width,
				Height:      meta.Height,
				Format:      "jpeg",
			}
			v.server.publishVisionFrame(visionMeta, imageBytes)
		}
	}

	guidance := strings.TrimSpace(response.GuidanceText)
	if guidance != "" && guidance != v.lastGuidanceSent {
		// 相同引导不重复广播，避免前端事件列表和设备语音被同一句刷满。
		v.lastGuidanceSent = guidance
		v.broadcastEvent(visionEventMessage{
			Type:         "vision_event",
			Event:        "guidance",
			State:        response.State,
			GuidanceText: guidance,
			CreatedAt:    time.Now().Format(time.RFC3339),
		})
	}
	v.server.broadcastJSON(v.snapshot())
}

func (v *visionWorker) refreshStatusLoop() {
	// 周期拉取 worker 状态，便于发现 Python 进程断开或模型未就绪。
	ticker := time.NewTicker(4 * time.Second)
	defer ticker.Stop()
	for {
		v.refreshStatus()
		<-ticker.C
	}
}

func (v *visionWorker) refreshStatus() {
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()

	request, err := http.NewRequestWithContext(ctx, http.MethodGet, v.endpoint("/api/vision/status"), nil)
	if err != nil {
		v.markError(err)
		return
	}
	response, err := v.client.Do(request)
	if err != nil {
		v.markError(err)
		return
	}
	defer response.Body.Close()
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		v.markError(fmt.Errorf("vision status returned HTTP %d", response.StatusCode))
		return
	}
	var payload visionWorkerResponse
	if err := json.NewDecoder(response.Body).Decode(&payload); err != nil {
		v.markError(err)
		return
	}
	payload.OK = true
	v.applyResponse(payload)
	v.server.broadcastJSON(v.snapshot())
}

func (v *visionWorker) postJSON(ctx context.Context, path string, payload []byte) (visionWorkerResponse, error) {
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, v.endpoint(path), bytes.NewReader(payload))
	if err != nil {
		return visionWorkerResponse{}, err
	}
	request.Header.Set("Content-Type", "application/json")
	return v.decodeWorkerResponse(request)
}

func (v *visionWorker) postImage(ctx context.Context, path string, payload []byte) (visionWorkerResponse, error) {
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, v.endpoint(path), bytes.NewReader(payload))
	if err != nil {
		return visionWorkerResponse{}, err
	}
	request.Header.Set("Content-Type", "image/jpeg")
	return v.decodeWorkerResponse(request)
}

func (v *visionWorker) decodeWorkerResponse(request *http.Request) (visionWorkerResponse, error) {
	// 对 worker 响应做统一解码和错误包装，限制读取大小防止异常响应占内存。
	response, err := v.client.Do(request)
	if err != nil {
		return visionWorkerResponse{}, err
	}
	defer response.Body.Close()

	body, err := io.ReadAll(io.LimitReader(response.Body, 12<<20))
	if err != nil {
		return visionWorkerResponse{}, err
	}
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return visionWorkerResponse{}, fmt.Errorf("vision worker HTTP %d: %s", response.StatusCode, strings.TrimSpace(string(body)))
	}

	var payload visionWorkerResponse
	if err := json.Unmarshal(body, &payload); err != nil {
		return visionWorkerResponse{}, err
	}
	if !payload.OK && payload.Error != "" {
		return payload, errors.New(payload.Error)
	}
	return payload, nil
}

func (v *visionWorker) endpoint(path string) string {
	ref := &url.URL{Path: path}
	return v.baseURL.ResolveReference(ref).String()
}

func (v *visionWorker) applyResponse(response visionWorkerResponse) {
	// 把 Python 响应合并进本地状态快照。
	v.mu.Lock()
	defer v.mu.Unlock()

	v.connected = true
	v.ready = response.Ready || response.OK
	if response.State != "" {
		v.state = response.State
	}
	if response.GuidanceText != "" {
		v.lastGuidance = response.GuidanceText
	}
	v.lastError = response.Error
	v.lastProcessedAt = time.Now()
	if response.OK {
		v.framesProcessed++
	} else {
		v.framesFailed++
	}
	if response.AnnotatedImageBase64 != "" {
		v.annotatedAvailable = true
		v.latestFrameBytes = base64.StdEncoding.DecodedLen(len(response.AnnotatedImageBase64))
	}
}

func (v *visionWorker) markError(err error) {
	if err == nil {
		return
	}
	log.Printf("vision worker error: %v", err)
	v.mu.Lock()
	v.connected = false
	v.ready = false
	v.lastError = err.Error()
	v.framesFailed++
	v.mu.Unlock()
}

func (v *visionWorker) broadcastEvent(event visionEventMessage) {
	// 视觉引导既广播给前端，也尝试匹配预录语音下发给设备扬声器。
	if event.CreatedAt == "" {
		event.CreatedAt = time.Now().Format(time.RFC3339)
	}
	if strings.TrimSpace(event.GuidanceText) != "" {
		v.server.enqueueNavigationVoiceForDevice(event.GuidanceText)
	}
	v.server.broadcastJSON(event)
}
