package serverapp

import (
	"sync"
	"time"
)

type streamQualityStats struct {
	// 前端质量面板直接展示这些字段，用于判断 Wi-Fi 丢包、视频 FPS、音频块速率。
	StartedAt               string  `json:"startedAt,omitempty"`
	UptimeSeconds           int64   `json:"uptimeSeconds"`
	DatagramsSeen           uint64  `json:"datagramsSeen"`
	BytesSeen               uint64  `json:"bytesSeen"`
	VideoFragmentsSeen      uint64  `json:"videoFragmentsSeen"`
	VideoFramesReassembled  uint64  `json:"videoFramesReassembled"`
	VideoFragmentDuplicates uint64  `json:"videoFragmentDuplicates"`
	VideoIncompleteFrames   uint64  `json:"videoIncompleteFrames"`
	VideoSeqGaps            uint64  `json:"videoSeqGaps"`
	AudioSeqGaps            uint64  `json:"audioSeqGaps"`
	DroppedPackets          uint64  `json:"droppedPackets"`
	VideoFPS                float64 `json:"videoFps"`
	AudioChunksPerSecond    float64 `json:"audioChunksPerSecond"`
	AverageVideoBytes       int     `json:"averageVideoBytes"`
	AverageAudioBytes       int     `json:"averageAudioBytes"`
	LastVideoSeq            uint64  `json:"lastVideoSeq,omitempty"`
	LastAudioSeq            uint64  `json:"lastAudioSeq,omitempty"`
	LastPacketAt            string  `json:"lastPacketAt,omitempty"`
}

type streamStatsTracker struct {
	// 所有计数都在 UDP 接收路径更新，snapshot 时计算最近 10 秒速率。
	mu sync.Mutex

	startedAt    time.Time
	lastPacketAt time.Time

	datagramsSeen uint64
	bytesSeen     uint64

	videoFragmentsSeen      uint64
	videoFramesReassembled  uint64
	videoFragmentDuplicates uint64
	videoIncompleteFrames   uint64
	videoSeqGaps            uint64
	videoBytesTotal         uint64
	lastVideoSeq            uint64
	hasVideoSeq             bool
	videoFrameTimes         []time.Time

	audioChunksSeen uint64
	audioSeqGaps    uint64
	audioBytesTotal uint64
	lastAudioSeq    uint64
	hasAudioSeq     bool
	audioChunkTimes []time.Time
	droppedPackets  uint64
}

func newStreamStatsTracker() *streamStatsTracker {
	return &streamStatsTracker{
		startedAt: time.Now(),
	}
}

func (s *streamStatsTracker) noteDatagram(size int) {
	if s == nil {
		return
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	s.datagramsSeen++
	if size > 0 {
		s.bytesSeen += uint64(size)
	}
	s.lastPacketAt = time.Now()
}

func (s *streamStatsTracker) noteDroppedPacket() {
	if s == nil {
		return
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	s.droppedPackets++
}

func (s *streamStatsTracker) noteVideoFragment() {
	if s == nil {
		return
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	s.videoFragmentsSeen++
}

func (s *streamStatsTracker) noteVideoDuplicateFragment() {
	if s == nil {
		return
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	s.videoFragmentDuplicates++
}

func (s *streamStatsTracker) noteIncompleteVideoFrame() {
	if s == nil {
		return
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	s.videoIncompleteFrames++
}

func (s *streamStatsTracker) noteVideoFrame(seq uint64, bytes int) {
	if s == nil {
		return
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	now := time.Now()
	if s.hasVideoSeq && seq > s.lastVideoSeq+1 {
		// 序号跳变说明整帧丢失，通常是 Wi-Fi 包丢失或分片未收齐。
		s.videoSeqGaps += seq - s.lastVideoSeq - 1
	}
	s.hasVideoSeq = true
	s.lastVideoSeq = seq
	s.videoFramesReassembled++
	if bytes > 0 {
		s.videoBytesTotal += uint64(bytes)
	}
	s.videoFrameTimes = appendRecentTimes(s.videoFrameTimes, now, 10*time.Second)
	s.lastPacketAt = now
}

func (s *streamStatsTracker) noteAudioChunk(seq uint64, bytes int) {
	if s == nil {
		return
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	now := time.Now()
	if s.hasAudioSeq && seq > s.lastAudioSeq+1 {
		// 音频序号跳变会直接影响 ASR 连续性，因此单独统计。
		s.audioSeqGaps += seq - s.lastAudioSeq - 1
	}
	s.hasAudioSeq = true
	s.lastAudioSeq = seq
	s.audioChunksSeen++
	if bytes > 0 {
		s.audioBytesTotal += uint64(bytes)
	}
	s.audioChunkTimes = appendRecentTimes(s.audioChunkTimes, now, 10*time.Second)
	s.lastPacketAt = now
}

func (s *streamStatsTracker) snapshot() streamQualityStats {
	if s == nil {
		return streamQualityStats{}
	}
	s.mu.Lock()
	defer s.mu.Unlock()

	now := time.Now()
	s.videoFrameTimes = appendRecentTimes(s.videoFrameTimes, now, 10*time.Second)
	s.audioChunkTimes = appendRecentTimes(s.audioChunkTimes, now, 10*time.Second)

	out := streamQualityStats{
		StartedAt:               s.startedAt.Format(time.RFC3339),
		UptimeSeconds:           int64(now.Sub(s.startedAt).Seconds()),
		DatagramsSeen:           s.datagramsSeen,
		BytesSeen:               s.bytesSeen,
		VideoFragmentsSeen:      s.videoFragmentsSeen,
		VideoFramesReassembled:  s.videoFramesReassembled,
		VideoFragmentDuplicates: s.videoFragmentDuplicates,
		VideoIncompleteFrames:   s.videoIncompleteFrames,
		VideoSeqGaps:            s.videoSeqGaps,
		AudioSeqGaps:            s.audioSeqGaps,
		DroppedPackets:          s.droppedPackets,
		VideoFPS:                rateFromTimes(s.videoFrameTimes),
		AudioChunksPerSecond:    rateFromTimes(s.audioChunkTimes),
		LastVideoSeq:            s.lastVideoSeq,
		LastAudioSeq:            s.lastAudioSeq,
	}
	if !s.lastPacketAt.IsZero() {
		out.LastPacketAt = s.lastPacketAt.Format(time.RFC3339)
	}
	if s.videoFramesReassembled > 0 {
		out.AverageVideoBytes = int(s.videoBytesTotal / s.videoFramesReassembled)
	}
	if s.audioChunksSeen > 0 {
		out.AverageAudioBytes = int(s.audioBytesTotal / s.audioChunksSeen)
	}
	return out
}

func appendRecentTimes(items []time.Time, now time.Time, window time.Duration) []time.Time {
	// 原地过滤时间窗口，减少每秒 snapshot 的分配。
	cutoff := now.Add(-window)
	write := 0
	for _, item := range items {
		if item.After(cutoff) {
			items[write] = item
			write++
		}
	}
	return items[:write]
}

func rateFromTimes(items []time.Time) float64 {
	// 用首尾时间估算速率，比简单 len/window 对短窗口更准确。
	if len(items) < 2 {
		return float64(len(items))
	}
	span := items[len(items)-1].Sub(items[0]).Seconds()
	if span <= 0 {
		return float64(len(items))
	}
	return float64(len(items)-1) / span
}
