package serverapp

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"strings"
	"time"

	"github.com/gin-gonic/gin"
)

const navigationRequestTimeout = 90 * time.Second

// navigationWorkerResultData 是 Python worker 导航接口返回的 data 字段，
// 用于从代理响应中提取需要下发到设备扬声器的播报文本。
type navigationWorkerResultData struct {
	ResponseText   string                    `json:"response_text"`
	PlanningResult *navigationPlanningResult `json:"planning_result"`
	TurnByTurn     []navigationTurnStep      `json:"turn_by_turn"`
}

type navigationPlanningResult struct {
	BroadcastText   string `json:"broadcast_text"`
	BroadcastTextEn string `json:"broadcast_text_en"`
}

type navigationTurnStep struct {
	Instruction string `json:"instruction"`
}

func (s *server) handleNavigationPlan(c *gin.Context) {
	body := s.proxyWorkerJSON(c, http.MethodPost, "/api/navigation/plan")
	s.playNavigationVoiceFromResponse(body)
}

func (s *server) handleNavigationVoice(c *gin.Context) {
	body := s.proxyWorkerJSON(c, http.MethodPost, "/api/navigation/voice")
	s.playNavigationVoiceFromResponse(body)
}

func (s *server) handleNavigationBroadcast(c *gin.Context) {
	body := s.proxyWorkerJSON(c, http.MethodPost, "/api/navigation/broadcast")
	s.playNavigationVoiceFromResponse(body)
}

func (s *server) handleNavigationStatus(c *gin.Context) {
	s.proxyWorkerJSON(c, http.MethodGet, "/api/navigation/status")
}

// playNavigationVoiceFromResponse 把 Python worker 返回的导航播报文本下发给
// 设备扬声器。优先使用中文 broadcast_text；/broadcast 接口额外逐段播报转弯指令。
func (s *server) playNavigationVoiceFromResponse(body []byte) {
	if len(body) == 0 {
		return
	}

	var payload struct {
		Data navigationWorkerResultData `json:"data"`
	}
	if err := json.Unmarshal(body, &payload); err != nil {
		return
	}

	text := ""
	if payload.Data.PlanningResult != nil {
		text = payload.Data.PlanningResult.BroadcastText
	}
	if strings.TrimSpace(text) == "" {
		text = payload.Data.ResponseText
	}
	if strings.TrimSpace(text) == "" {
		return
	}

	// 播报列表：先播路线确认，再按顺序播报高德每一步转弯指令。
	texts := []string{text}
	for _, step := range payload.Data.TurnByTurn {
		if instruction := strings.TrimSpace(step.Instruction); instruction != "" {
			texts = append(texts, instruction)
		}
	}
	s.enqueueNavigationVoiceSequenceForDevice(texts)
}

func (s *server) proxyWorkerJSON(c *gin.Context, method, path string) []byte {
	// 代理到 Python worker，并把原始响应体返回给调用方（导航处理器需要解析
	// 其中的播报文本下发到设备）。出错时已直接写入 HTTP 响应，返回 nil。
	if s.vision == nil {
		c.String(http.StatusServiceUnavailable, "vision worker is not configured")
		return nil
	}

	var body []byte
	if c.Request.Body != nil {
		limited := io.LimitReader(c.Request.Body, 1<<20)
		readBody, err := io.ReadAll(limited)
		if err != nil {
			c.String(http.StatusBadRequest, err.Error())
			return nil
		}
		body = readBody
	}

	ctx, cancel := context.WithTimeout(c.Request.Context(), navigationRequestTimeout)
	defer cancel()

	req, err := http.NewRequestWithContext(ctx, method, s.vision.endpoint(path), bytes.NewReader(body))
	if err != nil {
		c.String(http.StatusInternalServerError, err.Error())
		return nil
	}
	req.Header.Set("Accept", "application/json")
	if contentType := c.GetHeader("Content-Type"); contentType != "" {
		req.Header.Set("Content-Type", contentType)
	}

	// 导航类请求会调用高德 geocode / 路线规划等耗时服务，
	// 不能复用 vision 帧处理用的 12 秒超时 client（visionRequestTimeout），
	// 否则 geocode 等耗时超过 12 秒就会误报 502 Bad Gateway。
	client := &http.Client{Timeout: navigationRequestTimeout}
	resp, err := client.Do(req)
	if err != nil {
		s.vision.markError(err)
		c.String(http.StatusBadGateway, err.Error())
		return nil
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(io.LimitReader(resp.Body, 12<<20))
	if err != nil {
		c.String(http.StatusBadGateway, err.Error())
		return nil
	}

	if contentType := resp.Header.Get("Content-Type"); contentType != "" {
		c.Header("Content-Type", contentType)
	} else {
		c.Header("Content-Type", "application/json")
	}
	c.Header("Cache-Control", "no-store")
	c.Status(resp.StatusCode)
	_, _ = c.Writer.Write(respBody)
	return respBody
}
