package serverapp

import (
	"encoding/binary"
	"math"
	"net/http"
	"strconv"

	"github.com/gin-gonic/gin"
)

const (
	speakerTestSampleRate = 24000
	speakerTestChannels   = 1
	speakerTestBits       = 16
)

func (s *server) handleSpeakerTest(c *gin.Context) {
	_, target := s.snapshotDevicePlaybackTarget()
	if target == nil {
		c.JSON(http.StatusServiceUnavailable, gin.H{
			"ok":    false,
			"error": "device playback target is not ready; wait for a fresh ESP32 hello packet",
		})
		return
	}

	durationMs := boundedIntQuery(c, "durationMs", 700, 100, 3000)
	frequencyHz := boundedIntQuery(c, "freq", 880, 120, 3000)
	payload := makeSpeakerTestTone(durationMs, frequencyHz)
	chunk := devicePlaybackChunk{
		sampleRate:    speakerTestSampleRate,
		channels:      speakerTestChannels,
		bitsPerSample: speakerTestBits,
		payload:       payload,
	}

	s.clearDevicePlaybackQueue()
	result, err := s.sendAIAudioToDeviceNow(chunk)
	if err != nil {
		c.JSON(http.StatusBadGateway, gin.H{
			"ok":            false,
			"error":         err.Error(),
			"target":        result.Target,
			"datagramsSent": result.DatagramsSent,
			"bytesSent":     result.BytesSent,
		})
		return
	}

	c.JSON(http.StatusOK, gin.H{
		"ok":            true,
		"target":        result.Target,
		"durationMs":    durationMs,
		"frequencyHz":   frequencyHz,
		"payloadBytes":  len(payload),
		"datagramsSent": result.DatagramsSent,
		"bytesSent":     result.BytesSent,
	})
}

func boundedIntQuery(c *gin.Context, name string, fallback, minValue, maxValue int) int {
	value, err := strconv.Atoi(c.Query(name))
	if err != nil {
		return fallback
	}
	if value < minValue {
		return minValue
	}
	if value > maxValue {
		return maxValue
	}
	return value
}

func makeSpeakerTestTone(durationMs, frequencyHz int) []byte {
	sampleCount := speakerTestSampleRate * durationMs / 1000
	payload := make([]byte, sampleCount*2)
	const amplitude = 12000

	for i := 0; i < sampleCount; i++ {
		phase := 2 * math.Pi * float64(frequencyHz) * float64(i) / speakerTestSampleRate
		envelope := 1.0
		fadeSamples := speakerTestSampleRate / 100
		if i < fadeSamples {
			envelope = float64(i) / float64(fadeSamples)
		} else if remaining := sampleCount - i - 1; remaining < fadeSamples {
			envelope = float64(remaining) / float64(fadeSamples)
		}
		sample := int16(math.Sin(phase) * amplitude * envelope)
		binary.LittleEndian.PutUint16(payload[i*2:], uint16(sample))
	}

	return payload
}
