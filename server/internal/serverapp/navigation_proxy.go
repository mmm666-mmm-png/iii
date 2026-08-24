package serverapp

import (
	"bytes"
	"context"
	"io"
	"net/http"
	"time"

	"github.com/gin-gonic/gin"
)

const navigationRequestTimeout = 90 * time.Second

func (s *server) handleNavigationPlan(c *gin.Context) {
	s.proxyWorkerJSON(c, http.MethodPost, "/api/navigation/plan")
}

func (s *server) handleNavigationVoice(c *gin.Context) {
	s.proxyWorkerJSON(c, http.MethodPost, "/api/navigation/voice")
}

func (s *server) handleNavigationStatus(c *gin.Context) {
	s.proxyWorkerJSON(c, http.MethodGet, "/api/navigation/status")
}

func (s *server) proxyWorkerJSON(c *gin.Context, method, path string) {
	if s.vision == nil {
		c.String(http.StatusServiceUnavailable, "vision worker is not configured")
		return
	}

	var body []byte
	if c.Request.Body != nil {
		limited := io.LimitReader(c.Request.Body, 1<<20)
		readBody, err := io.ReadAll(limited)
		if err != nil {
			c.String(http.StatusBadRequest, err.Error())
			return
		}
		body = readBody
	}

	ctx, cancel := context.WithTimeout(c.Request.Context(), navigationRequestTimeout)
	defer cancel()

	req, err := http.NewRequestWithContext(ctx, method, s.vision.endpoint(path), bytes.NewReader(body))
	if err != nil {
		c.String(http.StatusInternalServerError, err.Error())
		return
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
		return
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(io.LimitReader(resp.Body, 12<<20))
	if err != nil {
		c.String(http.StatusBadGateway, err.Error())
		return
	}

	if contentType := resp.Header.Get("Content-Type"); contentType != "" {
		c.Header("Content-Type", contentType)
	} else {
		c.Header("Content-Type", "application/json")
	}
	c.Header("Cache-Control", "no-store")
	c.Status(resp.StatusCode)
	_, _ = c.Writer.Write(respBody)
}
