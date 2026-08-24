package serverapp

import (
	"encoding/binary"
	"os"
	"path/filepath"
	"testing"
)

func TestNormalizeNavigationVoiceText(t *testing.T) {
	got := normalizeNavigationVoiceText("[导航] 保持直行。")
	want := normalizeNavigationVoiceText("保持直行")
	if got != want {
		t.Fatalf("normalized text = %q, want %q", got, want)
	}
}

func TestNavigationVoiceAliases(t *testing.T) {
	entries := map[string]navigationVoiceEntry{
		normalizeNavigationVoiceText("切换到盲道导航。"): {Files: []string{"nav.wav"}},
		normalizeNavigationVoiceText("已停止导航。"):   {Files: []string{"stop.wav"}},
	}
	addNavigationVoiceAliases(entries)

	if entry, ok := entries[normalizeNavigationVoiceText("盲道导航已启动")]; !ok || entry.Files[0] != "nav.wav" {
		t.Fatalf("missing blind navigation alias: %#v", entry)
	}
	if entry, ok := entries[normalizeNavigationVoiceText("导航已停止")]; !ok || entry.Files[0] != "stop.wav" {
		t.Fatalf("missing stop navigation alias: %#v", entry)
	}
}

func TestReadPCM16WavWithListChunk(t *testing.T) {
	path := filepath.Join(t.TempDir(), "voice.wav")
	payload := []byte{0x01, 0x00, 0xff, 0xff}
	if err := os.WriteFile(path, makeTestWav(16000, 1, payload), 0o600); err != nil {
		t.Fatal(err)
	}

	wav, err := readPCM16Wav(path)
	if err != nil {
		t.Fatal(err)
	}
	if wav.sampleRate != 16000 || wav.channels != 1 || wav.bitsPerSample != 16 {
		t.Fatalf("unexpected wav format: %#v", wav)
	}
	if string(wav.payload) != string(payload) {
		t.Fatalf("payload = %v, want %v", wav.payload, payload)
	}
}

func makeTestWav(sampleRate, channels int, payload []byte) []byte {
	const bitsPerSample = 16
	fmtChunk := make([]byte, 16)
	binary.LittleEndian.PutUint16(fmtChunk[0:2], 1)
	binary.LittleEndian.PutUint16(fmtChunk[2:4], uint16(channels))
	binary.LittleEndian.PutUint32(fmtChunk[4:8], uint32(sampleRate))
	byteRate := sampleRate * channels * bitsPerSample / 8
	binary.LittleEndian.PutUint32(fmtChunk[8:12], uint32(byteRate))
	binary.LittleEndian.PutUint16(fmtChunk[12:14], uint16(channels*bitsPerSample/8))
	binary.LittleEndian.PutUint16(fmtChunk[14:16], bitsPerSample)

	listChunk := []byte("INFO")
	riffSize := 4 + 8 + len(fmtChunk) + 8 + len(listChunk) + 8 + len(payload)
	out := make([]byte, 12, 8+riffSize)
	copy(out[0:4], "RIFF")
	binary.LittleEndian.PutUint32(out[4:8], uint32(riffSize))
	copy(out[8:12], "WAVE")
	out = appendWavChunk(out, "fmt ", fmtChunk)
	out = appendWavChunk(out, "LIST", listChunk)
	out = appendWavChunk(out, "data", payload)
	return out
}

func appendWavChunk(out []byte, id string, payload []byte) []byte {
	out = append(out, []byte(id)...)
	size := make([]byte, 4)
	binary.LittleEndian.PutUint32(size, uint32(len(payload)))
	out = append(out, size...)
	out = append(out, payload...)
	if len(payload)%2 == 1 {
		out = append(out, 0)
	}
	return out
}
