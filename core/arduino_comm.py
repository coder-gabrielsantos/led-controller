import serial
import serial.tools.list_ports
import time


class ArduinoComm:
    def __init__(self, baudrate=500000):
        self.port = None
        self.baudrate = baudrate
        self.connection = None

    def connect(self):
        """Busca e conecta ao Arduino automaticamente."""
        ports = list(serial.tools.list_ports.comports())
        for p in ports:
            # Filtra por identificadores comuns de Arduino/Chips Seriais
            if any(x in p.description for x in ["Arduino", "CH340", "USB Serial", "Genuino"]):
                try:
                    self.connection = serial.Serial(p.device, self.baudrate, timeout=0.1)
                    self.port = p.device
                    time.sleep(2)  # Aguarda o reset do Arduino ao conectar
                    return True
                except:
                    continue
        return False

    def is_connected(self):
        """Verifica se a conexão ainda está ativa."""
        if self.connection and self.connection.is_open:
            return True
        return False

    def send_full_frame(self, pixel_data):
        """
        Envia o estado de todos os 88 LEDs em um único pacote de bytes.
        pixel_data: Lista de sublistas [[R,G,B], [R,G,B]...]
        """
        if self.is_connected():
            try:
                # Criar um bytearray é muito mais rápido que enviar um por um
                packet = bytearray()
                for r, g, b in pixel_data:
                    # Clamp: Garante que os valores estejam entre 0 e 255
                    packet.append(int(max(0, min(255, r))))
                    packet.append(int(max(0, min(255, g))))
                    packet.append(int(max(0, min(255, b))))

                self.connection.write(packet)
            except Exception as e:
                print(f"Erro de transmissão: {e}")
                self.connection = None

    def close(self):
        if self.connection:
            self.connection.close()
            self.connection = None