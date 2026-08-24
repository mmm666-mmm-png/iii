package serverapp

import (
	"encoding/binary"
	"encoding/json"
	"log"
	"net"
	"sync"
	"time"
)

const (
	// UDP 包头与 ESP32 固件 app_config.h 保持一致：
	// magic('X','S') + version + type，随后按类型追加小端字段。
	udpMagic0             = 'X'
	udpMagic1             = 'S'
	udpVersion            = 1
	udpTypeHello          = 1
	udpTypeAudio          = 2
	udpTypeVideo          = 3
	udpHelloHeaderSize    = 4
	udpAudioHeaderSize    = 18
	udpVideoHeaderSize    = 26
	udpMaxDatagramSize    = 2048
	udpAuthWindow         = 12 * time.Second
	udpIncompleteFrameTTL = 3 * time.Second
)

type deviceHelloPacket struct {
	// hello JSON 比 DeviceHello 多一个 token 字段，用于刷新媒体包授权窗口。
	DeviceHello
	Token string `json:"token,omitempty"`
}

// videoAssembly 用来跟踪一帧 JPEG 的分片重组过程。
type videoAssembly struct {
	seq         uint32
	timestampMs uint64
	width       int
	height      int
	totalBytes  int
	fragCount   uint16
	fragments   map[uint16][]byte
	firstSeen   time.Time
	lastSeen    time.Time
}

type udpIngestor struct {
	server *server

	mu sync.Mutex
	// 只有最近一次通过 hello/token 校验的 IP 才能继续发媒体包，这样 ESP32
	// 侧就不需要在每个包里都重复带完整鉴权信息。
	authorizedIP     string
	lastAuthorizedAt time.Time
	assemblies       map[uint32]*videoAssembly
}

func (s *server) listenUDP(listenAddr string) {
	// UDP 是 ESP32 主传输通道：hello、音频、视频分片都从同一监听端口进入。
	udpAddr, err := net.ResolveUDPAddr("udp4", listenAddr)
	if err != nil {
		log.Printf("udp resolve failed for %s: %v", listenAddr, err)
		return
	}

	conn, err := net.ListenUDP("udp4", udpAddr)
	if err != nil {
		log.Printf("udp listen failed on %s: %v", listenAddr, err)
		return
	}
	defer conn.Close()
	// 同一个 UDP socket 也用于向设备回写 AI 音频，方便穿透本地路由/NAT 状态。
	s.setDevicePlaybackConn(conn)
	defer s.clearDevicePlaybackConn(conn)
	if err := conn.SetReadBuffer(1 << 20); err != nil {
		log.Printf("udp read buffer resize failed: %v", err)
	}

	log.Printf("udp listening on %s", listenAddr)

	ingestor := &udpIngestor{
		server:     s,
		assemblies: make(map[uint32]*videoAssembly),
	}

	buffer := make([]byte, udpMaxDatagramSize)
	for {
		n, remote, err := conn.ReadFromUDP(buffer)
		if err != nil {
			log.Printf("udp read failed: %v", err)
			continue
		}
		if s.stats != nil {
			s.stats.noteDatagram(n)
		}

		packet := buffer[:n]
		if len(packet) < udpHelloHeaderSize {
			if s.stats != nil {
				s.stats.noteDroppedPacket()
			}
			continue
		}
		if packet[0] != udpMagic0 || packet[1] != udpMagic1 || packet[2] != udpVersion {
			if s.stats != nil {
				s.stats.noteDroppedPacket()
			}
			continue
		}

		switch packet[3] {
		case udpTypeHello:
			ingestor.handleHello(remote, packet)
		case udpTypeAudio:
			ingestor.handleAudio(remote, packet)
		case udpTypeVideo:
			ingestor.handleVideo(remote, packet)
		}
	}
}

func (u *udpIngestor) authorize(remote *net.UDPAddr) {
	u.mu.Lock()
	defer u.mu.Unlock()

	u.authorizedIP = remote.IP.String()
	u.lastAuthorizedAt = time.Now()
}

func (u *udpIngestor) isAuthorized(remote *net.UDPAddr) bool {
	// 如果配置了 DEVICE_TOKEN，只有近期发过合法 hello 的 IP 才能发音视频包。
	if u.server.deviceToken == "" {
		return true
	}

	u.mu.Lock()
	defer u.mu.Unlock()

	return u.authorizedIP == remote.IP.String() && time.Since(u.lastAuthorizedAt) < udpAuthWindow
}

func (u *udpIngestor) cleanupAssembliesLocked(now time.Time) {
	for seq, assembly := range u.assemblies {
		// 丢掉长时间没收齐的半截帧，避免分片丢失或乱序时一直占着内存。
		if now.Sub(assembly.lastSeen) > udpIncompleteFrameTTL {
			delete(u.assemblies, seq)
			u.server.stats.noteIncompleteVideoFrame()
		}
	}
}

func (u *udpIngestor) handleHello(remote *net.UDPAddr, packet []byte) {
	var helloPacket deviceHelloPacket
	if err := json.Unmarshal(packet[udpHelloHeaderSize:], &helloPacket); err != nil {
		log.Printf("udp hello decode failed from %s: %v", remote, err)
		return
	}
	if helloPacket.Type != "hello" {
		return
	}
	if u.server.deviceToken != "" && helloPacket.Token != u.server.deviceToken {
		log.Printf("udp hello token mismatch from %s", remote)
		u.server.stats.noteDroppedPacket()
		return
	}

	// 收到合法 hello 后，刷新这个发送端 IP 的授权窗口。
	u.authorize(remote)
	if helloPacket.Speaker == nil {
		u.server.rememberDevicePlaybackAddr(remote.IP, udpDefaultPlaybackPort)
	} else if helloPacket.Speaker.Enabled {
		playbackPort := udpDefaultPlaybackPort
		if helloPacket.Speaker.AudioPort > 0 && helloPacket.Speaker.AudioPort <= 65535 {
			playbackPort = helloPacket.Speaker.AudioPort
		}
		u.server.rememberDevicePlaybackAddr(remote.IP, playbackPort)
	} else {
		u.server.clearDevicePlaybackAddr()
	}
	hello := helloPacket.DeviceHello
	if hello.DeviceID == "" {
		hello.DeviceID = remote.IP.String()
	}

	u.server.publishHello(&hello)
	log.Printf("udp hello received from %s (%s)", hello.DeviceID, remote)
}

func (u *udpIngestor) handleAudio(remote *net.UDPAddr, packet []byte) {
	// 音频包不分片：固定头 + PCM payload。ESP32 已经做了轻量降噪/AGC。
	if !u.isAuthorized(remote) || len(packet) < udpAudioHeaderSize {
		u.server.stats.noteDroppedPacket()
		return
	}

	payloadLen := int(binary.LittleEndian.Uint16(packet[16:18]))
	if payloadLen <= 0 || udpAudioHeaderSize+payloadLen != len(packet) {
		u.server.stats.noteDroppedPacket()
		return
	}

	payload := append([]byte(nil), packet[udpAudioHeaderSize:]...)
	meta := &PacketMeta{
		Type:          "audio",
		Seq:           uint64(binary.LittleEndian.Uint32(packet[4:8])),
		TimestampMs:   uint64(binary.LittleEndian.Uint32(packet[8:12])),
		Bytes:         len(payload),
		SampleRate:    int(binary.LittleEndian.Uint16(packet[12:14])),
		Channels:      int(packet[14]),
		BitsPerSample: int(packet[15]),
	}

	u.server.publishPacket(meta, payload)
	u.server.stats.noteAudioChunk(meta.Seq, len(payload))
}

func (u *udpIngestor) handleVideo(remote *net.UDPAddr, packet []byte) {
	// 视频包是 JPEG 分片。服务端按 seq 聚合所有 fragment 后再发布完整帧。
	if !u.isAuthorized(remote) || len(packet) < udpVideoHeaderSize {
		u.server.stats.noteDroppedPacket()
		return
	}

	payloadLen := int(binary.LittleEndian.Uint16(packet[24:26]))
	if payloadLen <= 0 || udpVideoHeaderSize+payloadLen != len(packet) {
		u.server.stats.noteDroppedPacket()
		return
	}

	seq := binary.LittleEndian.Uint32(packet[4:8])
	timestampMs := uint64(binary.LittleEndian.Uint32(packet[8:12]))
	width := int(binary.LittleEndian.Uint16(packet[12:14]))
	height := int(binary.LittleEndian.Uint16(packet[14:16]))
	fragIndex := binary.LittleEndian.Uint16(packet[16:18])
	fragCount := binary.LittleEndian.Uint16(packet[18:20])
	totalBytes := int(binary.LittleEndian.Uint32(packet[20:24]))

	if fragCount == 0 || fragIndex >= fragCount || totalBytes <= 0 {
		u.server.stats.noteDroppedPacket()
		return
	}

	now := time.Now()
	payload := append([]byte(nil), packet[udpVideoHeaderSize:]...)
	u.server.stats.noteVideoFragment()

	u.mu.Lock()
	defer u.mu.Unlock()

	u.cleanupAssembliesLocked(now)

	assembly, exists := u.assemblies[seq]
	if !exists || assembly.fragCount != fragCount || assembly.totalBytes != totalBytes {
		assembly = &videoAssembly{
			seq:         seq,
			timestampMs: timestampMs,
			width:       width,
			height:      height,
			totalBytes:  totalBytes,
			fragCount:   fragCount,
			fragments:   make(map[uint16][]byte, fragCount),
			firstSeen:   now,
		}
		u.assemblies[seq] = assembly
	}

	assembly.lastSeen = now
	if _, seen := assembly.fragments[fragIndex]; seen {
		// 重复分片可能来自 Wi-Fi 重传或设备侧重复发送，统计后忽略。
		u.server.stats.noteVideoDuplicateFragment()
		return
	}
	assembly.fragments[fragIndex] = payload

	if len(assembly.fragments) != int(assembly.fragCount) {
		return
	}

	// 只有所有分片都到齐后，才真正重组出完整 JPEG。
	frame := make([]byte, 0, assembly.totalBytes)
	for index := uint16(0); index < assembly.fragCount; index++ {
		fragment, ok := assembly.fragments[index]
		if !ok {
			return
		}
		frame = append(frame, fragment...)
	}
	delete(u.assemblies, seq)

	if len(frame) > assembly.totalBytes {
		frame = frame[:assembly.totalBytes]
	}

	meta := &PacketMeta{
		Type:        "video",
		Seq:         uint64(seq),
		TimestampMs: assembly.timestampMs,
		Bytes:       len(frame),
		Width:       assembly.width,
		Height:      assembly.height,
		Format:      "jpeg",
	}

	u.server.publishPacket(meta, frame)
	u.server.stats.noteVideoFrame(meta.Seq, len(frame))
}
