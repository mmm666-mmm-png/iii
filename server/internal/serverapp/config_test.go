package serverapp

import "testing"

func TestParseEnvLine(t *testing.T) {
	tests := []struct {
		line      string
		wantKey   string
		wantValue string
		wantOK    bool
	}{
		{line: "SERVER_HTTP_ADDR=:8888", wantKey: "SERVER_HTTP_ADDR", wantValue: ":8888", wantOK: true},
		{line: "export DEVICE_TOKEN=123456", wantKey: "DEVICE_TOKEN", wantValue: "123456", wantOK: true},
		{line: `DASHSCOPE_MODEL="qwen3-omni-flash-realtime"`, wantKey: "DASHSCOPE_MODEL", wantValue: "qwen3-omni-flash-realtime", wantOK: true},
		{line: "VISION_WORKER_URL=http://127.0.0.1:18082 # local", wantKey: "VISION_WORKER_URL", wantValue: "http://127.0.0.1:18082", wantOK: true},
		{line: "# comment", wantOK: false},
	}

	for _, tt := range tests {
		t.Run(tt.line, func(t *testing.T) {
			key, value, ok := parseEnvLine(tt.line)
			if ok != tt.wantOK {
				t.Fatalf("ok = %v, want %v", ok, tt.wantOK)
			}
			if key != tt.wantKey || value != tt.wantValue {
				t.Fatalf("got %q=%q, want %q=%q", key, value, tt.wantKey, tt.wantValue)
			}
		})
	}
}

func TestEnvBool(t *testing.T) {
	t.Setenv("BOOL_TRUE", "1")
	t.Setenv("BOOL_FALSE", "off")

	if !envBool("BOOL_TRUE", false) {
		t.Fatal("expected true")
	}
	if envBool("BOOL_FALSE", true) {
		t.Fatal("expected false")
	}
	if !envBool("BOOL_MISSING", true) {
		t.Fatal("expected fallback")
	}
}
