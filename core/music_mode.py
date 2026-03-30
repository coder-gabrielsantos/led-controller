import threading
import time
import warnings
import logging

import numpy as np

# Compat: soundcard 0.4.x usa numpy.fromstring em modo binário no Windows.
# Em numpy 2.x isso lança ValueError; redirecionamos para frombuffer.
_ORIG_FROMSTRING = np.fromstring


def _fromstring_compat(*args, **kwargs):
    sep = kwargs.get("sep", args[3] if len(args) > 3 else "")
    if sep == "":
        src = kwargs.get("string", args[0] if args else None)
        dtype = kwargs.get("dtype", float)
        count = kwargs.get("count", -1)
        try:
            return np.frombuffer(src, dtype=dtype, count=count)
        except Exception:
            pass
    return _ORIG_FROMSTRING(*args, **kwargs)


np.fromstring = _fromstring_compat
import soundcard as sc

# Evita flood de warnings conhecidos do loopback no backend MediaFoundation.
if hasattr(sc, "SoundcardRuntimeWarning"):
    warnings.filterwarnings(
        "ignore",
        message="data discontinuity in recording",
        category=sc.SoundcardRuntimeWarning,
    )


class MusicMode:
    def __init__(self, arduino):
        self.arduino = arduino
        self.running = False
        self.ui_callback = None
        self.num_leds = 88
        self._thread = None

        self.sample_rate = 48000
        # Menor bloco = menor latência visual (mais responsivo às batidas).
        self.block_size = 1024
        self.serial_fps = 35
        self.ui_fps = 24
        self._next_serial_ts = 0.0
        self._next_ui_ts = 0.0
        self._red_hold_until = 0.0
        self._window = np.hanning(self.block_size)
        self._band_bin_ranges = self._build_band_ranges()
        self._band_weight = np.linspace(1.25, 0.95, self.num_leds, dtype=np.float32)
        self._band_levels = np.zeros(self.num_leds, dtype=np.float32)
        self._pixels = [[0.0, 0.0, 0.0] for _ in range(self.num_leds)]

        self._smoothed = np.zeros(self.num_leds, dtype=np.float32)
        self._dynamic_peak = 1.0
        self._loudness_ema = 0.08
        self._floor_ema = 0.0
        self._prev_peak = 0.0
        self._onset_ema = 0.0
        self._global_level = 0.0
        self._bass_ema = 0.0
        self._beat_pulse = 0.0
        self._prev_above_floor = np.zeros(self.num_leds, dtype=np.float32)
        self._energy_ema = 0.0

    def set_callback(self, callback_func):
        self.ui_callback = callback_func

    def _build_band_ranges(self):
        """Cria bandas logarítmicas para mapear o espectro em 88 LEDs."""
        freqs = np.fft.rfftfreq(self.block_size, d=1.0 / self.sample_rate)
        edges = np.geomspace(30.0, 12000.0, self.num_leds + 1)
        ranges = []
        for i in range(self.num_leds):
            start = int(np.searchsorted(freqs, edges[i], side="left"))
            end = int(np.searchsorted(freqs, edges[i + 1], side="left"))
            end = max(end, start + 1)
            ranges.append((start, end))
        return ranges

    @staticmethod
    def _hsv_to_rgb(h, s, v):
        i = int(h * 6.0)
        f = (h * 6.0) - i
        p = v * (1.0 - s)
        q = v * (1.0 - s * f)
        t = v * (1.0 - s * (1.0 - f))
        i %= 6
        if i == 0:
            return [v, t, p]
        if i == 1:
            return [q, v, p]
        if i == 2:
            return [p, v, t]
        if i == 3:
            return [p, q, v]
        if i == 4:
            return [t, p, v]
        return [v, p, q]

    @staticmethod
    def _lerp(a, b, t):
        return a + (b - a) * t

    @classmethod
    def _lerp_color(cls, c1, c2, t):
        return [
            cls._lerp(c1[0], c2[0], t),
            cls._lerp(c1[1], c2[1], t),
            cls._lerp(c1[2], c2[2], t),
        ]

    @classmethod
    def _tri_color(cls, c1, c2, c3, t):
        t = float(np.clip(t, 0.0, 1.0))
        if t < 0.5:
            return cls._lerp_color(c1, c2, t * 2.0)
        return cls._lerp_color(c2, c3, (t - 0.5) * 2.0)

    def _get_loopback_mic(self):
        speaker = sc.default_speaker()
        if speaker is None:
            return None
        return sc.get_microphone(id=str(speaker.name), include_loopback=True)

    def start(self):
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self._render_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self.running = False
        current = threading.current_thread()
        if self._thread and self._thread.is_alive() and self._thread is not current:
            self._thread.join(timeout=0.3)
        self._thread = None
        self._smoothed.fill(0.0)
        self._dynamic_peak = 1.0
        self._loudness_ema = 0.08
        self._floor_ema = 0.0
        self._prev_peak = 0.0
        self._onset_ema = 0.0
        self._global_level = 0.0
        self._bass_ema = 0.0
        self._beat_pulse = 0.0
        self._prev_above_floor.fill(0.0)
        self._energy_ema = 0.0
        self._next_serial_ts = 0.0
        self._next_ui_ts = 0.0
        self._red_hold_until = 0.0

    def _render_loop(self):
        try:
            loopback_mic = self._get_loopback_mic()
        except Exception as e:
            logging.exception("Music Mode: falha ao acessar loopback de áudio")
            return

        if loopback_mic is None:
            logging.warning("Music Mode: nenhum dispositivo de saída/loopback encontrado.")
            return

        try:
            with loopback_mic.recorder(samplerate=self.sample_rate, channels=2) as recorder:
                while self.running:
                    audio = recorder.record(numframes=self.block_size)
                    if audio is None or len(audio) == 0:
                        time.sleep(0.01)
                        continue

                    mono = np.mean(audio, axis=1).astype(np.float32)
                    if mono.shape[0] < self.block_size:
                        mono = np.pad(mono, (0, self.block_size - mono.shape[0]))
                    elif mono.shape[0] > self.block_size:
                        mono = mono[: self.block_size]

                    spectrum = np.abs(np.fft.rfft(mono * self._window))

                    band_levels = self._band_levels
                    band_levels.fill(0.0)
                    for i, (start, end) in enumerate(self._band_bin_ranges):
                        band_slice = spectrum[start:end]
                        if band_slice.size > 0:
                            band_levels[i] = float(np.mean(band_slice))

                    # Dinâmica adaptativa: responde bem em áudio calmo e também em partes muito fortes.
                    band_levels = np.log1p(band_levels)
                    frame_loudness = float(np.mean(band_levels))
                    self._loudness_ema = self._loudness_ema * 0.92 + frame_loudness * 0.08

                    # AGC mais conservador para não "achatar" tudo em nível alto.
                    target_loudness = 0.11
                    auto_gain = np.clip(target_loudness / (self._loudness_ema + 1e-4), 0.45, 1.9)
                    compressed = np.power(np.clip(band_levels * auto_gain, 0.0, None), 0.72)

                    # Realça um pouco os graves para dar mais "pancada" visual.
                    weighted = compressed * self._band_weight

                    frame_floor = float(np.percentile(weighted, 12))
                    self._floor_ema = self._floor_ema * 0.96 + frame_floor * 0.04
                    above_floor = np.clip(weighted - self._floor_ema, 0.0, None)

                    frame_peak = max(1e-4, float(np.max(above_floor)))
                    self._dynamic_peak = max(frame_peak, self._dynamic_peak * 0.96)
                    normalized = np.clip(above_floor / (self._dynamic_peak + 1e-6), 0.0, 1.0)

                    # Boost para transientes e batidas (especialmente graves).
                    onset = max(0.0, frame_peak - self._prev_peak)
                    self._prev_peak = frame_peak
                    self._onset_ema = self._onset_ema * 0.85 + onset * 0.15
                    spectral_flux = float(
                        np.mean(np.clip(above_floor - self._prev_above_floor, 0.0, None))
                    )
                    np.copyto(self._prev_above_floor, above_floor)

                    bass_bins = max(6, int(self.num_leds * 0.22))
                    bass_energy = float(np.mean(normalized[:bass_bins]))
                    self._bass_ema = self._bass_ema * 0.90 + bass_energy * 0.10
                    beat_delta = max(0.0, bass_energy - (self._bass_ema * 1.08))
                    self._beat_pulse = max(self._beat_pulse * 0.80, beat_delta * 4.8)

                    transient_raw = self._onset_ema * 2.2 + spectral_flux * 3.1 + self._beat_pulse * 1.5
                    transient_boost = np.clip(transient_raw, 0.0, 0.85)
                    normalized = np.clip(normalized * (1.0 + transient_boost), 0.0, 1.0)

                    # Resposta mais rápida para bater junto com kick/snare.
                    attack = 0.90
                    release = 0.72
                    rising = normalized > self._smoothed
                    self._smoothed[rising] = (
                        self._smoothed[rising] * (1.0 - attack) + normalized[rising] * attack
                    )
                    self._smoothed[~rising] = (
                        self._smoothed[~rising] * release + normalized[~rising] * (1.0 - release)
                    )

                    # Intensidade global dita quantos LEDs acendem do centro para as pontas.
                    raw_energy = float(
                        np.mean(normalized) * 0.50
                        + np.max(normalized) * 0.35
                        + bass_energy * 0.15
                    )
                    raw_energy = np.clip(
                        raw_energy * (1.0 + transient_boost * 1.15 + self._beat_pulse * 0.8), 0.0, 1.0
                    )

                    # Estratégia nova:
                    # 1) Noise gate para evitar muitos LEDs em músicas calmas.
                    # 2) Curva exponencial para expansão só em energia realmente alta.
                    self._energy_ema = self._energy_ema * 0.95 + raw_energy * 0.05
                    gate_open = raw_energy > 0.32 or transient_boost > 0.30 or self._beat_pulse > 0.22
                    if gate_open:
                        gated = np.clip((raw_energy - 0.30) / 0.70, 0.0, 1.0)
                    else:
                        # Fechado: deixa um rastro mínimo no centro, mas quase apagado.
                        gated = np.clip((raw_energy - 0.36) / 1.20, 0.0, 0.03)

                    frame_energy = float(np.clip(gated ** 3.0, 0.0, 1.0))
                    frame_energy = np.clip(
                        frame_energy + transient_boost * 0.12 + self._beat_pulse * 0.10, 0.0, 1.0
                    )

                    if frame_energy > self._global_level:
                        self._global_level = self._global_level * 0.22 + frame_energy * 0.78
                    else:
                        self._global_level = self._global_level * 0.93 + frame_energy * 0.07

                    heat = float(
                        np.clip(
                            frame_energy * 0.42
                            + transient_boost * 1.25
                            + self._beat_pulse * 0.95,
                            0.0,
                            1.0,
                        )
                    )
                    max_dist = (self.num_leds - 1) / 2.0
                    center = max_dist
                    # Mantém centro compacto por padrão, abrindo para as pontas somente em alta intensidade.
                    active_dist = max_dist * (0.005 + 0.995 * (self._global_level ** 2.5))

                    # Hard cap progressivo: limita quantidade de LEDs até a música ficar realmente intensa.
                    if self._global_level < 0.38:
                        cap_fraction = 0.12
                    elif self._global_level < 0.52:
                        cap_fraction = 0.20
                    elif self._global_level < 0.66:
                        cap_fraction = 0.32
                    elif self._global_level < 0.80:
                        cap_fraction = 0.48
                    else:
                        cap_fraction = 1.0
                    active_dist = min(active_dist, max_dist * cap_fraction)

                    # Nova direção visual: batida leve = azul; música agitada = vermelho puro.
                    agitation = float(
                        np.clip(
                            frame_energy * 0.45 + transient_boost * 0.95 + self._beat_pulse * 1.25,
                            0.0,
                            1.0,
                        )
                    )
                    now = time.perf_counter()
                    # Segura vermelho por alguns ms em batidas fortes para "punch" visual.
                    if transient_boost > 0.62 or self._beat_pulse > 0.52:
                        self._red_hold_until = max(self._red_hold_until, now + 0.12)
                    hold_mix = 0.0
                    if now < self._red_hold_until:
                        hold_mix = min(1.0, (self._red_hold_until - now) / 0.12)

                    low_color = [0.0, 95.0, 255.0]   # azul para batidas menos intensas
                    high_color = [255.0, 0.0, 0.0]   # vermelho puro para alta agitação

                    pixels = self._pixels
                    for i in range(self.num_leds):
                        dist = abs(i - center)
                        if dist > active_dist:
                            pixels[i][0] = 0.0
                            pixels[i][1] = 0.0
                            pixels[i][2] = 0.0
                            continue

                        pos = dist / max(1e-6, active_dist)
                        beam_falloff = (1.0 - pos) ** 0.72

                        # Textura do feixe baseada no conteúdo espectral, mantendo simetria.
                        band_idx = int(pos * (self.num_leds - 1))
                        band_idx = max(0, min(self.num_leds - 1, band_idx))
                        detail = float(self._smoothed[band_idx])
                        envelope = np.clip((self._global_level - 0.02) / 0.98, 0.0, 1.0) ** 0.9
                        beam_strength = np.clip(
                            beam_falloff * (0.24 + 0.58 * detail + self._beat_pulse * 0.24) * envelope,
                            0.0,
                            1.0,
                        )

                        # Centro um pouco mais brilhante, ponta mais escura.
                        edge_dim = 1.0 - (pos * 0.28)
                        base_color = self._lerp_color(low_color, high_color, agitation)
                        if hold_mix > 0.0:
                            base_color = self._lerp_color(base_color, high_color, hold_mix)
                        pixels[i][0] = base_color[0] * beam_strength
                        pixels[i][1] = base_color[1] * beam_strength * edge_dim
                        pixels[i][2] = base_color[2] * beam_strength * edge_dim

                    if not self.running:
                        break

                    if (
                        self.arduino
                        and self.arduino.is_connected()
                        and now >= self._next_serial_ts
                    ):
                        self.arduino.send_full_frame(pixels)
                        self._next_serial_ts = now + (1.0 / max(1, self.serial_fps))

                    if self.ui_callback and now >= self._next_ui_ts:
                        for i in range(self.num_leds):
                            r, g, b = map(int, pixels[i])
                            self.ui_callback(i, f"#{r:02x}{g:02x}{b:02x}")
                        self._next_ui_ts = now + (1.0 / max(1, self.ui_fps))

                    # recorder.record já bloqueia por bloco; evitar sleep extra reduz latência.
        except Exception:
            logging.exception("Music Mode: erro durante captura/processamento de áudio")