(() => {
  const preview = document.getElementById("voice-preview");
  const button = document.getElementById("voice-preview-button");
  if (!preview || !button) return;

  const audio = document.getElementById("voice-preview-audio");
  const status = document.getElementById("voice-preview-status");
  let audioUrl = null;

  button.addEventListener("click", async () => {
    button.disabled = true;
    button.textContent = "Создаю пример…";
    status.textContent = "";
    audio.hidden = true;
    const body = new FormData();
    body.append("csrf_token", preview.dataset.csrfToken);
    body.append("voice", document.getElementById("yandex_voice").value);
    body.append("emotion", document.getElementById("yandex_voice_emotion").value);
    body.append("speed", document.getElementById("yandex_voice_speed").value);
    try {
      const response = await fetch(preview.dataset.previewUrl, {method: "POST", body});
      if (!response.ok) {
        let message = "Не удалось создать пример голоса.";
        try {
          const payload = await response.json();
          message = payload.error || payload.detail || message;
        } catch (_) {}
        throw new Error(message);
      }
      if (audioUrl) URL.revokeObjectURL(audioUrl);
      audioUrl = URL.createObjectURL(await response.blob());
      audio.src = audioUrl;
      audio.hidden = false;
      status.textContent = "Готово. Можно прослушать ещё раз в плеере.";
      try { await audio.play(); } catch (_) {}
    } catch (error) {
      status.textContent = error.message;
    } finally {
      button.disabled = false;
      button.textContent = "Послушать";
    }
  });
})();
