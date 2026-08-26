package serverapp

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"sort"
	"strings"
	"time"
)

const skillCallTimeout = 8 * time.Second

// skillHandler 返回三部分：结构化结果、给用户听的中文摘要、错误。
type skillHandler func(context.Context, json.RawMessage) (any, string, error)

type skillDefinition struct {
	Name        string
	DisplayName string
	Description string
	Parameters  map[string]any
	Handler     skillHandler
}

type skillDescriptor struct {
	Name        string         `json:"name"`
	DisplayName string         `json:"displayName"`
	Description string         `json:"description"`
	Parameters  map[string]any `json:"parameters"`
}

type skillIntent struct {
	Name string
	Args map[string]any
}

type skillRegistry struct {
	// 技能表既可暴露给前端，也可由 ASR 文本规则直接触发。
	skills map[string]skillDefinition
}

type aiSkillMessage struct {
	// ai_skill 消息会广播给前端技能控制台。
	Type        string `json:"type"`
	Name        string `json:"name"`
	DisplayName string `json:"displayName,omitempty"`
	Status      string `json:"status"`
	Summary     string `json:"summary,omitempty"`
	CallID      string `json:"callId,omitempty"`
	ResponseID  string `json:"responseId,omitempty"`
	Source      string `json:"source,omitempty"`
	Args        any    `json:"args,omitempty"`
	Result      any    `json:"result,omitempty"`
	Error       string `json:"error,omitempty"`
	DurationMs  int64  `json:"durationMs"`
	CreatedAt   string `json:"createdAt"`
}

func newSkillRegistry(server *server) *skillRegistry {
	// 注册内置技能：时间、设备状态、天气，以及视觉导航控制类技能。
	registry := &skillRegistry{
		skills: make(map[string]skillDefinition),
	}
	registry.register(currentTimeSkill())
	registry.register(deviceStatusSkill(server))
	registry.register(weatherSkill())
	registry.register(visionControlSkill(server, "start_blind_navigation", "盲道导航", "启动盲道导航视觉状态机。", "start_blind_navigation", false))
	registry.register(visionControlSkill(server, "start_crossing", "过马路辅助", "启动斑马线/过马路辅助模式。", "start_crossing", false))
	registry.register(visionControlSkill(server, "detect_traffic_light", "红绿灯检测", "启动红绿灯检测模式。", "detect_traffic_light", false))
	registry.register(visionControlSkill(server, "stop_navigation", "停止导航", "停止当前导航或视觉辅助模式。", "stop", false))
	registry.register(visionControlSkill(server, "find_object", "物品查找", "根据目标名称启动物品查找模式。", "find_object", true))
	registry.register(visionStatusSkill(server))
	return registry
}

func (r *skillRegistry) register(skill skillDefinition) {
	if r == nil || skill.Name == "" || skill.Handler == nil {
		return
	}
	r.skills[skill.Name] = skill
}

func (r *skillRegistry) descriptors() []skillDescriptor {
	if r == nil {
		return nil
	}

	items := make([]skillDescriptor, 0, len(r.skills))
	for _, skill := range r.sortedSkills() {
		items = append(items, skillDescriptor{
			Name:        skill.Name,
			DisplayName: skill.DisplayName,
			Description: skill.Description,
			Parameters:  skill.Parameters,
		})
	}
	return items
}

func (r *skillRegistry) toolDefinitions() []map[string]any {
	// 保留为函数调用模型接口准备；当前主要走本地意图匹配。
	if r == nil {
		return nil
	}

	definitions := make([]map[string]any, 0, len(r.skills))
	for _, skill := range r.sortedSkills() {
		definitions = append(definitions, map[string]any{
			"type":        "function",
			"name":        skill.Name,
			"description": skill.Description,
			"parameters":  skill.Parameters,
		})
	}
	return definitions
}

func (r *skillRegistry) sortedSkills() []skillDefinition {
	if r == nil {
		return nil
	}

	items := make([]skillDefinition, 0, len(r.skills))
	for _, skill := range r.skills {
		items = append(items, skill)
	}
	sort.Slice(items, func(left, right int) bool {
		return items[left].Name < items[right].Name
	})
	return items
}

func (r *skillRegistry) get(name string) (skillDefinition, bool) {
	if r == nil {
		return skillDefinition{}, false
	}
	skill, ok := r.skills[name]
	return skill, ok
}

func (r *skillRegistry) execute(ctx context.Context, name string, args json.RawMessage) (any, string, error) {
	skill, ok := r.get(name)
	if !ok {
		return nil, "", fmt.Errorf("unknown skill %q", name)
	}
	if len(strings.TrimSpace(string(args))) == 0 {
		args = json.RawMessage(`{}`)
	}
	return skill.Handler(ctx, args)
}

func (r *skillRegistry) matchIntent(text string) (skillIntent, bool) {
	// 轻量中文关键词意图识别。命中后直接执行本地技能，不依赖大模型 tool call。
	trimmed := strings.TrimSpace(text)
	if trimmed == "" {
		return skillIntent{}, false
	}

	normalized := strings.ToLower(trimmed)
	if containsAny(normalized, []string{"天气", "气温", "温度", "下雨", "降雨", "风速", "weather"}) {
		return skillIntent{
			Name: "get_current_weather",
			Args: map[string]any{
				"location": extractWeatherLocation(trimmed),
			},
		}, true
	}

	if containsAny(normalized, []string{"几点", "现在时间", "当前时间", "日期", "几号", "星期几", "time"}) {
		return skillIntent{
			Name: "get_current_time",
			Args: map[string]any{
				"timezone": inferTimezone(trimmed),
			},
		}, true
	}

	hasDeviceSubject := containsAny(normalized, []string{"设备", "摄像头", "相机", "麦克风", "直播", "device"})
	hasStatusAction := containsAny(normalized, []string{"状态", "在线", "连接", "收到", "帧", "音频", "status"})
	if hasDeviceSubject && hasStatusAction {
		return skillIntent{
			Name: "get_device_status",
			Args: map[string]any{},
		}, true
	}

	if containsAny(normalized, []string{"开始导航", "盲道导航", "帮我导航"}) {
		return skillIntent{Name: "start_blind_navigation", Args: map[string]any{}}, true
	}
	if containsAny(normalized, []string{"开始过马路", "帮我过马路", "过马路"}) {
		return skillIntent{Name: "start_crossing", Args: map[string]any{}}, true
	}
	if containsAny(normalized, []string{"红绿灯", "交通灯"}) {
		return skillIntent{Name: "detect_traffic_light", Args: map[string]any{}}, true
	}
	if containsAny(normalized, []string{"停止导航", "结束导航", "停止检测", "停止红绿灯"}) {
		return skillIntent{Name: "stop_navigation", Args: map[string]any{}}, true
	}
	if target, ok := extractFindObjectTarget(trimmed); ok {
		return skillIntent{Name: "find_object", Args: map[string]any{"target": target}}, true
	}
	if containsAny(normalized, []string{"导航状态", "视觉状态", "当前模式"}) {
		return skillIntent{Name: "get_navigation_status", Args: map[string]any{}}, true
	}

	return skillIntent{}, false
}

func currentTimeSkill() skillDefinition {
	return skillDefinition{
		Name:        "get_current_time",
		DisplayName: "当前时间",
		Description: "查询指定时区的当前日期、时间和星期。",
		Parameters: map[string]any{
			"type": "object",
			"properties": map[string]any{
				"timezone": map[string]any{
					"type":        "string",
					"description": "IANA 时区名称，例如 Asia/Shanghai、UTC、America/New_York。",
				},
			},
		},
		Handler: func(ctx context.Context, args json.RawMessage) (any, string, error) {
			var payload struct {
				Timezone string `json:"timezone"`
			}
			if err := json.Unmarshal(args, &payload); err != nil {
				return nil, "", err
			}

			timezone := strings.TrimSpace(payload.Timezone)
			if timezone == "" {
				timezone = "Asia/Shanghai"
			}
			location, err := time.LoadLocation(timezone)
			if err != nil {
				return nil, "", fmt.Errorf("invalid timezone %q", timezone)
			}

			now := time.Now().In(location)
			result := map[string]any{
				"timezone": timezone,
				"time":     now.Format(time.RFC3339),
				"date":     now.Format("2006-01-02"),
				"clock":    now.Format("15:04:05"),
				"weekday":  chineseWeekday(now.Weekday()),
				"unixMs":   now.UnixMilli(),
			}
			summary := fmt.Sprintf("%s 当前时间是 %s %s（%s）。", timezone, now.Format("2006-01-02"), now.Format("15:04:05"), chineseWeekday(now.Weekday()))
			return result, summary, nil
		},
	}
}

func deviceStatusSkill(server *server) skillDefinition {
	return skillDefinition{
		Name:        "get_device_status",
		DisplayName: "设备状态",
		Description: "查询当前设备在线状态、视频帧数、音频块数和观看端数量。",
		Parameters: map[string]any{
			"type":       "object",
			"properties": map[string]any{},
		},
		Handler: func(ctx context.Context, args json.RawMessage) (any, string, error) {
			state := server.currentState()
			if state.DeviceConnected {
				summary := fmt.Sprintf(
					"设备在线，已接收 %d 帧视频、%d 个音频块，当前观看端 %d 个。",
					state.VideoFramesSeen,
					state.AudioChunksSeen,
					state.ViewerCount,
				)
				return state, summary, nil
			}

			if state.LastDeviceSeen != "" {
				return state, fmt.Sprintf("设备当前离线，最近一次在线时间是 %s。", state.LastDeviceSeen), nil
			}
			return state, "设备当前离线，尚未收到设备握手。", nil
		},
	}
}

func weatherSkill() skillDefinition {
	return skillDefinition{
		Name:        "get_current_weather",
		DisplayName: "天气查询",
		Description: "根据城市或地区名称查询实时天气、体感温度、湿度、降水和风速。",
		Parameters: map[string]any{
			"type":     "object",
			"required": []string{"location"},
			"properties": map[string]any{
				"location": map[string]any{
					"type":        "string",
					"description": "城市或地区名称，例如 北京、上海、广州。",
				},
			},
		},
		Handler: runWeatherSkill,
	}
}

func visionControlSkill(server *server, name, displayName, description, command string, requireTarget bool) skillDefinition {
	// 把“开始导航/找物品/停止”等技能统一转成 Python worker 的 /api/vision/control。
	properties := map[string]any{}
	required := []string{}
	if requireTarget {
		required = append(required, "target")
		properties["target"] = map[string]any{
			"type":        "string",
			"description": "要查找的物品名称，例如 水杯、红牛、钥匙。",
		}
	}
	parameters := map[string]any{
		"type":       "object",
		"properties": properties,
	}
	if len(required) > 0 {
		parameters["required"] = required
	}

	return skillDefinition{
		Name:        name,
		DisplayName: displayName,
		Description: description,
		Parameters:  parameters,
		Handler: func(ctx context.Context, args json.RawMessage) (any, string, error) {
			if server == nil || server.vision == nil {
				return nil, "视觉导航 worker 尚未配置。", fmt.Errorf("vision worker is not configured")
			}

			var payload struct {
				Target string `json:"target"`
			}
			if err := json.Unmarshal(args, &payload); err != nil {
				return nil, "", err
			}
			target := strings.TrimSpace(payload.Target)
			if requireTarget && target == "" {
				return nil, "", fmt.Errorf("target is required")
			}

			response, err := server.vision.control(ctx, command, target)
			state := server.vision.snapshot()
			result := map[string]any{
				"response": response,
				"state":    state,
			}
			if err != nil {
				return result, fmt.Sprintf("%s 启动失败：%s。", displayName, err.Error()), err
			}

			summary := strings.TrimSpace(response.GuidanceText)
			if summary == "" {
				summary = fmt.Sprintf("%s 已切换，当前视觉状态为 %s。", displayName, state.State)
			}
			return result, summary, nil
		},
	}
}

func visionStatusSkill(server *server) skillDefinition {
	return skillDefinition{
		Name:        "get_navigation_status",
		DisplayName: "导航状态",
		Description: "查询视觉导航 worker 的连接状态、当前模式和最近提示。",
		Parameters: map[string]any{
			"type":       "object",
			"properties": map[string]any{},
		},
		Handler: func(ctx context.Context, args json.RawMessage) (any, string, error) {
			if server == nil || server.vision == nil {
				return nil, "视觉导航 worker 尚未配置。", fmt.Errorf("vision worker is not configured")
			}
			state := server.vision.snapshot()
			summary := fmt.Sprintf("视觉导航当前状态为 %s，worker %s。", state.State, map[bool]string{true: "已连接", false: "未连接"}[state.Connected])
			if state.LastGuidance != "" {
				summary += " 最近提示：" + state.LastGuidance + "。"
			}
			if state.LastError != "" {
				summary += " 最近错误：" + state.LastError + "。"
			}
			return state, summary, nil
		},
	}
}

func runWeatherSkill(ctx context.Context, args json.RawMessage) (any, string, error) {
	// 使用 Open-Meteo 免费接口；先地理编码，再查询当前天气。
	var payload struct {
		Location string `json:"location"`
	}
	if err := json.Unmarshal(args, &payload); err != nil {
		return nil, "", err
	}

	locationName := cleanLocationText(payload.Location)
	if locationName == "" {
		locationName = "北京"
	}

	location, err := geocodeLocation(ctx, locationName)
	if err != nil {
		return nil, "", err
	}

	forecast, err := fetchCurrentWeather(ctx, location)
	if err != nil {
		return nil, "", err
	}

	temperatureUnit := forecast.CurrentUnits.Temperature2m
	if temperatureUnit == "" {
		temperatureUnit = "°C"
	}
	windUnit := forecast.CurrentUnits.WindSpeed10m
	if windUnit == "" {
		windUnit = "km/h"
	}
	precipitationUnit := forecast.CurrentUnits.Precipitation
	if precipitationUnit == "" {
		precipitationUnit = "mm"
	}

	place := formatWeatherPlace(location)
	weatherText := weatherCodeText(forecast.Current.WeatherCode)
	summary := fmt.Sprintf(
		"%s 当前%s，气温 %.1f%s，体感 %.1f%s，湿度 %.0f%%，风速 %.1f%s。",
		place,
		weatherText,
		forecast.Current.Temperature2m,
		temperatureUnit,
		forecast.Current.ApparentTemperature,
		temperatureUnit,
		forecast.Current.RelativeHumidity2m,
		forecast.Current.WindSpeed10m,
		windUnit,
	)
	if forecast.Current.Precipitation > 0 {
		summary += fmt.Sprintf(" 近时段降水 %.1f%s。", forecast.Current.Precipitation, precipitationUnit)
	}

	result := map[string]any{
		"provider": "Open-Meteo",
		"location": map[string]any{
			"name":      location.Name,
			"admin1":    location.Admin1,
			"country":   location.Country,
			"latitude":  location.Latitude,
			"longitude": location.Longitude,
			"timezone":  location.Timezone,
		},
		"current": map[string]any{
			"time":                forecast.Current.Time,
			"temperature2m":       forecast.Current.Temperature2m,
			"apparentTemperature": forecast.Current.ApparentTemperature,
			"relativeHumidity2m":  forecast.Current.RelativeHumidity2m,
			"precipitation":       forecast.Current.Precipitation,
			"weatherCode":         forecast.Current.WeatherCode,
			"weatherText":         weatherText,
			"windSpeed10m":        forecast.Current.WindSpeed10m,
			"windDirection10m":    forecast.Current.WindDirection10m,
		},
		"units": forecast.CurrentUnits,
	}
	return result, summary, nil
}

type geocodingResponse struct {
	Results []weatherLocation `json:"results"`
}

type weatherLocation struct {
	Name      string  `json:"name"`
	Latitude  float64 `json:"latitude"`
	Longitude float64 `json:"longitude"`
	Timezone  string  `json:"timezone"`
	Country   string  `json:"country"`
	Admin1    string  `json:"admin1"`
}

type currentWeatherResponse struct {
	CurrentUnits weatherCurrentUnits `json:"current_units"`
	Current      weatherCurrent      `json:"current"`
}

type weatherCurrentUnits struct {
	Time                string `json:"time,omitempty"`
	Temperature2m       string `json:"temperature_2m,omitempty"`
	ApparentTemperature string `json:"apparent_temperature,omitempty"`
	RelativeHumidity2m  string `json:"relative_humidity_2m,omitempty"`
	Precipitation       string `json:"precipitation,omitempty"`
	WeatherCode         string `json:"weather_code,omitempty"`
	WindSpeed10m        string `json:"wind_speed_10m,omitempty"`
	WindDirection10m    string `json:"wind_direction_10m,omitempty"`
}

type weatherCurrent struct {
	Time                string  `json:"time"`
	Temperature2m       float64 `json:"temperature_2m"`
	ApparentTemperature float64 `json:"apparent_temperature"`
	RelativeHumidity2m  float64 `json:"relative_humidity_2m"`
	Precipitation       float64 `json:"precipitation"`
	WeatherCode         int     `json:"weather_code"`
	WindSpeed10m        float64 `json:"wind_speed_10m"`
	WindDirection10m    float64 `json:"wind_direction_10m"`
}

func geocodeLocation(ctx context.Context, name string) (weatherLocation, error) {
	// 常用中文城市先本地命中，减少网络依赖；未命中再请求 geocoding API。
	if location, ok := localWeatherLocation(name); ok {
		return location, nil
	}

	query := url.Values{}
	query.Set("name", name)
	query.Set("count", "1")
	query.Set("language", "zh")
	query.Set("format", "json")

	var payload geocodingResponse
	if err := fetchSkillJSON(ctx, "https://geocoding-api.open-meteo.com/v1/search?"+query.Encode(), &payload); err != nil {
		return weatherLocation{}, err
	}
	if len(payload.Results) == 0 {
		return weatherLocation{}, fmt.Errorf("未找到地点 %q", name)
	}
	return payload.Results[0], nil
}

func localWeatherLocation(name string) (weatherLocation, bool) {
	normalized := normalizeWeatherLocationName(name)
	if normalized == "" {
		return weatherLocation{}, false
	}

	locations := map[string]weatherLocation{
		"北京": {Name: "北京", Latitude: 39.9042, Longitude: 116.4074, Timezone: "Asia/Shanghai", Country: "中国", Admin1: "北京市"},
		"上海": {Name: "上海", Latitude: 31.2304, Longitude: 121.4737, Timezone: "Asia/Shanghai", Country: "中国", Admin1: "上海市"},
		"广州": {Name: "广州", Latitude: 23.1291, Longitude: 113.2644, Timezone: "Asia/Shanghai", Country: "中国", Admin1: "广东省"},
		"深圳": {Name: "深圳", Latitude: 22.5431, Longitude: 114.0579, Timezone: "Asia/Shanghai", Country: "中国", Admin1: "广东省"},
		"杭州": {Name: "杭州", Latitude: 30.2741, Longitude: 120.1551, Timezone: "Asia/Shanghai", Country: "中国", Admin1: "浙江省"},
		"南京": {Name: "南京", Latitude: 32.0603, Longitude: 118.7969, Timezone: "Asia/Shanghai", Country: "中国", Admin1: "江苏省"},
		"济南": {Name: "济南", Latitude: 36.6512, Longitude: 117.1201, Timezone: "Asia/Shanghai", Country: "中国", Admin1: "山东省"},
		"青岛": {Name: "青岛", Latitude: 36.0671, Longitude: 120.3826, Timezone: "Asia/Shanghai", Country: "中国", Admin1: "山东省"},
		"潍坊": {Name: "潍坊", Latitude: 36.7068, Longitude: 119.1618, Timezone: "Asia/Shanghai", Country: "中国", Admin1: "山东省"},
		"安丘": {Name: "安丘", Latitude: 36.4785, Longitude: 119.2178, Timezone: "Asia/Shanghai", Country: "中国", Admin1: "山东省"},
	}

	aliases := map[string]string{
		"北京市":     "北京",
		"上海市":     "上海",
		"广州市":     "广州",
		"深圳市":     "深圳",
		"杭州市":     "杭州",
		"南京市":     "南京",
		"济南市":     "济南",
		"青岛市":     "青岛",
		"潍坊市":     "潍坊",
		"山东潍坊":    "潍坊",
		"山东省潍坊":   "潍坊",
		"山东省潍坊市":  "潍坊",
		"安丘市":     "安丘",
		"山东安丘":    "安丘",
		"山东省安丘":   "安丘",
		"山东省安丘市":  "安丘",
		"weifang": "潍坊",
		"anqiu":   "安丘",
	}
	if canonical, ok := aliases[normalized]; ok {
		normalized = canonical
	}

	location, ok := locations[normalized]
	return location, ok
}

func normalizeWeatherLocationName(name string) string {
	normalized := strings.TrimSpace(strings.ToLower(name))
	normalized = strings.ReplaceAll(normalized, " ", "")
	normalized = strings.ReplaceAll(normalized, "　", "")
	normalized = strings.Trim(normalized, "，,。.?？!！")
	return normalized
}

func fetchCurrentWeather(ctx context.Context, location weatherLocation) (currentWeatherResponse, error) {
	query := url.Values{}
	query.Set("latitude", fmt.Sprintf("%.6f", location.Latitude))
	query.Set("longitude", fmt.Sprintf("%.6f", location.Longitude))
	query.Set("current", "temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,weather_code,wind_speed_10m,wind_direction_10m")
	query.Set("timezone", "auto")

	var payload currentWeatherResponse
	if err := fetchSkillJSON(ctx, "https://api.open-meteo.com/v1/forecast?"+query.Encode(), &payload); err != nil {
		return currentWeatherResponse{}, err
	}
	return payload, nil
}

func fetchSkillJSON(ctx context.Context, endpoint string, target any) error {
	// 所有外部技能 HTTP JSON 请求走这里，便于统一 User-Agent 和错误处理。
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint, nil)
	if err != nil {
		return err
	}
	request.Header.Set("Accept", "application/json")
	request.Header.Set("User-Agent", "xiao-stream-skill/1.0")

	response, err := http.DefaultClient.Do(request)
	if err != nil {
		return err
	}
	defer response.Body.Close()

	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return fmt.Errorf("weather api returned HTTP %d", response.StatusCode)
	}
	return json.NewDecoder(response.Body).Decode(target)
}

func (s *server) executeSkillCall(ctx context.Context, source, name, callID, responseID string, args json.RawMessage, announceTranscript bool) aiSkillMessage {
	// 统一执行技能、计时、广播结果；DashScope 桥和后台意图匹配都调用这里。
	startedAt := time.Now()
	if s.skills == nil {
		return aiSkillMessage{}
	}

	displayName := name
	if skill, ok := s.skills.get(name); ok && skill.DisplayName != "" {
		displayName = skill.DisplayName
	}

	argsPayload := decodeSkillArgs(args)
	callCtx, cancel := context.WithTimeout(ctx, skillCallTimeout)
	defer cancel()

	result, summary, err := s.skills.execute(callCtx, name, args)
	status := "completed"
	errorMessage := ""
	if err != nil {
		status = "failed"
		errorMessage = err.Error()
		if summary == "" {
			summary = fmt.Sprintf("%s 调用失败：%s。", displayName, errorMessage)
		}
	}

	message := aiSkillMessage{
		Type:        "ai_skill",
		Name:        name,
		DisplayName: displayName,
		Status:      status,
		Summary:     summary,
		CallID:      callID,
		ResponseID:  responseID,
		Source:      source,
		Args:        argsPayload,
		Result:      result,
		Error:       errorMessage,
		DurationMs:  time.Since(startedAt).Milliseconds(),
		CreatedAt:   time.Now().Format(time.RFC3339),
	}

	s.broadcastJSON(message)
	if announceTranscript && summary != "" {
		s.broadcastJSON(aiTranscriptMessage{
			Type:       "ai_transcript",
			Role:       "assistant",
			Text:       summary,
			Final:      true,
			ResponseID: responseID,
		})
	}
	return message
}

func (s *server) skillIntentFromTranscript(text string) (skillIntent, bool) {
	if s.skills == nil {
		return skillIntent{}, false
	}
	return s.skills.matchIntent(text)
}

func (s *server) maybeRunSkillFromTranscript(text, responseID string) bool {
	if s.skills == nil {
		return false
	}

	intent, ok := s.skills.matchIntent(text)
	if !ok {
		return false
	}

	args, err := json.Marshal(intent.Args)
	if err != nil {
		return false
	}

	callID := fmt.Sprintf("transcript_skill_%d", time.Now().UnixNano())
	go s.executeSkillCall(context.Background(), "transcript", intent.Name, callID, responseID, args, true)
	return true
}

func buildSkillResponsePrompt(userText string, message aiSkillMessage) string {
	// 技能执行完成后，把结果包装成 prompt，让模型用自然语言复述，避免生硬 JSON。
	displayName := message.DisplayName
	if displayName == "" {
		displayName = message.Name
	}
	summary := strings.TrimSpace(message.Summary)
	if summary == "" && message.Error != "" {
		summary = fmt.Sprintf("%s 调用失败：%s。", displayName, message.Error)
	}
	if summary == "" {
		summary = fmt.Sprintf("%s 已调用完成。", displayName)
	}

	statusLine := "服务端技能调用成功。"
	if message.Status != "completed" {
		statusLine = "服务端技能调用失败。"
	}

	return fmt.Sprintf(
		"用户刚才说：%q。服务端已经先调用了技能「%s」。%s"+
			"技能结果摘要：%s\n\n请直接用简洁、自然的中文把这个结果回复给用户；不要重新判断意图，不要说你无法访问外部工具。",
		userText,
		displayName,
		statusLine,
		summary,
	)
}

func decodeSkillArgs(args json.RawMessage) any {
	if len(strings.TrimSpace(string(args))) == 0 {
		return map[string]any{}
	}

	var payload any
	if err := json.Unmarshal(args, &payload); err != nil {
		return string(args)
	}
	if payload == nil {
		return map[string]any{}
	}
	return payload
}

func extractWeatherLocation(text string) string {
	// 从“帮我查一下上海天气”这类句子中剥离天气关键词，得到地点。
	candidates := []string{"天气", "气温", "温度", "下雨", "降雨", "风速", "weather"}
	target := text
	firstIndex := -1
	for _, keyword := range candidates {
		if index := strings.Index(strings.ToLower(text), keyword); index >= 0 && (firstIndex == -1 || index < firstIndex) {
			firstIndex = index
		}
	}
	if firstIndex > 0 {
		target = text[:firstIndex]
	}

	location := cleanLocationText(target)
	if location == "" {
		location = cleanLocationText(text)
	}
	if location == "" {
		return "北京"
	}
	return location
}

func cleanLocationText(input string) string {
	// 清洗口语中的“帮我/查一下/现在/天气/吗”等噪声词。
	output := strings.TrimSpace(input)
	hasLeadingXiaResidue := strings.Contains(output, "查下") ||
		strings.Contains(output, "看下") ||
		strings.Contains(output, "问下") ||
		strings.HasPrefix(output, "下")
	replacements := []string{
		"请帮我查一下", "请帮我查下", "帮我查一下", "帮我查下", "帮忙查一下", "帮忙查下",
		"麻烦查一下", "麻烦查下", "查一查", "查一下", "查下", "看一下", "看下", "问一下", "问下",
		"帮我", "帮忙", "请", "麻烦", "能不能", "可以", "查询", "查", "看看",
		"当前", "现在", "今天", "明天", "此刻", "这边", "那里", "这里", "当地",
		"天气", "气温", "温度", "下雨", "降雨", "风速", "怎么样", "如何", "会不会", "会", "吗",
		"的", "一下", "？", "?", "，", ",", "。", ".", "！", "!",
	}
	sort.SliceStable(replacements, func(left, right int) bool {
		return len(replacements[left]) > len(replacements[right])
	})
	for _, item := range replacements {
		output = strings.ReplaceAll(output, item, "")
	}
	output = strings.TrimSpace(strings.Trim(output, "，,。.?？!！"))
	if hasLeadingXiaResidue && strings.HasPrefix(output, "下") {
		output = strings.TrimSpace(strings.TrimPrefix(output, "下"))
	}
	return strings.TrimSpace(output)
}

func extractFindObjectTarget(text string) (string, bool) {
	// 提取“帮我找一下 X”中的 X，作为 Python 寻物目标。
	normalized := strings.TrimSpace(text)
	if normalized == "" {
		return "", false
	}
	prefixes := []string{
		"帮我找一下", "帮我找下", "帮我找", "找一下", "找下", "寻找", "查找",
	}
	for _, prefix := range prefixes {
		if strings.Contains(normalized, prefix) {
			parts := strings.SplitN(normalized, prefix, 2)
			target := ""
			if len(parts) == 2 {
				target = parts[1]
			}
			target = strings.TrimSpace(strings.Trim(target, "，,。.?？!！"))
			target = strings.TrimPrefix(target, "这个")
			target = strings.TrimSpace(target)
			if target != "" {
				return target, true
			}
		}
	}
	return "", false
}

func inferTimezone(text string) string {
	// 少量中英文城市映射到 IANA 时区，默认使用中国时区。
	lower := strings.ToLower(text)
	switch {
	case strings.Contains(text, "纽约") || strings.Contains(lower, "new york"):
		return "America/New_York"
	case strings.Contains(text, "伦敦") || strings.Contains(lower, "london"):
		return "Europe/London"
	case strings.Contains(text, "东京") || strings.Contains(lower, "tokyo"):
		return "Asia/Tokyo"
	case strings.Contains(text, "北京") || strings.Contains(text, "上海") || strings.Contains(text, "中国"):
		return "Asia/Shanghai"
	case strings.Contains(lower, "utc") || strings.Contains(text, "世界时"):
		return "UTC"
	default:
		return "Asia/Shanghai"
	}
}

func containsAny(text string, keywords []string) bool {
	for _, keyword := range keywords {
		if strings.Contains(text, keyword) {
			return true
		}
	}
	return false
}

func chineseWeekday(day time.Weekday) string {
	switch day {
	case time.Monday:
		return "星期一"
	case time.Tuesday:
		return "星期二"
	case time.Wednesday:
		return "星期三"
	case time.Thursday:
		return "星期四"
	case time.Friday:
		return "星期五"
	case time.Saturday:
		return "星期六"
	default:
		return "星期日"
	}
}

func formatWeatherPlace(location weatherLocation) string {
	parts := make([]string, 0, 3)
	if location.Country != "" {
		parts = append(parts, location.Country)
	}
	if location.Admin1 != "" && location.Admin1 != location.Name {
		parts = append(parts, location.Admin1)
	}
	if location.Name != "" {
		parts = append(parts, location.Name)
	}
	return strings.Join(parts, " ")
}

func weatherCodeText(code int) string {
	switch code {
	case 0:
		return "晴朗"
	case 1, 2, 3:
		return "多云"
	case 45, 48:
		return "有雾"
	case 51, 53, 55, 56, 57:
		return "有毛毛雨"
	case 61, 63, 65, 66, 67:
		return "有降雨"
	case 71, 73, 75, 77:
		return "有降雪"
	case 80, 81, 82:
		return "有阵雨"
	case 85, 86:
		return "有阵雪"
	case 95, 96, 99:
		return "有雷暴"
	default:
		return fmt.Sprintf("天气代码 %d", code)
	}
}
