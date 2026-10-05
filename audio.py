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
       - PC       -> pygame.mixer.music (MotorPygame):  pip install pygame
                     (o ffpyplayer NÃO é usado: derruba o app no Windows)
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
from pathlib import Path

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
        self._seek_event = None
        self._avisou = False
        self.aviso = ""

    # ---------------- abrir / fechar ----------------
    def carregar(self, caminho):
        from kivy.core.audio import SoundLoader

        self.parar()
        caminho = Path(caminho)
        if not caminho.is_file():
            print(f"áudio: arquivo não encontrado: {caminho}")
            return False

        try:
            som = SoundLoader.load(str(caminho))  # None se o arquivo não abrir
        except Exception as erro:
            print(f"áudio: falha ao carregar '{caminho}':", erro)
            return False

        if som is None:
            print(f"áudio: SoundLoader não conseguiu abrir '{caminho}'")
            return False
        self.som = som
        nome = type(som).__name__.lower()
        self.suporta_seek = any(b in nome for b in self._BACKENDS_COM_SEEK)
        if not self.suporta_seek and not self._avisou:
            self._avisou = True
            print(
                f"AVISO: o backend de áudio '{type(som).__name__}' não sabe pular no tempo.\n"
                "       Ao despausar a música recomeça do início.\n"
                "       Corrija instalando o pygame (NÃO instale o ffpyplayer, ele derruba o app):\n"
                "           python -m pip install pygame"
            )
        self._tocando = False
        self._base = 0.0
        return True

    def parar(self):
        if self._seek_event is not None:
            try:
                self._seek_event.cancel()
            except Exception:
                pass
            self._seek_event = None

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
            self.som.play()
        except Exception as erro:
            self._tocando = False
            print("áudio: play falhou:", erro)
            return False

        self._base = 0.0
        self._t0 = time.monotonic()
        self._started_at = self._t0
        self._tocando = True
        return True

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
            som_atual.play()
        except Exception as erro:
            self._tocando = False
            print("áudio: retomar falhou:", erro)
            return False

        self._t0 = time.monotonic()
        self._started_at = self._t0
        self._tocando = True

        if self.suporta_seek and alvo > 0.2:
            # O callback captura o som original. Se outra música já tiver sido
            # carregada, ele é ignorado e não faz seek na faixa nova.
            self._seek_event = Clock.schedule_once(
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
        finally:
            self._seek_event = None

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
        desde_o_play = time.monotonic() - self._started_at
        # 1) O backend avisa que parou (state='stop'). Esperamos ~1,5 s depois do
        #    play porque alguns backends ainda estão em 'stop' no começo da faixa.
        if self.som.state == "stop" and desde_o_play > 1.5:
            return True
        # 2) Segurança: a posição passou da duração (a duração do mp3 pode ser
        #    levemente imprecisa, por isso a folga).
        dur = self.duracao()
        return bool(dur) and desde_o_play > 1.5 and self.posicao() > dur + 1.5


class MotorAndroid:
    """MediaPlayer nativo do Android (pyjnius). Pausa e seek funcionam de verdade."""

    suporta_seek = True

    def __init__(self):
        from jnius import autoclass  # só existe no Android

        self._MediaPlayer = autoclass("android.media.MediaPlayer")
        self.mp = None
        self._tocando = False  # "queremos que esteja tocando" (False = pausado)
        self._started_at = 0.0  # evita detectar falso "fim" logo depois do start()

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
            self._started_at = time.monotonic()
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
            self._started_at = time.monotonic()
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
        if time.monotonic() - self._started_at < 1.0:
            return False
        try:
            return not self.mp.isPlaying()  # queríamos tocar e parou sozinho = fim
        except Exception:
            return False


class MotorPygame:
    """
    Motor de áudio do PC usando pygame.mixer.music (pip install pygame).

    POR QUE ELE EXISTE
    O Kivy toca mp3 pelo ffpyplayer, e no Windows o ffpyplayer CAI (access
    violation dentro de MediaPlayer(...)) ao abrir a SEGUNDA música, porque o
    Kivy cria um player novo a cada faixa. O pygame.mixer.music tem uma única
    "stream" que é reaproveitada: load() troca de arquivo sem criar/destruir
    threads nem dispositivos de áudio. Pausa e seek funcionam.

    A posição é medida por um relógio nosso (o get_pos do pygame não conta
    o ponto de onde o seek começou).
    """

    suporta_seek = True

    def __init__(self):
        import os

        os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
        import pygame

        self._pg = pygame
        pygame.mixer.init()  # abre o dispositivo de áudio UMA vez, no início
        self._carregada = False
        self._tocando = False    # "queremos som saindo" (False = pausado)
        self._offset = 0.0       # posição (s) em que o play() atual começou
        self._t0 = 0.0           # hora (monotonic) em que o play() atual começou
        self._parado_em = 0.0    # posição guardada enquanto pausado
        self._seek_pendente = False  # buscar() chamado com a música pausada
        self._duracao = 0.0
        self._started_at = 0.0

    def carregar(self, caminho):
        self.parar()
        caminho = Path(caminho)
        if not caminho.is_file():
            print(f"áudio: arquivo não encontrado: {caminho}")
            return False
        try:
            self._pg.mixer.music.load(str(caminho))
        except Exception as erro:
            print(f"áudio: pygame não abriu '{caminho}':", erro)
            return False
        self._duracao = self._ler_duracao(caminho)
        self._carregada = True
        self._tocando = False
        self._offset = self._parado_em = 0.0
        self._seek_pendente = False
        return True

    @staticmethod
    def _ler_duracao(caminho):
        """Duração via mutagen (leve; o db.py já usa). 0 se não conseguir."""
        try:
            from mutagen import File

            info = File(str(caminho)).info
            return float(info.length)
        except Exception:
            return 0.0

    def parar(self):
        self._carregada = False
        self._tocando = False
        self._offset = self._parado_em = 0.0
        self._seek_pendente = False
        try:
            self._pg.mixer.music.stop()
            if hasattr(self._pg.mixer.music, "unload"):
                self._pg.mixer.music.unload()  # solta o arquivo (pygame >= 2.0)
        except Exception as erro:
            print("áudio: erro ao parar:", erro)

    def _iniciar_em(self, segundos):
        """Começa a tocar a partir de 'segundos' e reinicia o relógio."""
        segundos = max(0.0, segundos)
        if segundos > 0:
            self._pg.mixer.music.play(start=segundos)
        else:
            self._pg.mixer.music.play()
        self._offset = segundos
        self._t0 = time.monotonic()
        self._started_at = self._t0
        self._tocando = True
        self._seek_pendente = False

    def tocar(self):
        if not self._carregada:
            return False
        try:
            self._iniciar_em(0.0)
        except Exception as erro:
            self._tocando = False
            print("áudio: play falhou:", erro)
            return False
        return True

    def pausar(self):
        if not self._carregada or not self._tocando:
            return
        self._parado_em = self.posicao()  # guarda ONDE parou, antes de pausar
        self._tocando = False
        try:
            self._pg.mixer.music.pause()
        except Exception as erro:
            print("áudio: pause falhou:", erro)

    def retomar(self):
        if not self._carregada or self._tocando:
            return False
        try:
            if self._seek_pendente:
                self._iniciar_em(self._parado_em)  # arrastou a barra durante a pausa
            else:
                self._pg.mixer.music.unpause()
                self._offset = self._parado_em
                self._t0 = time.monotonic()
                self._started_at = self._t0
                self._tocando = True
        except Exception as erro:
            self._tocando = False
            print("áudio: retomar falhou:", erro)
            return False
        return True

    def buscar(self, segundos):
        if not self._carregada:
            return False
        segundos = max(0.0, segundos)
        if self._duracao:
            segundos = min(segundos, max(0.0, self._duracao - 1.0))
        try:
            if self._tocando:
                self._iniciar_em(segundos)
            else:
                self._parado_em = segundos  # aplicado em retomar()
                self._seek_pendente = True
        except Exception as erro:
            print("áudio: seek falhou:", erro)
            return False
        return True

    def posicao(self):
        if not self._carregada:
            return 0.0
        if not self._tocando:
            return self._parado_em
        pos = self._offset + (time.monotonic() - self._t0)
        return min(pos, self._duracao) if self._duracao else pos

    def duracao(self):
        return self._duracao

    def terminou(self):
        if not self._carregada or not self._tocando:
            return False
        if time.monotonic() - self._started_at < 1.0:
            return False  # evita "fim" falso logo depois do play()
        try:
            return not self._pg.mixer.music.get_busy()
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
    else:
        # PC: pygame primeiro (estável). O ffpyplayer do Kivy derruba o app no Windows.
        try:
            return MotorPygame()
        except Exception as erro:
            import sys

            print(
                "\n" + "=" * 70 + "\n"
                "AVISO: o pygame NÃO carregou (" + repr(erro) + ").\n"
                "       Sem ele, pausar reinicia a música e a barra não pula.\n"
                f"       Python em uso: {sys.version.split()[0]}  ({sys.executable})\n"
                "       Instale NESTE mesmo Python, com:\n"
                f'           "{sys.executable}" -m pip install pygame\n'
                "       Se falhar (Python muito novo), use o substituto compatível:\n"
                f'           "{sys.executable}" -m pip install pygame-ce\n'
                + "=" * 70 + "\n"
            )
            motor = MotorKivy()
            motor.aviso = "pygame não instalado: pausa e barra limitadas"
            return motor
    return MotorKivy()
