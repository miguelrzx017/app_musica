"""
audio.py - o MOTOR DE ÁUDIO do app. Fica separado da interface (main.py).

POR QUE ESTE ARQUIVO EXISTE
O SoundLoader do Kivy não tem "pausar", e dependendo do backend de áudio
(principalmente o SDL2, que é o padrão no PC) ele também NÃO sabe:
  - dizer em que ponto da música está (get_pos devolve sempre 0)
  - pular para um ponto da música (seek não faz nada)
Resultado: a barra de tempo não andava e, ao pausar, a música voltava do início.

A solução tem duas partes:
  1) POSIÇÃO: o app mede o tempo por conta própria (um relógio) quando o
     backend não informa a posição. Assim a barra sempre anda.
  2) PAUSA/SEEK: precisam de um backend que saiba pular no tempo:
       - Android  -> usamos o MediaPlayer do próprio Android (via pyjnius)
       - PC       -> o Kivy usa o ffpyplayer se ele estiver instalado:
                         pip install ffpyplayer
     Se nenhum dos dois existir, o app avisa no terminal e a música
     recomeça do início ao despausar (é o limite do SDL2).

Os dois motores têm os MESMOS métodos, então o main.py não precisa saber
qual está em uso:
    carregar(caminho) -> bool   abre o arquivo (ainda sem tocar)
    tocar()                      começa do início
    pausar() / retomar()         pausa e continua de onde parou
    buscar(segundos)             pula para um ponto da música
    posicao()                    segundos já tocados
    duracao()                    duração em segundos (0 se não souber)
    terminou()                   True quando a música chegou ao fim
    parar()                      para e libera o arquivo
"""

import time

from kivy.clock import Clock


class MotorKivy:
    """Usa o SoundLoader do Kivy (PC e, na falta de coisa melhor, qualquer plataforma)."""

    # Backends que sabem pular no tempo. O nome vem da classe do som
    # (SoundFFPy, SoundSDL2, SoundAndroidPlayer...).
    _BACKENDS_COM_SEEK = ("ffpy", "android", "gst", "avplayer")

    def __init__(self):
        self.som = None
        self.suporta_seek = False
        self._tocando = False
        self._base = 0.0   # posição (s) no momento em que o relógio foi zerado
        self._t0 = 0.0     # hora (time.monotonic) em que o relógio foi zerado
        self._started_at = 0.0  # evita detectar falso "fim" logo após play()
        self._avisou = False

    # ---------------- abrir / fechar ----------------
    def carregar(self, caminho):
        from kivy.core.audio import SoundLoader

        self.parar()
        som = SoundLoader.load(str(caminho))  # None se o arquivo não abrir
        if som is None:
            return False
        self.som = som
        nome = type(som).__name__.lower()
        self.suporta_seek = any(b in nome for b in self._BACKENDS_COM_SEEK)
        if not self.suporta_seek and not self._avisou:
            self._avisou = True
            print(
                f"AVISO: o backend de áudio '{type(som).__name__}' não sabe pular no tempo.\n"
                "       Ao despausar a música recomeça do início. Para corrigir no PC:\n"
                "       pip install ffpyplayer   (o app passa a usá-lo sozinho)"
            )
        self._tocando = False
        self._base = 0.0
        return True

    def parar(self):
        som, self.som = self.som, None
        self._tocando = False
        self._base = 0.0
        self._t0 = 0.0
        self._started_at = 0.0
        if som is not None:
            try:
                som.stop()
            except Exception as erro:
                print("áudio: erro ao parar:", erro)
            try:
                som.unload()
            except Exception as erro:
                print("áudio: erro ao descarregar:", erro)

    # ---------------- controles ----------------
    def tocar(self):
        if self.som is None:
            return False
        try:
            self._base = 0.0
            self._t0 = time.monotonic()
            self._started_at = self._t0
            self._tocando = True
            self.som.play()
            return True
        except Exception as erro:
            self._tocando = False
            print("áudio: play falhou:", erro)
            return False

    def pausar(self):
        if self.som is None or not self._tocando:
            return
        self._base = self.posicao()  # guarda ONDE parou, antes de parar
        self._tocando = False
        self.som.stop()

    def retomar(self):
        if self.som is None or self._tocando:
            return False
        if not self.suporta_seek:
            self._base = 0.0  # sem seek não há como continuar: recomeça
        alvo = self._base
        som_atual = self.som
        try:
            self._t0 = time.monotonic()
            self._started_at = self._t0
            self._tocando = True
            som_atual.play()
        except Exception as erro:
            self._tocando = False
            print("áudio: retomar falhou:", erro)
            return False

        if self.suporta_seek and alvo > 0.2:
            # O callback captura o som original. Se outra música já tiver sido
            # carregada, ele é ignorado e não faz seek na faixa nova.
            Clock.schedule_once(
                lambda dt: self._pular_para(alvo, som_atual),
                0.2,
            )
        return True

    def buscar(self, segundos):
        """Arrastou a barra. Devolve False se o backend não consegue pular."""
        if self.som is None or not self.suporta_seek:
            return False
        segundos = max(0.0, segundos)
        self._base = segundos
        self._t0 = time.monotonic()
        if self._tocando:
            self._pular_para(segundos)
        # pausado: o valor fica em _base e é aplicado em retomar()
        return True

    def _pular_para(self, segundos, som_esperado=None):
        if self.som is None:
            return
        # Evita callback atrasado de uma música antiga mexer na música atual.
        if som_esperado is not None and self.som is not som_esperado:
            return
        try:
            self.som.seek(segundos)
        except Exception as erro:
            print("áudio: seek falhou:", erro)

    # ---------------- consultas ----------------
    def posicao(self):
        if self.som is None:
            return 0.0
        if not self._tocando:
            return self._base  # pausado: fica parado no ponto guardado
        if self.suporta_seek:
            try:
                p = self.som.get_pos()
            except Exception:
                p = 0
            if p and p > 0:
                return p
        # backend não informa a posição: usa o relógio
        return self._base + (time.monotonic() - self._t0)

    def duracao(self):
        try:
            return float(self.som.length) if self.som is not None else 0.0
        except Exception:
            return 0.0

    def terminou(self):
        if self.som is None or not self._tocando:
            return False

        dur = self.duracao()
        pos = self.posicao()

        # Primeiro usa duração/posição. Isso evita o falso "fim" que pode
        # acontecer porque alguns backends ainda estão em state='stop' nos
        # primeiros instantes de uma faixa recém-carregada.
        if dur:
            if pos >= max(0.0, dur - 0.35) and self.som.state == "stop":
                return True
            if pos > dur + 0.8:
                return True
            return False

        # Sem duração conhecida, só aceita state='stop' depois de um intervalo
        # mínimo desde o início da faixa.
        return (
            self.som.state == "stop"
            and self._started_at
            and time.monotonic() - self._started_at > 1.2
        )


class MotorAndroid:
    """MediaPlayer nativo do Android (pyjnius). Pausa e seek funcionam de verdade."""

    suporta_seek = True

    def __init__(self):
        from jnius import autoclass  # só existe no Android

        self._MediaPlayer = autoclass("android.media.MediaPlayer")
        self.mp = None
        self._tocando = False  # "queremos que esteja tocando" (False = pausado)

    def carregar(self, caminho):
        self.parar()
        mp = self._MediaPlayer()
        try:
            mp.setDataSource(str(caminho))
            mp.prepare()  # arquivo local
        except Exception as erro:
            print("áudio: não consegui abrir", caminho, "->", erro)
            try:
                mp.reset()
            except Exception:
                pass
            try:
                mp.release()
            except Exception:
                pass
            return False

        self.mp = mp
        self._tocando = False
        return True

    def parar(self):
        mp, self.mp = self.mp, None
        self._tocando = False
        if mp is not None:
            try:
                mp.stop()
            except Exception:
                pass
            try:
                mp.release()
            except Exception:
                pass

    def tocar(self):
        if self.mp is None:
            return False
        try:
            self.mp.start()
            self._tocando = True
            return True
        except Exception as erro:
            self._tocando = False
            print("áudio: play Android falhou:", erro)
            try:
                self.mp.reset()
            except Exception:
                pass
            return False

    def pausar(self):
        if self.mp is None or not self._tocando:
            return
        self._tocando = False
        try:
            self.mp.pause()
        except Exception as erro:
            print("áudio: pause falhou:", erro)

    def retomar(self):
        if self.mp is None or self._tocando:
            return False
        try:
            self.mp.start()  # continua exatamente de onde o pause() parou
            self._tocando = True
            return True
        except Exception as erro:
            self._tocando = False
            print("áudio: start Android falhou:", erro)
            return False

    def buscar(self, segundos):
        if self.mp is None:
            return False
        try:
            self.mp.seekTo(int(max(0.0, segundos) * 1000))
        except Exception as erro:
            print("áudio: seek falhou:", erro)
            return False
        return True

    def posicao(self):
        if self.mp is None:
            return 0.0
        try:
            return self.mp.getCurrentPosition() / 1000.0
        except Exception:
            return 0.0

    def duracao(self):
        if self.mp is None:
            return 0.0
        try:
            return self.mp.getDuration() / 1000.0
        except Exception:
            return 0.0

    def terminou(self):
        if self.mp is None or not self._tocando:
            return False

        try:
            dur = self.mp.getDuration() / 1000.0
            pos = self.mp.getCurrentPosition() / 1000.0

            # MediaPlayer pode responder isPlaying=False nos primeiros
            # instantes de uma nova faixa. Só considera fim perto da duração.
            if dur > 0:
                return pos >= max(0.0, dur - 0.35) and not self.mp.isPlaying()

            return not self.mp.isPlaying()
        except Exception:
            return False


def criar_motor():
    """Escolhe o motor certo para a plataforma."""
    from kivy.utils import platform

    if platform == "android":
        try:
            return MotorAndroid()
        except Exception as erro:
            print("áudio: MediaPlayer indisponível, usando o Kivy:", erro)
    return MotorKivy()
