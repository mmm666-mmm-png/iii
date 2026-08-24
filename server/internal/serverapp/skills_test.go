package serverapp

import "testing"

func TestSkillIntentMatching(t *testing.T) {
	registry := &skillRegistry{}

	tests := []struct {
		name      string
		text      string
		wantSkill string
		wantArg   string
	}{
		{
			name:      "weather city before keyword",
			text:      "帮我查一下北京天气",
			wantSkill: "get_current_weather",
			wantArg:   "北京",
		},
		{
			name:      "weather rain intent",
			text:      "上海今天会下雨吗",
			wantSkill: "get_current_weather",
			wantArg:   "上海",
		},
		{
			name:      "weather colloquial check",
			text:      "帮我查下潍坊天气",
			wantSkill: "get_current_weather",
			wantArg:   "潍坊",
		},
		{
			name:      "weather leading xia residue",
			text:      "下潍坊气温怎么样",
			wantSkill: "get_current_weather",
			wantArg:   "潍坊",
		},
		{
			name:      "time intent",
			text:      "现在几点",
			wantSkill: "get_current_time",
			wantArg:   "Asia/Shanghai",
		},
		{
			name:      "device status intent",
			text:      "设备现在在线状态怎么样",
			wantSkill: "get_device_status",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			intent, ok := registry.matchIntent(tt.text)
			if !ok {
				t.Fatalf("expected intent for %q", tt.text)
			}
			if intent.Name != tt.wantSkill {
				t.Fatalf("intent.Name = %q, want %q", intent.Name, tt.wantSkill)
			}
			if tt.wantArg == "" {
				return
			}
			if location, ok := intent.Args["location"].(string); ok && location == tt.wantArg {
				return
			}
			if timezone, ok := intent.Args["timezone"].(string); ok && timezone == tt.wantArg {
				return
			}
			t.Fatalf("intent args = %#v, want %q", intent.Args, tt.wantArg)
		})
	}
}

func TestCleanLocationText(t *testing.T) {
	tests := []struct {
		input string
		want  string
	}{
		{input: "查下潍坊", want: "潍坊"},
		{input: "帮我查下潍坊", want: "潍坊"},
		{input: "下潍坊", want: "潍坊"},
	}

	for _, tt := range tests {
		t.Run(tt.input, func(t *testing.T) {
			if got := cleanLocationText(tt.input); got != tt.want {
				t.Fatalf("cleanLocationText(%q) = %q, want %q", tt.input, got, tt.want)
			}
		})
	}
}

func TestSkillIntentNoMatch(t *testing.T) {
	registry := &skillRegistry{}
	if _, ok := registry.matchIntent("帮我看一下画面里有什么"); ok {
		t.Fatal("unexpected skill intent")
	}
}

func TestLocalWeatherLocation(t *testing.T) {
	tests := []struct {
		name string
		want string
	}{
		{name: "安丘", want: "安丘"},
		{name: "安丘市", want: "安丘"},
		{name: "山东省潍坊市", want: "潍坊"},
		{name: "weifang", want: "潍坊"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			location, ok := localWeatherLocation(tt.name)
			if !ok {
				t.Fatalf("expected local weather location for %q", tt.name)
			}
			if location.Name != tt.want {
				t.Fatalf("location.Name = %q, want %q", location.Name, tt.want)
			}
			if location.Latitude == 0 || location.Longitude == 0 {
				t.Fatalf("location coordinates are empty: %#v", location)
			}
		})
	}
}
