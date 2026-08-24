package serverapp

import (
	"bufio"
	"flag"
	"os"
	"path/filepath"
	"strconv"
	"strings"
)

// 默认系统提示词：用于 DashScope Realtime 连接，约束 AI 结合设备音视频回答。
const defaultDashScopeInstructions = "你是连接在 XIAO ESP32S3 Sense 设备上的多模态语音助手。请结合实时音频和摄像头画面，用简洁、自然、友好的中文回答用户；如果画面或语音信息不足，就明确说明你不确定。"

// Config 汇总服务端所有可配置项。配置来源优先级：
// 命令行参数 > 环境变量/.env > 代码默认值。
type Config struct {
	HTTPAddr              string
	UDPAddr               string
	DeviceToken           string
	CORSAllowOrigin       string
	VisionWorkerURL       string
	VisionFrameIntervalMs int
	DashScopeAPIKey       string
	DashScopeRegion       string
	DashScopeModel        string
	DashScopeVoice        string
	DashScopeInstructions string
	DashScopeEnableSearch bool
	NavigationVoiceDir    string
}

func LoadConfig(args []string) (Config, error) {
	// 先加载 .env，再解析命令行。已经存在的环境变量不会被 .env 覆盖。
	loadEnvFiles()

	cfg := Config{
		HTTPAddr:              envOrDefault("SERVER_HTTP_ADDR", ":8888"),
		UDPAddr:               envOrDefault("SERVER_UDP_ADDR", ":8888"),
		DeviceToken:           envOrDefault("DEVICE_TOKEN", "change-me"),
		CORSAllowOrigin:       envOrDefault("CORS_ALLOW_ORIGIN", "*"),
		VisionWorkerURL:       envOrDefault("VISION_WORKER_URL", "http://127.0.0.1:18082"),
		VisionFrameIntervalMs: envInt("VISION_FRAME_INTERVAL_MS", 450),
		DashScopeAPIKey:       envOrDefault("DASHSCOPE_API_KEY", ""),
		DashScopeRegion:       envOrDefault("DASHSCOPE_REGION", "cn"),
		DashScopeModel:        envOrDefault("DASHSCOPE_MODEL", "qwen3-omni-flash-realtime"),
		DashScopeVoice:        envOrDefault("DASHSCOPE_VOICE", "Ethan"),
		DashScopeInstructions: envOrDefault("DASHSCOPE_INSTRUCTIONS", defaultDashScopeInstructions),
		DashScopeEnableSearch: envBool("DASHSCOPE_ENABLE_SEARCH", false),
		NavigationVoiceDir:    envOrDefault("NAVIGATION_VOICE_DIR", "../python_worker/voice"),
	}

	fs := flag.NewFlagSet("xiao-stream", flag.ContinueOnError)
	fs.StringVar(&cfg.HTTPAddr, "http-addr", cfg.HTTPAddr, "HTTP listen address")
	fs.StringVar(&cfg.HTTPAddr, "listen", cfg.HTTPAddr, "legacy alias for -http-addr")
	fs.StringVar(&cfg.UDPAddr, "udp-addr", cfg.UDPAddr, "UDP listen address for ESP32 device packets")
	fs.StringVar(&cfg.UDPAddr, "udp-listen", cfg.UDPAddr, "legacy alias for -udp-addr")
	fs.StringVar(&cfg.DeviceToken, "device-token", cfg.DeviceToken, "shared token for the ESP32 device")
	fs.StringVar(&cfg.CORSAllowOrigin, "cors-allow-origin", cfg.CORSAllowOrigin, "CORS allow origin for the separate frontend")
	fs.StringVar(&cfg.VisionWorkerURL, "vision-worker-url", cfg.VisionWorkerURL, "optional Python vision worker URL")
	fs.IntVar(&cfg.VisionFrameIntervalMs, "vision-frame-interval-ms", cfg.VisionFrameIntervalMs, "minimum interval between frames sent to the vision worker")
	fs.StringVar(&cfg.DashScopeAPIKey, "dashscope-api-key", cfg.DashScopeAPIKey, "optional DashScope API key for Bailian realtime bridge")
	fs.StringVar(&cfg.DashScopeRegion, "dashscope-region", cfg.DashScopeRegion, "DashScope region: cn or intl")
	fs.StringVar(&cfg.DashScopeModel, "dashscope-model", cfg.DashScopeModel, "DashScope realtime model name")
	fs.StringVar(&cfg.DashScopeVoice, "dashscope-voice", cfg.DashScopeVoice, "DashScope realtime voice")
	fs.StringVar(&cfg.DashScopeInstructions, "dashscope-instructions", cfg.DashScopeInstructions, "DashScope system instructions")
	fs.BoolVar(&cfg.DashScopeEnableSearch, "dashscope-enable-search", cfg.DashScopeEnableSearch, "enable DashScope realtime web search")
	fs.StringVar(&cfg.NavigationVoiceDir, "navigation-voice-dir", cfg.NavigationVoiceDir, "directory containing pregenerated navigation TTS wav files and map.zh-CN.json")
	if err := fs.Parse(args); err != nil {
		return Config{}, err
	}

	return cfg, nil
}

func loadEnvFiles() {
	// 兼容从仓库根目录或 server 目录启动两种方式。
	candidates := []string{
		".env.local",
		".env",
		filepath.Join("server", ".env.local"),
		filepath.Join("server", ".env"),
	}
	for _, path := range candidates {
		_ = loadEnvFile(path)
	}
}

func loadEnvFile(path string) error {
	// 简单 .env 解析器：不依赖第三方库，支持 KEY=value、export KEY=value 和引号。
	file, err := os.Open(path)
	if err != nil {
		return err
	}
	defer file.Close()

	scanner := bufio.NewScanner(file)
	for scanner.Scan() {
		key, value, ok := parseEnvLine(scanner.Text())
		if !ok {
			continue
		}
		if _, exists := os.LookupEnv(key); exists {
			continue
		}
		_ = os.Setenv(key, value)
	}
	return scanner.Err()
}

func parseEnvLine(line string) (string, string, bool) {
	// 去掉 UTF-8 BOM，兼容 Windows 编辑器保存的 .env 文件。
	line = strings.TrimSpace(strings.TrimPrefix(line, "\ufeff"))
	if line == "" || strings.HasPrefix(line, "#") {
		return "", "", false
	}
	line = strings.TrimSpace(strings.TrimPrefix(line, "export "))

	key, value, found := strings.Cut(line, "=")
	if !found {
		return "", "", false
	}
	key = strings.TrimSpace(key)
	if key == "" {
		return "", "", false
	}

	value = strings.TrimSpace(value)
	if len(value) >= 2 {
		quote := value[0]
		if (quote == '"' || quote == '\'') && value[len(value)-1] == quote {
			unquoted, err := strconv.Unquote(value)
			if err == nil {
				value = unquoted
			} else {
				value = value[1 : len(value)-1]
			}
		}
	}
	if index := strings.Index(value, " #"); index >= 0 {
		value = strings.TrimSpace(value[:index])
	}
	return key, value, true
}

func envOrDefault(name, fallback string) string {
	if value := os.Getenv(name); value != "" {
		return value
	}
	return fallback
}

func envBool(name string, fallback bool) bool {
	// 对布尔环境变量做宽松解析，未知值回退默认值，避免启动失败。
	value := strings.TrimSpace(strings.ToLower(os.Getenv(name)))
	if value == "" {
		return fallback
	}
	switch value {
	case "1", "true", "yes", "on", "enabled":
		return true
	case "0", "false", "no", "off", "disabled":
		return false
	default:
		return fallback
	}
}

func envInt(name string, fallback int) int {
	// 整型配置解析失败时回退默认值。
	value := strings.TrimSpace(os.Getenv(name))
	if value == "" {
		return fallback
	}
	parsed, err := strconv.Atoi(value)
	if err != nil {
		return fallback
	}
	return parsed
}
