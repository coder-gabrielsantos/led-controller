import mido
import threading
import time
import math


class PianoMode:
    def __init__(self, arduino):
        self.arduino = arduino
        self.running = False
        self._midi_thread = None
        self._render_thread = None
        self.ui_callback = None
        self.num_leds = 88

        # Buffer de trabalho
        self.pixel_data = [[0.0, 0.0, 0.0] for _ in range(self.num_leds)]
        self.pressed_keys = {}
        self.active_effects = []

        # Configurações da Respiração
        self.breath_phase = 0.0
        self.breath_speed = 0.05  # Velocidade do pulsar

    def set_callback(self, callback_func):
        self.ui_callback = callback_func

    def start(self):
        if not self.running:
            self.running = True
            self._midi_thread = threading.Thread(target=self.midi_listen, daemon=True)
            self._render_thread = threading.Thread(target=self.render_loop, daemon=True)
            self._midi_thread.start()
            self._render_thread.start()

    def stop(self):
        self.running = False
        current = threading.current_thread()
        if self._midi_thread and self._midi_thread.is_alive() and self._midi_thread is not current:
            self._midi_thread.join(timeout=0.2)
        if self._render_thread and self._render_thread.is_alive() and self._render_thread is not current:
            self._render_thread.join(timeout=0.2)
        self.pressed_keys.clear()
        self.active_effects.clear()
        for i in range(self.num_leds):
            self.pixel_data[i] = [0.0, 0.0, 0.0]

    def get_lerp_color(self, velocity):
        v = velocity / 127.0
        if v <= 0.4:
            ratio = v / 0.4
            return [0.0 + (100.0 * ratio), 242.0 + (13.0 * ratio), 255.0 * (1.0 - ratio)]
        else:
            ratio = (v - 0.4) / 0.6
            return [100.0 + (155.0 * ratio), 255.0 * (1.0 - ratio), 0.0]

    def midi_listen(self):
        """Escuta o MIDI e ignora comandos de pedal/sustain para não travar."""
        try:
            input_names = mido.get_input_names()
            if not input_names: return

            with mido.open_input(input_names[0]) as inport:
                while self.running:
                    for msg in inport.iter_pending():
                        if not self.running:
                            break
                        # FILTRO: Ignora Sustain (CC 64) e outros controles
                        if msg.type == 'control_change' or msg.is_meta:
                            continue

                        if msg.type in ['note_on', 'note_off']:
                            idx = msg.note - 21
                            if 0 <= idx < self.num_leds:
                                if msg.type == 'note_on' and msg.velocity > 0:
                                    color = self.get_lerp_color(msg.velocity)
                                    self.pressed_keys[msg.note] = color
                                    self.trigger_ripple(idx, color, msg.velocity)
                                elif msg.type == 'note_off' or (msg.type == 'note_on' and msg.velocity == 0):
                                    if msg.note in self.pressed_keys:
                                        del self.pressed_keys[msg.note]
                    time.sleep(0.001)
        except:
            pass

    def trigger_ripple(self, idx, color, velocity):
        strength = velocity / 127.0
        self.active_effects.append({
            'pos': idx, 'radius': 0.0, 'color': color,
            'life': 1.0, 'speed': 2.0 + (strength * 2.5)
        })

    def render_loop(self):
        while self.running:
            start_time = time.time()

            # --- 1. CÁLCULO DA RESPIRAÇÃO (Idle Animation) ---
            self.breath_phase += self.breath_speed
            # Oscila entre 5 e 25 de brilho no canal Roxo
            breath_intensity = (math.sin(self.breath_phase) + 1) / 2
            bg_r = 5 + (10 * breath_intensity)
            bg_g = 0
            bg_b = 15 + (25 * breath_intensity)

            # --- 2. DECAIMENTO PARA O ESTADO DE RESPIRAÇÃO ---
            for i in range(self.num_leds):
                self.pixel_data[i][0] = self.pixel_data[i][0] * 0.86 + bg_r * 0.14
                self.pixel_data[i][1] = self.pixel_data[i][1] * 0.86 + bg_g * 0.14
                self.pixel_data[i][2] = self.pixel_data[i][2] * 0.86 + bg_b * 0.14

            # --- 3. PROCESSAR ONDAS (Ripples) ---
            for effect in self.active_effects[:]:
                effect['radius'] += effect['speed']
                effect['life'] -= 0.035

                if effect['life'] <= 0:
                    self.active_effects.remove(effect)
                    continue

                r_int = int(effect['radius'])
                for i in [effect['pos'] - r_int, effect['pos'] + r_int]:
                    if 0 <= i < self.num_leds:
                        for neighbor in range(-1, 2):
                            n_idx = i + neighbor
                            if 0 <= n_idx < self.num_leds:
                                pwr = 0.6 if neighbor == 0 else 0.25
                                for c in range(3):
                                    blend = self.pixel_data[n_idx][c] + (effect['color'][c] * effect['life'] * pwr)
                                    self.pixel_data[n_idx][c] = min(255, blend)

            # --- 4. SOBREPOSIÇÃO DE TECLAS PRESSIONADAS (Branco) ---
            for note in list(self.pressed_keys.keys()):
                idx = note - 21
                if 0 <= idx < self.num_leds:
                    self.pixel_data[idx] = [255.0, 255.0, 255.0]

            # --- 5. ENVIO PARA ARDUINO E UI ---
            if not self.running:
                break
            if self.arduino and self.arduino.is_connected():
                self.arduino.send_full_frame(self.pixel_data)

            if self.ui_callback:
                for i in range(self.num_leds):
                    r, g, b = map(int, self.pixel_data[i])
                    self.ui_callback(i, f"#{r:02x}{g:02x}{b:02x}")

            time.sleep(max(0, 1 / 60 - (time.time() - start_time)))