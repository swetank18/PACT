/**
 * Two sounds, synthesised. A rise when a check passes, a thud when one fails.
 *
 * WebAudio rather than audio files so nothing has to load — a demo laptop that
 * 404s a sample mid-replay is worse than silence. Off by default; the feed
 * header and the settings page both control the same preference.
 */
let ctx: AudioContext | null = null;

function audio(): AudioContext | null {
  try {
    const Ctor =
      window.AudioContext ??
      (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!Ctor) return null;
    ctx ??= new Ctor();
    void ctx.resume();
    return ctx;
  } catch {
    return null;
  }
}

function tone(freq: number, ms: number, type: OscillatorType, gain: number) {
  const ac = audio();
  if (!ac) return;
  const osc = ac.createOscillator();
  const vol = ac.createGain();
  osc.type = type;
  osc.frequency.value = freq;
  vol.gain.setValueAtTime(gain, ac.currentTime);
  vol.gain.exponentialRampToValueAtTime(0.0001, ac.currentTime + ms / 1000);
  osc.connect(vol).connect(ac.destination);
  osc.start();
  osc.stop(ac.currentTime + ms / 1000);
}

export const ding = () => tone(1180, 90, "sine", 0.05);
export const thud = () => tone(110, 260, "triangle", 0.14);
