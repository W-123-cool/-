#!/usr/bin/env bash
# Voice nav playback setup: ES8316 3.5mm + Sherpa Matcha TTS env vars
# Usage: source voice_nav_audio.sh && voice_nav_setup_playback

voice_nav_setup_playback() {
  local bundle model vocoder card sink

  bundle="${SHERPA_BUNDLE:-${HOME}/Desktop/rk3588-offline-bundle}"
  model="${SHERPA_TTS_MODEL:-${bundle}/model/matcha-icefall-zh-baker}"
  vocoder="${SHERPA_TTS_VOCODER:-${bundle}/model/vocos-22khz-univ.onnx}"

  export SHERPA_TTS_MODEL="${model}"
  export SHERPA_TTS_VOCODER="${vocoder}"
  export VOICE_NAV_TTS_BACKEND="${VOICE_NAV_TTS_BACKEND:-sherpa}"
  export VOICE_NAV_TTS="${VOICE_NAV_TTS:-1}"
  export VOICE_MERGE_SEC="${VOICE_MERGE_SEC:-7.0}"
  export VOICE_EP_RULE1="${VOICE_EP_RULE1:-2.5}"
  export VOICE_EP_RULE2="${VOICE_EP_RULE2:-1.8}"
  export VOICE_EP_RULE3="${VOICE_EP_RULE3:-0.5}"
  export VOICE_NAV_TTS_SYNC="${VOICE_NAV_TTS_SYNC:-1}"

  card="${VOICE_NAV_ALSA_CARD:-}"
  if [[ -z "${card}" ]]; then
    card="$(aplay -l 2>/dev/null | grep -i es8316 | sed -n 's/^card \([0-9]*\):.*/\1/p' | head -1)"
  fi
  if [[ -z "${card}" ]]; then
    card="$(arecord -l 2>/dev/null | grep -i es8316 | sed -n 's/^card \([0-9]*\):.*/\1/p' | head -1)"
  fi

  if [[ -n "${card}" ]] && command -v amixer >/dev/null 2>&1; then
    amixer -c "${card}" set 'DAC' 192 unmute >/dev/null 2>&1 || true
    amixer -c "${card}" set 'HP' 4 unmute >/dev/null 2>&1 || true
    amixer -c "${card}" set 'Left DAC' on >/dev/null 2>&1 || true
    amixer -c "${card}" set 'Right DAC' on >/dev/null 2>&1 || true
  fi

  if command -v pactl >/dev/null 2>&1; then
    if ! pactl info >/dev/null 2>&1; then
      pulseaudio --start >/dev/null 2>&1 || true
      sleep 1
    fi
    sink="${VOICE_NAV_PULSE_SINK:-}"
    if [[ -z "${sink}" ]]; then
      # RockPi names vary: stereo-fallback vs multichannel-output
      sink="$(pactl list short sinks 2>/dev/null | awk '/es8316/ {print $2; exit}')"
    fi
    if [[ -n "${sink}" ]]; then
      export VOICE_NAV_PULSE_SINK="${sink}"
      pactl suspend-sink "${sink}" 0 2>/dev/null || true
      pactl set-default-sink "${sink}" 2>/dev/null || true
      pactl set-sink-volume "${sink}" 100% 2>/dev/null || true
      pactl set-sink-mute "${sink}" 0 2>/dev/null || true
    else
      unset VOICE_NAV_PULSE_SINK 2>/dev/null || true
      export VOICE_NAV_PULSE_SINK=""
    fi
  fi

  if [[ -n "${card}" ]]; then
    export VOICE_NAV_ALSA_DEVICE="${VOICE_NAV_ALSA_DEVICE:-plughw:${card},0}"
    if [[ -z "${VOICE_NAV_PULSE_SINK:-}" ]]; then
      echo "  [info] no es8316 Pulse sink (HDMI-only?) -> ALSA ${VOICE_NAV_ALSA_DEVICE}"
    fi
  fi

  if [[ -f "${model}/model-steps-3.onnx" && -f "${vocoder}" ]]; then
    echo "  TTS: Sherpa Matcha"
    echo "       model: ${model}"
  else
    echo "  [warn] Sherpa TTS model missing, fallback espeak-ng"
    export VOICE_NAV_TTS_BACKEND="auto"
  fi

  if [[ -n "${VOICE_NAV_PULSE_SINK:-}" ]]; then
    echo "  output: PulseAudio -> ${VOICE_NAV_PULSE_SINK}"
  elif [[ -n "${VOICE_NAV_ALSA_DEVICE:-}" ]]; then
    echo "  output: ALSA -> ${VOICE_NAV_ALSA_DEVICE}"
  else
    echo "  [warn] no es8316 output detected, TTS may be silent"
  fi
}
