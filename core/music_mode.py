class MusicMode:
    def __init__(self, arduino):
        self.arduino = arduino
        self.is_running = False

    def start(self):
        self.is_running = True
        # Lógica de FFT/Áudio entrará aqui
        print("Music Mode: Iniciado")

    def stop(self):
        self.is_running = False
        print("Music Mode: Parado")