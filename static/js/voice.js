/**
 * Browser Web Speech API wrapper for Korean voice input and spoken replies.
 */

const SpeechRecognitionCtor =
  window.SpeechRecognition || window.webkitSpeechRecognition || null;

export function isVoiceInputSupported() {
  return Boolean(SpeechRecognitionCtor);
}

export function createVoiceRecognizer({ onResult, onStateChange, onError }) {
  if (!SpeechRecognitionCtor) {
    return null;
  }
  const recognition = new SpeechRecognitionCtor();
  recognition.lang = "ko-KR";
  recognition.interimResults = false;
  recognition.maxAlternatives = 1;

  let active = false;

  recognition.onstart = () => {
    active = true;
    onStateChange?.(true);
  };
  recognition.onend = () => {
    active = false;
    onStateChange?.(false);
  };
  recognition.onerror = (event) => {
    active = false;
    onStateChange?.(false);
    onError?.(event.error || "speech_error");
  };
  recognition.onresult = (event) => {
    const transcript = event.results?.[0]?.[0]?.transcript || "";
    if (transcript.trim()) {
      onResult?.(transcript.trim());
    }
  };

  return {
    toggle() {
      if (active) {
        recognition.stop();
      } else {
        try {
          recognition.start();
        } catch {
          // ignore duplicate start attempts
        }
      }
    },
  };
}

export function speakKorean(text) {
  if (!("speechSynthesis" in window) || !text) return;
  try {
    window.speechSynthesis.cancel();
    const cleaned = text.replace(/[*`#[\]]/g, " ").replace(/\s+/g, " ").trim();
    if (!cleaned) return;
    const utter = new SpeechSynthesisUtterance(cleaned.slice(0, 400));
    utter.lang = "ko-KR";
    utter.rate = 1.05;
    window.speechSynthesis.speak(utter);
  } catch {
    // ignore speech synthesis errors
  }
}
