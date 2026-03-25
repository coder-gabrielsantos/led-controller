import threading
import time
import math


class EffectsMode:
    def __init__(self, arduino):
        self.arduino = arduino
        self.running = False
        self.ui_callback = None
        self.num_leds = 88
        self.offset = 0.0  # Controla o movimento do arco-íris

    def set_callback(self, callback_func):
        self.ui_callback = callback_func

    def start(self):
        if not self.running:
            self.running = True
            threading.Thread(target=self.render_loop, daemon=True).start()

    def stop(self):
        self.running = False

    def hsv_to_rgb(self, h, s, v):
        """Converte cores de HSV para RGB para o efeito arco-íris."""
        if s == 0.0: return [v, v, v]
        i = int(h * 6.0)
        f = (h * 6.0) - i
        p = v * (1.0 - s)
        q = v * (1.0 - s * f)
        t = v * (1.0 - s * (1.0 - f))
        i %= 6
        if i == 0: return [v, t, p]
        if i == 1: return [q, v, p]
        if i == 2: return [p, v, t]
        if i == 3: return [p, q, v]
        if i == 4: return [t, p, v]
        if i == 5: return [v, p, q]

    def render_loop(self):
        while self.running:
            t_start = time.time()
            pixel_data = []

            # Incrementa o offset para fazer o arco-íris "andar"
            self.offset += 0.01

            for i in range(self.num_leds):
                # Calcula o tom (Hue) baseado na posição do LED e no tempo (offset)
                hue = (i / self.num_leds) + self.offset
                hue = hue % 1.0  # Mantém entre 0.0 e 1.0

                # Geramos a cor RGB (Brilho 150 para não ficar agressivo no quarto)
                r, g, b = self.hsv_to_rgb(hue, 1.0, 150.0)
                pixel_data.append([r, g, b])

                # Atualiza a UI (Canvas)
                if self.ui_callback:
                    color_hex = f"#{int(r):02x}{int(g):02x}{int(b):02x}"
                    self.ui_callback(i, color_hex)

            # Envia o frame completo para o Arduino (Otimizado)
            if self.arduino and self.arduino.is_connected():
                self.arduino.send_full_frame(pixel_data)

            # Trava em 60 FPS para suavidade total
            time.sleep(max(0, 1 / 60 - (time.time() - t_start)))