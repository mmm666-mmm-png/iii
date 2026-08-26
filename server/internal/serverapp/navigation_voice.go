package serverapp

import (
	"bytes"
	"encoding/binary"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"
	"unicode"
)

const (
	navigationVoiceMapFile = "map.zh-CN.json"
)

type navigationVoiceLibrary struct {
	// 预录导航语音库：中文提示文本 -> wav 文件 -> devicePlaybackChunk 缓存。
	dir     string
	entries map[string]navigationVoiceEntry
	cache   map[string]devicePlaybackChunk
	mu      sync.Mutex
}

type navigationVoiceEntry struct {
	Files      []string `json:"files"`
	DurationMs int      `json:"duration_ms"`
}

type wavData struct {
	sampleRate    int
	channels      int
	bitsPerSample int
	payload       []byte
}

func newNavigationVoiceLibrary(dir string) (*navigationVoiceLibrary, error) {
	// 读取 python_worker/voice/map.zh-CN.json，并建立规范化文本索引。
	dir = strings.TrimSpace(dir)
	if dir == "" {
		return nil, nil
	}

	mapPath := filepath.Join(dir, navigationVoiceMapFile)
	mapBytes, err := os.ReadFile(mapPath)
	if err != nil {
		return nil, err
	}

	var rawEntries map[string]navigationVoiceEntry
	if err := json.Unmarshal(mapBytes, &rawEntries); err != nil {
		return nil, err
	}

	entries := make(map[string]navigationVoiceEntry, len(rawEntries)*2)
	for text, entry := range rawEntries {
		normalized := normalizeNavigationVoiceText(text)
		if normalized == "" || len(entry.Files) == 0 {
			continue
		}
		entries[normalized] = entry
	}
	addNavigationVoiceAliases(entries)
	if len(entries) == 0 {
		return nil, fmt.Errorf("no navigation voice entries loaded from %s", mapPath)
	}

	return &navigationVoiceLibrary{
		dir:     dir,
		entries: entries,
		cache:   make(map[string]devicePlaybackChunk),
	}, nil
}

func addNavigationVoiceAliases(entries map[string]navigationVoiceEntry) {
	// 后端生成的状态提示可能和录音文案略有差异，这里做少量别名映射。
	aliases := map[string]string{
		"盲道导航已启动":  "切换到盲道导航",
		"导航已停止":    "已停止导航",
		"视觉状态已重置":  "导航已被取消",
		"红绿灯检测已启动": "正在等待绿灯",
	}
	for alias, target := range aliases {
		aliasKey := normalizeNavigationVoiceText(alias)
		targetKey := normalizeNavigationVoiceText(target)
		if aliasKey == "" || targetKey == "" {
			continue
		}
		if _, exists := entries[aliasKey]; exists {
			continue
		}
		if entry, ok := entries[targetKey]; ok {
			entries[aliasKey] = entry
		}
	}
}

func (l *navigationVoiceLibrary) chunkForText(text string) (devicePlaybackChunk, bool, error) {
	// 根据提示文本找到 wav，首次读取后缓存 PCM，后续直接复用。
	if l == nil {
		return devicePlaybackChunk{}, false, nil
	}
	normalized := normalizeNavigationVoiceText(text)
	if normalized == "" {
		return devicePlaybackChunk{}, false, nil
	}

	l.mu.Lock()
	defer l.mu.Unlock()

	if chunk, ok := l.cache[normalized]; ok {
		chunk.payload = append([]byte(nil), chunk.payload...)
		return chunk, true, nil
	}

	entry, ok := l.entries[normalized]
	if !ok {
		return devicePlaybackChunk{}, false, nil
	}

	var lastErr error
	for _, name := range entry.Files {
		path := filepath.Clean(filepath.Join(l.dir, filepath.FromSlash(name)))
		wav, err := readPCM16Wav(path)
		if err != nil {
			lastErr = err
			continue
		}
		chunk := devicePlaybackChunk{
			sampleRate:    wav.sampleRate,
			channels:      wav.channels,
			bitsPerSample: wav.bitsPerSample,
			payload:       wav.payload,
		}
		l.cache[normalized] = chunk
		chunk.payload = append([]byte(nil), chunk.payload...)
		return chunk, true, nil
	}
	if lastErr != nil {
		return devicePlaybackChunk{}, true, lastErr
	}
	return devicePlaybackChunk{}, true, errors.New("navigation voice entry has no usable wav files")
}

func normalizeNavigationVoiceText(text string) string {
	// 去掉标点、空白和“[导航]”前缀，降低文本匹配对标点差异的敏感度。
	text = strings.TrimSpace(text)
	text = strings.TrimPrefix(text, "[导航]")
	text = strings.TrimSpace(text)
	text = strings.ReplaceAll(text, "，", ",")
	text = strings.ReplaceAll(text, "。", "")
	text = strings.ReplaceAll(text, ".", "")
	text = strings.ReplaceAll(text, "！", "!")
	text = strings.ReplaceAll(text, "？", "?")
	text = strings.ReplaceAll(text, "…", "")
	text = strings.ReplaceAll(text, "‘", "'")
	text = strings.ReplaceAll(text, "’", "'")
	text = strings.TrimFunc(text, func(r rune) bool {
		return unicode.IsSpace(r) || strings.ContainsRune(".,!?;:，。！？；：", r)
	})
	text = strings.Join(strings.Fields(text), "")
	return strings.ToLower(text)
}

func readPCM16Wav(path string) (wavData, error) {
	// 手写最小 WAV 解析器，支持 fmt/data/List 等 chunk 顺序变化，只接受 PCM16。
	data, err := os.ReadFile(path)
	if err != nil {
		return wavData{}, err
	}
	if len(data) < 12 || string(data[0:4]) != "RIFF" || string(data[8:12]) != "WAVE" {
		return wavData{}, fmt.Errorf("%s is not a RIFF/WAVE file", path)
	}

	reader := bytes.NewReader(data[12:])
	var sampleRate int
	var channels int
	var bitsPerSample int
	var audioFormat uint16
	var payload []byte

	for reader.Len() >= 8 {
		var chunkID [4]byte
		if _, err := io.ReadFull(reader, chunkID[:]); err != nil {
			return wavData{}, err
		}
		var chunkSize uint32
		if err := binary.Read(reader, binary.LittleEndian, &chunkSize); err != nil {
			return wavData{}, err
		}
		if uint64(chunkSize) > uint64(reader.Len()) {
			return wavData{}, fmt.Errorf("%s has truncated wav chunk %q", path, string(chunkID[:]))
		}
		chunk := make([]byte, chunkSize)
		if _, err := io.ReadFull(reader, chunk); err != nil {
			return wavData{}, err
		}
		if chunkSize%2 == 1 && reader.Len() > 0 {
			if _, err := reader.ReadByte(); err != nil {
				return wavData{}, err
			}
		}

		switch string(chunkID[:]) {
		case "fmt ":
			if len(chunk) < 16 {
				return wavData{}, fmt.Errorf("%s has invalid fmt chunk", path)
			}
			audioFormat = binary.LittleEndian.Uint16(chunk[0:2])
			channels = int(binary.LittleEndian.Uint16(chunk[2:4]))
			sampleRate = int(binary.LittleEndian.Uint32(chunk[4:8]))
			bitsPerSample = int(binary.LittleEndian.Uint16(chunk[14:16]))
		case "data":
			payload = append([]byte(nil), chunk...)
		}
	}

	if audioFormat != 1 {
		return wavData{}, fmt.Errorf("%s uses unsupported wav format %d", path, audioFormat)
	}
	if sampleRate <= 0 || channels <= 0 || bitsPerSample != 16 || len(payload) == 0 {
		return wavData{}, fmt.Errorf("%s has unsupported wav audio format", path)
	}

	if channels != 1 {
		mono, err := mixPCM16ToMono(payload, channels)
		if err != nil {
			return wavData{}, err
		}
		payload = mono
		channels = 1
	}

	if len(payload)%2 == 1 {
		payload = payload[:len(payload)-1]
	}
	return wavData{
		sampleRate:    sampleRate,
		channels:      channels,
		bitsPerSample: bitsPerSample,
		payload:       payload,
	}, nil
}

func mixPCM16ToMono(payload []byte, channels int) ([]byte, error) {
	// 多声道录音转 mono，ESP32 扬声器下行协议目前只支持单声道。
	if channels <= 0 {
		return nil, fmt.Errorf("invalid channel count %d", channels)
	}
	frameBytes := channels * 2
	if len(payload)%frameBytes != 0 {
		return nil, fmt.Errorf("invalid pcm payload length %d for %d channels", len(payload), channels)
	}

	frames := len(payload) / frameBytes
	out := make([]byte, frames*2)
	for frame := 0; frame < frames; frame++ {
		sum := 0
		base := frame * frameBytes
		for channel := 0; channel < channels; channel++ {
			offset := base + channel*2
			sum += int(int16(binary.LittleEndian.Uint16(payload[offset : offset+2])))
		}
		sample := int16(sum / channels)
		binary.LittleEndian.PutUint16(out[frame*2:], uint16(sample))
	}
	return out, nil
}

func (s *server) enqueueNavigationVoiceForDevice(text string) {
	// 视觉引导优先级高：清空 AI 语音队列后播放预录导航提示。
	if s.navigationVoice == nil {
		return
	}

	chunk, found, err := s.navigationVoice.chunkForText(resolveNavigationVoiceFallback(text))
	if err != nil {
		log.Printf("navigation voice load failed for %q: %v", text, err)
		return
	}
	if !found {
		log.Printf("navigation voice not found for %q", text)
		return
	}
	if chunk.payload == nil {
		return
	}

	s.clearDevicePlaybackQueue()
	select {
	case s.devicePlaybackCh <- chunk:
	default:
		s.clearDevicePlaybackQueue()
		select {
		case s.devicePlaybackCh <- chunk:
		default:
			log.Printf("device playback queue full, dropping navigation voice %q", text)
		}
	}
}

// enqueueNavigationVoiceSequenceForDevice 把一串导航指令按顺序下发到设备扬声器，
// 用于逐段路线播报。仅保留能命中预录语音的指令，避免队列被大量无效片段占满。
func (s *server) enqueueNavigationVoiceSequenceForDevice(texts []string) {
	if s.navigationVoice == nil || len(texts) == 0 {
		return
	}

	s.enqueueNavigationVoiceSequenceWithAudioForDevice(
		devicePlaybackChunk{},
		s.navigationVoiceChunksForTexts(texts),
	)
}

func (s *server) navigationVoiceChunksForTexts(texts []string) []devicePlaybackChunk {
	if s.navigationVoice == nil || len(texts) == 0 {
		return nil
	}

	chunks := make([]devicePlaybackChunk, 0, len(texts))
	for _, text := range texts {
		text = strings.TrimSpace(text)
		if text == "" {
			continue
		}
		chunk, found, err := s.navigationVoice.chunkForText(resolveNavigationVoiceFallback(text))
		if err != nil {
			log.Printf("navigation voice load failed for %q: %v", text, err)
			continue
		}
		if !found || chunk.payload == nil {
			continue
		}
		chunks = append(chunks, chunk)
	}
	return chunks
}

func (s *server) enqueueNavigationVoiceSequenceWithAudioForDevice(first devicePlaybackChunk, chunks []devicePlaybackChunk) {
	allChunks := make([]devicePlaybackChunk, 0, len(chunks)+1)
	if len(first.payload) > 0 {
		allChunks = append(allChunks, first)
	}
	allChunks = append(allChunks, chunks...)
	if len(allChunks) == 0 {
		return
	}
	s.clearDevicePlaybackQueue()
	for index, chunk := range allChunks {
		if index < len(allChunks)-1 {
			chunk.pauseAfter = 700 * time.Millisecond
		}
		select {
		case s.devicePlaybackCh <- chunk:
		default:
			log.Printf("device playback queue full, dropping navigation voice sequence")
			return
		}
	}
}

// resolveNavigationVoiceFallback 把高德路线规划生成的动态播报文本映射到
// 预录导航语音。动态文本包含距离/时间/盲道覆盖率，无法逐字命中 wav，
// 因此统一降级到通用的“已为您规划好路线”提示。
func resolveNavigationVoiceFallback(text string) string {
	t := strings.TrimSpace(text)
	t = strings.TrimPrefix(t, "[导航]")
	t = strings.TrimSpace(t)

	if strings.Contains(t, "已为您规划好") {
		return "已为您规划好路线，请确认安全后出发。"
	}

	// 高德转弯指令通常是“沿XX路步行…右转进入XX路”这类长句，
	// 按关键词降级到预录的短指令，保证设备端能播报关键导航动作。
	if strings.Contains(t, "到达") || strings.Contains(t, "终点") {
		return "已到达目标，引导结束。"
	}
	if strings.Contains(t, "右转") {
		return "右转"
	}
	if strings.Contains(t, "左转") {
		return "左转"
	}
	if strings.Contains(t, "直行") {
		return "保持直行"
	}

	return t
}
