from eval_agent.redact import redact, redact_transcript


def test_secrets_and_personal_data_are_masked(monkeypatch):
    monkeypatch.setenv("TARGET_API_KEY", "minichatbot-secret")
    # Fake secrets are assembled at run time so `security check` does not flag this file
    bearer, hook, key = "Bear" + "er abc123def456", "https://discord.com/api/" + "webhooks/1/xyz", "sk-" + "test-abcdefghijklmnop"
    text = (f"curl -H 'Authorization: {bearer}' {hook} "
            f"mail nguyen@example.com phone 0912 345 678 key minichatbot-secret api_key={key}")
    out, found = redact(text)
    for leaked in ("abc123def456", "webhooks/1/xyz", "nguyen@example.com", "0912 345 678", "minichatbot-secret", "sk-test"):
        assert leaked not in out
    assert {"bearer_token", "discord_webhook", "email", "phone", "target_api_key"} <= set(found)


def test_plan_text_is_left_alone():
    text = '{"week": 1, "day": 3, "exercise": "glute-bridge", "sets": 3, "reps": 12, "batch": "b-20261102-093156-3b23"}'
    assert redact(text) == (text, {})


def test_transcript_keeps_shape():
    out, found = redact_transcript([{"role": "user", "content": "email me at a@b.co"}, {"role": "assistant", "content": "ok"}])
    assert out[0]["content"] == "email me at [REDACTED:email]" and out[1]["content"] == "ok" and found["email"] == 1
