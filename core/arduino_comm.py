import os
import serial
import serial.tools.list_ports
import threading
import time


NUM_LEDS = 88
DEFAULT_MAX_BRIGHTNESS_PERCENT = 65

# Descrição / fabricante típicos de Arduino e adaptadores USB-serial
_PORT_KEYWORDS = (
    "arduino", "ch340", "ch341", "ch9102", "usb serial", "genuino",
    "silicon labs", "cp210", "ftdi", "usb-serial", "wch.cn", "wch",
    # Windows em PT/ES e nomes genéricos
    "serial usb", "dispositivo de s", "serie usb",
    "comunicações", "comunicaciones",
)


def _norm_com_name(device):
    """COM3 e \\\\.\\COM3 são a mesma porta no Windows."""
    if not device:
        return ""
    d = device.strip().upper()
    if d.startswith("\\\\.\\"):
        d = d[4:]
    return d


class ArduinoComm:
    def __init__(self, baudrate=500000):
        self.port = None
        env = os.environ.get("ARDUINO_BAUD")
        self.baudrate = int(env) if env else baudrate
        max_env = os.environ.get("LED_MAX_BRIGHTNESS_PERCENT")
        try:
            self.max_brightness_percent = int(max_env) if max_env else DEFAULT_MAX_BRIGHTNESS_PERCENT
        except ValueError:
            self.max_brightness_percent = DEFAULT_MAX_BRIGHTNESS_PERCENT
        self.max_brightness_percent = max(1, min(100, self.max_brightness_percent))
        self.brightness_percent = self.max_brightness_percent
        self.connection = None
        # RLock evita deadlock quando métodos com lock chamam outros métodos com lock.
        self._io_lock = threading.RLock()

    @staticmethod
    def _port_blob(p):
        return " ".join(
            filter(
                None,
                [
                    getattr(p, "description", None) or "",
                    getattr(p, "manufacturer", None) or "",
                    getattr(p, "product", None) or "",
                    getattr(p, "hwid", None) or "",
                ],
            )
        ).lower()

    @classmethod
    def _port_matches_candidate(cls, p):
        blob = cls._port_blob(p)
        if any(k in blob for k in _PORT_KEYWORDS):
            return True
        # Qualquer dispositivo USB com interface serial (hwid típico no Windows)
        hw = (getattr(p, "hwid", None) or "").upper()
        if "VID_" in hw and ("PID_" in hw or "MI_" in hw):
            return True
        return False

    @classmethod
    def _ports_to_try(cls):
        """Prioriza portas que parecem Arduino/USB-serial; depois tenta as restantes."""
        ports = list(serial.tools.list_ports.comports())
        preferred = [p for p in ports if cls._port_matches_candidate(p)]
        pref_devices = {p.device for p in preferred}
        rest = [p for p in ports if p.device not in pref_devices]
        return preferred + rest

    def connect(self):
        """Procura portas seriais e conecta à primeira que abrir (prioriza candidatos óbvios)."""
        with self._io_lock:
            self.close()
            for p in self._ports_to_try():
                try:
                    self.connection = serial.Serial(p.device, self.baudrate, timeout=0.1)
                    self.port = p.device
                    time.sleep(2)  # reset do MCU ao abrir a serial
                    return True
                except (OSError, serial.SerialException, ValueError):
                    continue
            return False

    def is_connected(self):
        """True se o objeto serial está aberto (checagem rápida; use verify_still_plugged no watchdog)."""
        return bool(self.connection and self.connection.is_open)

    def verify_still_plugged(self):
        """
        Confere se a porta ainda aparece no sistema (ex.: cabo USB desligado).
        Fecha a serial e limpa o estado se a porta sumiu.
        """
        with self._io_lock:
            if not self.connection or not self.connection.is_open:
                return False
            if not self.port:
                return True
            mine = _norm_com_name(self.port)

            def _listed():
                return mine in {_norm_com_name(x.device) for x in serial.tools.list_ports.comports()}

            if _listed():
                return True
            time.sleep(0.3)
            if _listed():
                return True
            try:
                self.connection.close()
            except OSError:
                pass
            self.connection = None
            self.port = None
            return False

    def send_full_frame(self, pixel_data):
        """
        Envia o estado de todos os 88 LEDs em um único pacote de bytes.
        pixel_data: Lista de sublistas [[R,G,B], [R,G,B]...]
        """
        with self._io_lock:
            if not self.connection or not self.connection.is_open:
                return
            try:
                factor = self.brightness_percent / 100.0
                packet = bytearray()
                for r, g, b in pixel_data:
                    packet.append(int(max(0, min(255, r * factor))))
                    packet.append(int(max(0, min(255, g * factor))))
                    packet.append(int(max(0, min(255, b * factor))))
                self.connection.write(packet)
            except (OSError, serial.SerialException) as e:
                print(f"Erro de transmissão: {e}")
                try:
                    self.connection.close()
                except OSError:
                    pass
                self.connection = None
                self.port = None

    def set_brightness(self, percent):
        """
        Define brilho global no lado do PC.
        O valor é limitado por max_brightness_percent para proteger fonte/fita.
        """
        pct = int(max(0, min(100, percent)))
        self.brightness_percent = min(pct, self.max_brightness_percent)

    def clear_leds(self):
        """Apaga todos os LEDs (frame RGB preto)."""
        black = [[0, 0, 0] for _ in range(NUM_LEDS)]
        self.send_full_frame(black)

    def close(self):
        with self._io_lock:
            if self.connection:
                try:
                    self.connection.close()
                except OSError:
                    pass
                self.connection = None
            self.port = None