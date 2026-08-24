package serverapp

import "testing"

func TestSkillResponseSuppressionBindsNextResponse(t *testing.T) {
	var bridge dashScopeBridge

	bridge.beginSkillResponseSuppression()
	bridge.markResponseStarted("resp_1")

	if !bridge.shouldSuppressAssistantOutput("resp_1") {
		t.Fatal("expected response resp_1 to be suppressed")
	}
	if !bridge.shouldSuppressAssistantOutput("") {
		t.Fatal("expected output without response id to be suppressed while bound response is active")
	}
	if bridge.shouldSuppressAssistantOutput("resp_2") {
		t.Fatal("did not expect unrelated response resp_2 to be suppressed")
	}

	bridge.finishSkillResponseSuppression("resp_1")
	if bridge.shouldSuppressAssistantOutput("resp_1") {
		t.Fatal("did not expect suppression after response finished")
	}
}

func TestSkillResponseSuppressionUsesActiveResponse(t *testing.T) {
	var bridge dashScopeBridge

	bridge.markResponseStarted("active_resp")
	bridge.beginSkillResponseSuppression()

	if !bridge.shouldSuppressAssistantOutput("active_resp") {
		t.Fatal("expected active response to be suppressed")
	}
	if bridge.shouldSuppressAssistantOutput("other_resp") {
		t.Fatal("did not expect another response to be suppressed")
	}
}
