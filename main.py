import os
import customtkinter as ctk
from PIL import Image
import threading
import time
import pystray
from pystray import MenuItem as item
import sys

# Importando lógica do pacote core
from core import ArduinoComm, PianoMode, MusicMode, EffectsMode

# Design System
COLOR_BG = "#070708"
COLOR_CARD = "#121217"
COLOR_SELECTED = "#5e5ce6"
COLOR_TEXT = "#efeff4"

_APP_DIR = os.path.dirname(os.path.abspath(__file__))
LOGO_ICO_PATH = os.path.join(_APP_DIR, "assets", "logo.ico")


class LEDControllerApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("LED Controller Pro")
        self.geometry("850x620")
        self.resizable(False, False)
        self.configure(fg_color=COLOR_BG)

        try:
            self.iconbitmap(LOGO_ICO_PATH)
        except Exception:
            pass

        # --- Hardware & Motores ---
        self.arduino = ArduinoComm()

        # Inicializa os modos
        self.piano = PianoMode(self.arduino)
        self.music = MusicMode(self.arduino)
        self.effects = EffectsMode(self.arduino)

        # CONFIGURAÇÃO CRUCIAL: Todos os modos precisam do callback da UI
        self.piano.set_callback(self.update_led_canvas)
        self.effects.set_callback(self.update_led_canvas)
        self.music.set_callback(self.update_led_canvas)

        self.active_mode_obj = None
        self.buttons = {}
        self._search_generation = 0
        self._search_anim_step = 0
        self._search_anim_job = None
        self.stop_watchdog = False

        self.setup_ui()
        self.slider.set(100)

        self.protocol("WM_DELETE_WINDOW", self.hide_window)
        self.create_tray_icon()

        self.start_watchdog()
        self.after(500, self.auto_connect)

    def setup_ui(self):
        # TOP BAR
        self.top_bar = ctk.CTkFrame(self, fg_color="transparent")
        self.top_bar.pack(fill="x", padx=40, pady=(35, 0))

        self.status_indicator = ctk.CTkLabel(self.top_bar, text="● SEARCHING",
                                             font=("Nunito", 11, "bold"), text_color="#ff9f0a")
        self.status_indicator.pack(side="left")

        ctk.CTkLabel(self, text="LED CONTROLLER SYSTEM",
                     font=("Nunito", 12, "bold"), text_color="gray30").pack(pady=(20, 0))

        # MODES GRID
        self.center_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.center_frame.pack(expand=True)

        self.create_mode_card("Piano", "assets/piano.png", 0, self.piano)
        self.create_mode_card("Music", "assets/music.png", 1, self.music)
        self.create_mode_card("Effects", "assets/magic.png", 2, self.effects)

        # SIMULADOR DE LEDS (88 LEDs - 1:1)
        ctk.CTkLabel(self, text="LIVE LED SIMULATION",
                     font=("Nunito", 9, "bold"), text_color="gray35").pack(pady=(10, 0))

        self.sim_frame = ctk.CTkFrame(self, fg_color="#0a0a0c", height=45, corner_radius=5)
        self.sim_frame.pack(fill="x", padx=60, pady=(5, 15))

        self.led_canvas = ctk.CTkCanvas(self.sim_frame, height=30, bg="#0a0a0c",
                                        highlightthickness=0, bd=0)
        self.led_canvas.pack(fill="both", expand=True, padx=5, pady=5)

        self.led_drawings = []
        self.num_leds = 88
        self.after(200, self.init_simulator)

        # BRIGHTNESS
        self.bottom_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.bottom_frame.pack(fill="x", padx=120, pady=(0, 40))

        ctk.CTkLabel(self.bottom_frame, text="MASTER BRIGHTNESS",
                     font=("Nunito", 10, "bold"), text_color="gray40").pack()

        self.slider = ctk.CTkSlider(self.bottom_frame, from_=0, to=100, height=14,
                                    button_color=COLOR_SELECTED, progress_color=COLOR_SELECTED,
                                    command=self.update_brightness)
        self.slider.pack(fill="x", pady=10)

    def init_simulator(self):
        self.led_canvas.update()
        w = self.led_canvas.winfo_width()
        led_w = w / self.num_leds
        for i in range(self.num_leds):
            x = i * led_w
            rect = self.led_canvas.create_rectangle(x, 0, x + led_w - 1, 30, fill="#1a1a1a", outline="")
            self.led_drawings.append(rect)

    def update_led_canvas(self, led_id, color_hex):
        """Metodo de alta performance para atualizar o canvas"""
        if 0 <= led_id < self.num_leds:
            self.led_canvas.itemconfig(self.led_drawings[led_id], fill=color_hex)

    def clear_led_simulator(self):
        """Volta o simulador ao estado inicial (LEDs apagados)."""
        if len(self.led_drawings) != self.num_leds:
            return
        off = "#1a1a1a"
        for i in range(self.num_leds):
            self.led_canvas.itemconfig(self.led_drawings[i], fill=off)

    def clear_all_leds(self):
        if self.arduino.is_connected():
            self.arduino.clear_leds()
            time.sleep(0.03)
            self.arduino.clear_leds()
        self.clear_led_simulator()

    def create_mode_card(self, name, icon_path, col, mode_obj):
        try:
            img = ctk.CTkImage(Image.open(icon_path), size=(48, 48))
        except:
            img = None

        btn = ctk.CTkButton(self.center_frame, text=name.upper(), image=img, compound="top",
                            width=200, height=180, corner_radius=25,
                            fg_color=COLOR_CARD, hover_color="#1a1a24",
                            font=("Nunito", 14, "bold"), text_color="gray60",
                            border_spacing=20,
                            command=lambda n=name, obj=mode_obj: self.select_mode(n, obj))
        btn.grid(row=0, column=col, padx=15)
        self.buttons[name] = btn

    # --- System Tray & Janela ---
    def create_tray_icon(self):
        try:
            icon_img = Image.open(LOGO_ICO_PATH)
        except:
            icon_img = Image.new('RGB', (64, 64), color=(94, 92, 230))
        menu = (item('Abrir Interface', self.show_window), item('Sair', self.quit_application))
        self.tray_icon = pystray.Icon("LEDController", icon_img, "LED Controller Pro", menu)
        threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def hide_window(self):
        self.withdraw()

    def show_window(self, icon=None, item=None):
        self.deiconify(); self.focus_force()

    def quit_application(self, icon=None, item=None):
        self.stop_watchdog = True
        if self._search_anim_job:
            self.after_cancel(self._search_anim_job)
            self._search_anim_job = None
        if self.active_mode_obj: self.active_mode_obj.stop()
        self.arduino.close();
        self.tray_icon.stop();
        self.destroy();
        sys.exit()

    def _animate_search_status(self):
        if self.stop_watchdog:
            return
        dots = "." * ((self._search_anim_step % 3) + 1)
        self.status_indicator.configure(text=f"● SEARCHING{dots}", text_color="#ff9f0a")
        self._search_anim_step += 1
        self._search_anim_job = self.after(350, self._animate_search_status)

    def set_status_searching(self):
        if self._search_anim_job:
            self.after_cancel(self._search_anim_job)
            self._search_anim_job = None
        self._search_anim_step = 0
        self._animate_search_status()

    def set_status_online(self):
        if self._search_anim_job:
            self.after_cancel(self._search_anim_job)
            self._search_anim_job = None
        port = self.arduino.port or "?"
        self.status_indicator.configure(text=f"● ONLINE | {port}", text_color="#32d74b")

    # --- Watchdog de Conexao ---
    def start_watchdog(self):
        def watch():
            while not self.stop_watchdog:
                if self.arduino.connection and not self.arduino.verify_still_plugged():
                    self.after(0, self.handle_disconnection)
                time.sleep(1.0)

        threading.Thread(target=watch, daemon=True).start()

    def handle_disconnection(self):
        self.arduino.close()
        if self.active_mode_obj:
            self.active_mode_obj.stop()
        self.auto_connect()

    def auto_connect(self):
        """Procura Arduino em loop; cancela buscas antigas ao incrementar _search_generation."""
        self._search_generation += 1
        gen = self._search_generation

        def worker():
            self.after(0, self.set_status_searching)
            while not self.stop_watchdog and gen == self._search_generation:
                if self.arduino.connect():
                    if gen == self._search_generation:
                        self.after(0, self.set_status_online)
                    return
                time.sleep(0.75)

        threading.Thread(target=worker, daemon=True).start()

    def update_brightness(self, value):
        if self.arduino.is_connected(): self.arduino.send_command(254, int(value))

    def select_mode(self, mode_name, mode_obj):
        # Desliga o modo anterior
        if self.active_mode_obj:
            self.active_mode_obj.stop()

        self.clear_all_leds()

        # Limpa o visual dos botoes
        for btn in self.buttons.values():
            btn.configure(fg_color=COLOR_CARD, border_width=0, text_color="gray60")

        # Ativa o novo modo
        self.active_mode_obj = mode_obj
        self.buttons[mode_name].configure(fg_color="#1a1a24", border_width=2,
                                          border_color=COLOR_SELECTED, text_color="white")

        # Inicia a thread do modo
        mode_obj.start()


if __name__ == "__main__":
    app = LEDControllerApp()
    app.mainloop()