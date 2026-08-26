package serverapp

import (
	"encoding/binary"
	"errors"
	"log"
	"net"
	"time"
)

const (
	// 与 ESP32 固件保持一致：udpTypeAIAudio 表示服务端下发到设备扬声器的 PCM。
	udpTypeAIAudio         = 4
	udpDefaultPlaybackPort = 3334
	udpPlaybackChunkBytes  = 960
)

type devicePlaybackChunk struct {
	// 当前只发送 PCM16 mono；字段仍保留，便于以后支持更多格式。
	sampleRate    int
	channels      int
	bitsPerSample int
	payload       []byte
	pauseAfter    time.Duration
}

type devicePlaybackSendResult struct {
	Target        string
	DatagramsSent int
	BytesSent     int
}

func (s *server) setDevicePlaybackConn(conn net.PacketConn) {
	s.devicePlaybackMu.Lock()
	defer s.devicePlaybackMu.Unlock()
	s.devicePlaybackConn = conn
}

func (s *server) clearDevicePlaybackConn(conn net.PacketConn) {
	s.devicePlaybackMu.Lock()
	defer s.devicePlaybackMu.Unlock()
	if s.devicePlaybackConn == conn {
		s.devicePlaybackConn = nil
	}
}

func (s *server) rememberDevicePlaybackAddr(ip net.IP, port int) {
	// 设备 hello 会带 speaker.audioPort；服务端记住 IP:port 后即可回写 AI 语音。
	if ip == nil {
		return
	}
	if port <= 0 || port > 65535 {
		port = udpDefaultPlaybackPort
	}

	addr := &net.UDPAddr{
		IP:   append(net.IP(nil), ip...),
		Port: port,
	}

	s.devicePlaybackMu.Lock()
	defer s.devicePlaybackMu.Unlock()
	s.devicePlaybackAddr = addr
}

func (s *server) clearDevicePlaybackAddr() {
	s.devicePlaybackMu.Lock()
	defer s.devicePlaybackMu.Unlock()
	s.devicePlaybackAddr = nil
}

func (s *server) nextDevicePlaybackSeq() uint32 {
	s.devicePlaybackMu.Lock()
	defer s.devicePlaybackMu.Unlock()
	s.devicePlaybackSeq++
	return s.devicePlaybackSeq
}

func (s *server) snapshotDevicePlaybackTarget() (net.PacketConn, *net.UDPAddr) {
	s.devicePlaybackMu.RLock()
	defer s.devicePlaybackMu.RUnlock()

	if s.devicePlaybackConn == nil || s.devicePlaybackAddr == nil {
		return nil, nil
	}

	addrCopy := *s.devicePlaybackAddr
	addrCopy.IP = append(net.IP(nil), s.devicePlaybackAddr.IP...)
	return s.devicePlaybackConn, &addrCopy
}

func (s *server) enqueueAIAudioForDevice(meta *PacketMeta, payload []byte) {
	// 只接受 PCM16 mono 的 AI 音频；其他格式浏览器可播，但设备端暂不处理。
	if meta == nil || len(payload) == 0 {
		return
	}

	if meta.SampleRate <= 0 || meta.Channels != 1 || meta.BitsPerSample != 16 {
		return
	}

	chunk := devicePlaybackChunk{
		sampleRate:    meta.SampleRate,
		channels:      meta.Channels,
		bitsPerSample: meta.BitsPerSample,
		payload:       append([]byte(nil), payload...),
	}

	select {
	case s.devicePlaybackCh <- chunk:
	default:
		// 队列满时丢最旧块，避免设备端播报明显落后当前对话/导航。
		select {
		case <-s.devicePlaybackCh:
		default:
		}
		select {
		case s.devicePlaybackCh <- chunk:
		default:
			log.Printf("device playback queue full, dropping %d bytes", len(payload))
		}
	}
}

func (s *server) clearDevicePlaybackQueue() {
	for {
		select {
		case <-s.devicePlaybackCh:
		default:
			return
		}
	}
}

func (s *server) devicePlaybackLoop() {
	// 单 goroutine 串行下发，保持音频包顺序和节拍。
	for chunk := range s.devicePlaybackCh {
		if _, err := s.sendAIAudioToDeviceNow(chunk); err != nil {
			log.Printf("device playback udp send failed: %v", err)
		}
		if chunk.pauseAfter > 0 {
			time.Sleep(chunk.pauseAfter)
		}
	}
}

func (s *server) sendAIAudioToDeviceNow(chunk devicePlaybackChunk) (devicePlaybackSendResult, error) {
	// 按设备 UDP 协议拆包，并跟随音频时钟 sleep，避免瞬间把大量包打爆 Wi-Fi。
	conn, addr := s.snapshotDevicePlaybackTarget()
	if conn == nil || addr == nil {
		return devicePlaybackSendResult{}, errors.New("device playback target is not ready")
	}

	result := devicePlaybackSendResult{Target: addr.String()}
	bytesPerSecond := chunk.sampleRate * chunk.channels * (chunk.bitsPerSample / 8)
	if bytesPerSecond <= 0 {
		return result, errors.New("invalid playback audio format")
	}

	timestampMs := uint32(time.Now().UnixMilli())
	for offset := 0; offset < len(chunk.payload); {
		chunkLen := len(chunk.payload) - offset
		if chunkLen > udpPlaybackChunkBytes {
			chunkLen = udpPlaybackChunkBytes
		}
		if chunk.bitsPerSample == 16 && (chunkLen&1) == 1 {
			chunkLen--
		}
		if chunkLen <= 0 {
			break
		}

		packet := make([]byte, udpAudioHeaderSize+chunkLen)
		// 复用音频包头布局，但 type 改为 ai_audio，ESP32 会写入 I2S 扬声器。
		packet[0] = udpMagic0
		packet[1] = udpMagic1
		packet[2] = udpVersion
		packet[3] = udpTypeAIAudio
		binary.LittleEndian.PutUint32(packet[4:8], s.nextDevicePlaybackSeq())
		binary.LittleEndian.PutUint32(packet[8:12], timestampMs)
		binary.LittleEndian.PutUint16(packet[12:14], uint16(chunk.sampleRate))
		packet[14] = byte(chunk.channels)
		packet[15] = byte(chunk.bitsPerSample)
		binary.LittleEndian.PutUint16(packet[16:18], uint16(chunkLen))
		copy(packet[udpAudioHeaderSize:], chunk.payload[offset:offset+chunkLen])

		if _, err := conn.WriteTo(packet, addr); err != nil {
			return result, err
		}

		result.DatagramsSent++
		result.BytesSent += len(packet)
		offset += chunkLen

		// Follow the audio clock instead of flushing bursts as fast as possible.
		playbackDelay := time.Duration(chunkLen) * time.Second / time.Duration(bytesPerSecond)
		if playbackDelay > 0 {
			time.Sleep(playbackDelay)
		}
	}

	return result, nil
}
