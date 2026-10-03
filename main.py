"""
main.py - o "cérebro" da interface (Kivy). O visual fica no main.kv.

TELAS (cada uma é uma classe Screen; o ScreenManager troca entre elas):
  menu     -> tela inicial (playlists, banco de dados, estatísticas)
  playlist -> lista de músicas da playlist
  player   -> "Tocando agora"
  fila     -> fila de reprodução

ONDE FICA O ESTADO DO PLAYER?
Agora que existem várias telas, o que está tocando não pode morar em UMA tela.
Ele mora no MainApp (propriedades como titulo_atual, tocando, posicao...).
No .kv qualquer tela lê isso com "app.titulo_atual" e atualiza sozinha.
No Python, qualquer tela chama App.get_running_app().pular(), etc.
"""

import os

# ÁUDIO NO PC: se o ffpyplayer estiver instalado (pip install ffpyplayer), o Kivy
# passa a usá-lo - ele sabe pausar/continuar e pular no tempo; o SDL2 (padrão) não.
# Isso precisa ser decidido ANTES de qualquer import do Kivy. No Android o app
# usa o MediaPlayer do sistema (veja audio.py), então não precisa disso.
if "ANDROID_ARGUMENT" not in os.environ:
    try:
        from ffpyplayer.player import MediaPlayer  # noqa: F401  (só testa se funciona)
        os.environ.setdefault("KIVY_AUDIO", "ffpyplayer")
    except Exception:
        pass

# Tem que vir ANTES de importar a janela: no PC, Esc passa a ser "voltar"
# (tratado em _tecla) em vez de fechar o app na hora.
from kivy.config import Config

Config.set("kivy", "exit_on_escape", "0")

import shutil
from pathlib import Path

from kivy.app import App
from kivy.clock import Clock
from kivy.core.image import Image as CoreImage
from kivy.core.text import LabelBase
from kivy.core.window import Window
from kivy.metrics import dp
from kivy.properties import BooleanProperty, NumericProperty, ObjectProperty, StringProperty
from kivy.resources import resource_add_path, resource_find
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.modalview import ModalView
from kivy.uix.screenmanager import Screen, ScreenManager, SlideTransition
from kivy.uix.widget import Widget
from kivy.utils import platform

import db
import fila
from audio import criar_motor
from db import _sem_acento, listar_playlists, musicas_da_playlist, registrar_reproducao

Window.clearcolor = (0.07, 0.07, 0.07, 1)  # fundo escuro (R, G, B, opacidade de 0 a 1)

if platform in ("win", "linux", "macosx"):
    Window.size = (360, 740)  # no PC, janela com proporção de celular (só para testar)

# Ensina o Kivy a achar "arquivos/icones/play.png" (usado no .kv) a partir da pasta
# do projeto, não importa de onde você rodou o python (antes dependia da pasta atual).
resource_add_path(str(db.BASE_DIR))

# ----------------------------------------------------------------------
# FONTE: DM Sans (regular, medium, bold)
# Coloque os 3 arquivos .ttf em arquivos/fontes/ (nomes abaixo; se os seus
# tiverem outro nome, é só mudar aqui). Se faltar algum, o app usa a fonte
# padrão do Kivy e avisa no terminal - não quebra.
# No .kv: font_name "DMSans" = regular | "DMSansMedium" = medium | bold: True = bold
# ----------------------------------------------------------------------
ARQ_REGULAR = "DMSans-Regular.ttf"
ARQ_MEDIUM = "DMSans-Medium.ttf"
ARQ_BOLD = "DMSans-Bold.ttf"


def registrar_fontes():
    pasta = db.BASE_DIR / "arquivos" / "fontes"
    regular, medium, bold = (pasta / n for n in (ARQ_REGULAR, ARQ_MEDIUM, ARQ_BOLD))
    if regular.exists() and medium.exists() and bold.exists():
        LabelBase.register(name="DMSans", fn_regular=str(regular), fn_bold=str(bold))
        LabelBase.register(name="DMSansMedium", fn_regular=str(medium), fn_bold=str(bold))
    else:
        print(f"AVISO: fontes DM Sans não encontradas em {pasta} - usando Roboto.")
        padrao = resource_find("data/fonts/Roboto-Regular.ttf")
        negrito = resource_find("data/fonts/Roboto-Bold.ttf")
        LabelBase.register(name="DMSans", fn_regular=padrao, fn_bold=negrito)
        LabelBase.register(name="DMSansMedium", fn_regular=padrao, fn_bold=negrito)


registrar_fontes()  # antes de o .kv ser carregado (ele só carrega em MainApp().run())

# ----------------------------------------------------------------------
# ONDE ESTÃO AS COISAS
# RAIZ = pasta que contém 'arquivos/musicas' e 'arquivos/capas_de_album'.
# No PC é a pasta do projeto. No celular, as músicas ficam FORA do APK
# (65 mp3 deixariam o APK enorme) - você copia a pasta para o celular.
# Ícones, fontes e imagens do menu vão DENTRO do APK (caminhos relativos).
# ----------------------------------------------------------------------
if platform == "android":
    RAIZ = Path("/storage/emulated/0/Music/AppMusica")
else:
    RAIZ = db.BASE_DIR


def preparar_banco(pasta_dados):
    """
    No Android, a pasta do app é APAGADA/substituída a cada atualização, e o
    seu histórico estaria lá dentro. Por isso o banco vive em 'user_data_dir'
    (pasta privada e permanente do app). Na 1ª abertura copiamos o musica.db
    que veio no APK para lá.
    """
    pasta_dados = Path(pasta_dados)
    pasta_dados.mkdir(parents=True, exist_ok=True)
    destino = pasta_dados / "musica.db"
    if not destino.exists():
        shutil.copy(db.BASE_DIR / "dados" / "musica.db", destino)
    db.BANCO = destino  # o db.py passa a usar este caminho (ele lê BANCO a cada chamada)


def capa_da_playlist(nome):
    """'Rock Gospel' -> .../arquivos/capa_playlist/rock_gospel.jpg (ou '' se não achar)."""
    slug = _sem_acento(nome).replace(" ", "_")
    for pasta in ("capa_playlist", "capas_de_playlist"):  # aceita os dois nomes de pasta
        for ext in (".jpg", ".jpeg", ".png"):
            caminho = RAIZ / "arquivos" / pasta / f"{slug}{ext}"
            if caminho.exists():
                return str(caminho)
    return ""


def _caminho(relativo):
    """Junta RAIZ com um caminho do banco, aceitando '\\' (banco criado no Windows)."""
    return RAIZ / str(relativo).replace("\\", "/")


def capa_da_musica(musica):
    return str(_caminho(musica["capa"])) if musica and musica.get("capa") else ""


# ----------------------------------------------------------------------
# WIDGETS REUTILIZÁVEIS (o visual deles está no main.kv)
# ----------------------------------------------------------------------
class ImagemArredondada(Widget):
    """
    Imagem com cantos arredondados que PREENCHE o quadrado (corta o excesso,
    como o "cover" do CSS).

    Como funciona: carregamos a imagem como textura, recortamos o miolo na
    proporção do widget (get_region) e entregamos a textura a um
    RoundedRectangle (veja o .kv). Nada de stencil - ele dava imagens
    quebradas dentro das listas com rolagem.
    """
    source = StringProperty("")
    raio = NumericProperty(dp(12))
    tex = ObjectProperty(None, allownone=True)  # textura já recortada (None = sem imagem)

    def __init__(self, **kwargs):
        self._original = None
        self._tentativas = 0
        self._retry = None
        super().__init__(**kwargs)
        self.bind(source=self._ao_mudar_fonte, size=self._recortar)
        self._carregar()

    def _ao_mudar_fonte(self, *_):
        self._tentativas = 0
        self._carregar()

    def _carregar(self, *_):
        if self._retry is not None:
            self._retry.cancel()
            self._retry = None
        self._original = None
        if self.source:
            caminho = resource_find(self.source)
            if caminho:
                try:
                    self._original = CoreImage(caminho).texture
                except Exception as erro:
                    print("imagem: não consegui abrir", caminho, "->", erro)
            if self._original is None and self._tentativas < 6:
                # No Android a permissão de leitura pode chegar depois da tela:
                # tenta de novo algumas vezes antes de desistir.
                self._tentativas += 1
                self._retry = Clock.schedule_once(self._carregar, 1.5)
        self._recortar()

    def _recortar(self, *_):
        original = self._original
        w, h = self.size
        if original is None or w <= 0 or h <= 0:
            self.tex = None
            return
        tw, th = original.size
        if tw / th > w / h:      # imagem mais larga que o espaço: corta as laterais
            cw, ch = th * w / h, th
        else:                    # mais alta: corta em cima e embaixo
            cw, ch = tw, tw * h / w
        cw, ch = max(1, int(round(cw))), max(1, int(round(ch)))
        x, y = (tw - cw) // 2, (th - ch) // 2
        try:
            self.tex = original.get_region(x, y, cw, ch)
        except Exception:
            self.tex = original  # plano B: estica a imagem inteira


class BarraProgresso(Widget):
    """
    Barra de progresso que você pode arrastar.
      progresso = valor de FORA (0 a 1): o app diz o quanto da música já tocou.
      fracao    = o que a barra DESENHA. Segue o 'progresso', exceto enquanto
                  o dedo está arrastando (aí segue o dedo).
    Ao soltar o dedo, pede ao app para pular para aquele ponto da música.
    """
    progresso = NumericProperty(0)
    fracao = NumericProperty(0)
    arrastando = BooleanProperty(False)

    def on_progresso(self, *_):
        if not self.arrastando:
            self.fracao = min(1, max(0, self.progresso))

    def on_touch_down(self, touch):
        if self.collide_point(*touch.pos):
            touch.grab(self)  # "esse toque é meu até soltar"
            self.arrastando = True
            self._seguir(touch)
            return True
        return super().on_touch_down(touch)

    def on_touch_move(self, touch):
        if touch.grab_current is self:
            self._seguir(touch)
            return True
        return super().on_touch_move(touch)

    def on_touch_up(self, touch):
        if touch.grab_current is self:
            touch.ungrab(self)
            self.arrastando = False
            App.get_running_app().buscar(self.fracao)
            # se o pulo não foi possível, a barra volta para onde a música está
            self.fracao = min(1, max(0, self.progresso))
            return True
        return super().on_touch_up(touch)

    def _seguir(self, touch):
        self.fracao = min(1, max(0, (touch.x - self.x) / max(self.width, 1)))


# ----------------------------------------------------------------------
# LINHAS DE LISTA
# ButtonBehavior transforma qualquer widget em "clicável".
# Os nomes (numero, titulo...) são IGUAIS às chaves do dicionário que
# mandamos para o RecycleView: é assim que ele sabe preencher cada linha.
# ----------------------------------------------------------------------
class LinhaMusica(ButtonBehavior, BoxLayout):
    """Uma linha da playlist."""
    numero = NumericProperty(0)
    titulo = StringProperty("")
    artista = StringProperty("")
    capa = StringProperty("")
    indice = NumericProperty(0)      # posição na lista visível (0 = primeira)
    id_musica = NumericProperty(-1)  # para destacar a que está tocando

    def on_release(self):
        App.get_running_app().root.get_screen("playlist").tocar_a_partir_de(int(self.indice))


class LinhaFila(ButtonBehavior, BoxLayout):
    """Uma linha da fila ("A Seguir")."""
    titulo = StringProperty("")
    artista = StringProperty("")
    capa = StringProperty("")
    indice = NumericProperty(0)  # posição ABSOLUTA dentro da fila

    def on_release(self):
        App.get_running_app().tocar_da_fila(int(self.indice))


class CartaoPlaylist(ButtonBehavior, BoxLayout):
    """Uma playlist no menu."""
    playlist_id = NumericProperty(0)
    nome = StringProperty("")
    subtitulo = StringProperty("")
    capa = StringProperty("")

    def on_release(self):
        App.get_running_app().abrir_playlist(int(self.playlist_id))


class OpcoesFila(ModalView):
    """Janelinha que abre ao tocar nas 3 barrinhas de uma música da fila."""
    indice = NumericProperty(0)
    titulo = StringProperty("")
    artista = StringProperty("")

    def acao(self, nome):
        App.get_running_app().acao_fila(nome, int(self.indice))
        self.dismiss()


# ----------------------------------------------------------------------
# TELAS
# ----------------------------------------------------------------------
class TelaMenu(Screen):
    def montar(self, playlists):
        caixa = self.ids.lista_playlists
        caixa.clear_widgets()
        if not playlists:
            caixa.add_widget(Label(text="Sem playlists (rode criar_playlist.py)",
                                   size_hint_y=None, height=dp(40)))
        for p in playlists:
            n = p["total_musicas"]
            caixa.add_widget(CartaoPlaylist(
                playlist_id=p["id"],
                nome=p["nome"],
                subtitulo=f"Playlist - {n} {'Música' if n == 1 else 'Músicas'}",
                capa=capa_da_playlist(p["nome"]),
            ))


class TelaPlaylist(Screen):
    nome_playlist = StringProperty("")
    subtitulo = StringProperty("")
    capa_playlist = StringProperty("")
    busca_aberta = BooleanProperty(False)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.musicas = []   # todas as músicas da playlist
        self.visiveis = []  # as que aparecem (muda quando você busca)

    def carregar(self, playlist):
        self.musicas = musicas_da_playlist(playlist["id"])
        self.nome_playlist = playlist["nome"]
        self.subtitulo = f"Playlist - {playlist['total_musicas']} Músicas"
        self.capa_playlist = capa_da_playlist(playlist["nome"])
        self.busca_aberta = False
        self.ids.campo_busca.text = ""
        self.mostrar(self.musicas)

    def mostrar(self, lista):
        """Entrega a lista ao RecycleView: UMA lista de DICIONÁRIOS."""
        self.visiveis = lista
        self.ids.lista.data = [
            {
                "numero": i + 1,
                "titulo": m["titulo"],
                "artista": m["artista"],
                "capa": capa_da_musica(m),
                "indice": i,
                "id_musica": m["id"],
            }
            for i, m in enumerate(lista)
        ]

    # ---------------- BUSCA ----------------
    def alternar_busca(self):
        self.busca_aberta = not self.busca_aberta
        if not self.busca_aberta:
            self.ids.campo_busca.text = ""
            self.mostrar(self.musicas)  # fechou a busca: volta a lista completa

    def filtrar(self, texto):
        t = _sem_acento(texto)  # ignora acento e maiúscula
        self.mostrar([m for m in self.musicas
                      if t in _sem_acento(m["titulo"] + " " + m["artista"])])

    # ---------------- BOTÕES ----------------
    def tocar_a_partir_de(self, indice):
        App.get_running_app().iniciar_fila(
            self.visiveis, comecar_em=indice, origem=self.nome_playlist)

    def tocar_aleatorio(self):
        App.get_running_app().iniciar_fila(
            self.visiveis, aleatorio=True, origem=self.nome_playlist)


class TelaPlayer(Screen):
    pass  # tudo vem do "app" (veja o main.kv)


class TelaFila(Screen):
    def atualizar(self):
        """Refaz a lista "A Seguir" a partir da fila do app."""
        app = App.get_running_app()
        f = app.fila_atual
        if f is None:
            self.ids.lista.data = []
            return
        base = f["posicao"] + 1  # a 1ª de "A Seguir" é a posição seguinte à atual
        self.ids.lista.data = [
            {
                "titulo": m["titulo"],
                "artista": m["artista"],
                "capa": capa_da_musica(m),
                "indice": base + i,
            }
            for i, m in enumerate(fila.proximas(f))
        ]


# ----------------------------------------------------------------------
# O APP (navegação + player)
# ----------------------------------------------------------------------
OPOSTO = {"left": "right", "right": "left", "up": "down", "down": "up"}


class MainApp(App):
    # Estado do player. O .kv lê com "app.nome" e se atualiza sozinho.
    id_atual = NumericProperty(-1)
    titulo_atual = StringProperty("")
    artista_atual = StringProperty("")
    capa_atual = StringProperty("")
    origem = StringProperty("")           # nome da playlist que está tocando
    tem_musica = BooleanProperty(False)   # False = esconde o mini player
    tocando = BooleanProperty(False)      # True = tocando, False = pausado
    aleatorio = BooleanProperty(False)
    posicao = NumericProperty(0)          # segundos já tocados
    duracao = NumericProperty(0)          # segundos totais

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.playlists = []
        self.lista_origem = []   # músicas na ordem da playlist (para desligar o aleatório)
        self.fila_atual = None   # dicionário criado pelo fila.criar_fila
        self.motor = criar_motor()  # motor de áudio (audio.py): Android ou Kivy
        self.carregado = False      # True = há uma música aberta no motor (tocando ou pausada)
        self.musica_tocando = None
        self.pilha = []          # telas por onde passamos (para o botão voltar)

    def build(self):
        if platform == "android":
            from android.permissions import request_permissions  # só existe no Android
            request_permissions([
                "android.permission.READ_MEDIA_AUDIO",    # Android 13+
                "android.permission.READ_MEDIA_IMAGES",   # capas, Android 13+
                "android.permission.READ_EXTERNAL_STORAGE",  # Android 12 e antes
            ], self._permissoes_respondidas)
            preparar_banco(self.user_data_dir)

        gerenciador = ScreenManager(transition=SlideTransition(duration=0.2))
        for classe, nome in ((TelaMenu, "menu"), (TelaPlaylist, "playlist"),
                             (TelaPlayer, "player"), (TelaFila, "fila")):
            gerenciador.add_widget(classe(name=nome))
        Window.bind(on_keyboard=self._tecla)
        return gerenciador  # o "root" do app

    def _permissoes_respondidas(self, permissoes, concedidas):
        # Pode ser chamado fora da thread do Kivy: o Clock traz de volta.
        Clock.schedule_once(lambda dt: self._recarregar_capas(), 0)

    def _recarregar_capas(self):
        """Antes da permissão o Android escondia as capas (exists() dava False): refaz."""
        if self.root is None or not self.playlists:
            return  # ainda não terminou de abrir; o on_start monta tudo
        self.root.get_screen("menu").montar(self.playlists)
        tela = self.root.get_screen("playlist")
        if tela.nome_playlist:
            tela.capa_playlist = capa_da_playlist(tela.nome_playlist)

    def on_start(self):
        self.playlists = listar_playlists()
        self.root.get_screen("menu").montar(self.playlists)
        Clock.schedule_interval(self._tick, 0.25)  # atualiza a barra de progresso

    def on_pause(self):
        return True  # sem isso o Android mata o app ao bloquear a tela

    # ---------------- NAVEGAÇÃO ----------------
    def ir(self, tela, direcao="left"):
        """Vai para uma tela. 'direcao' = de onde ela entra (left/right/up/down)."""
        if self.root.current == tela:
            return
        self.pilha.append((self.root.current, direcao))
        self.root.transition.direction = direcao
        self.root.current = tela

    def voltar_tela(self):
        """Volta para a tela anterior com a animação ao contrário. False se não há para onde."""
        if not self.pilha:
            return False
        anterior, direcao = self.pilha.pop()
        self.root.transition.direction = OPOSTO[direcao]
        self.root.current = anterior
        return True

    def _tecla(self, janela, tecla, *args):
        if tecla == 27:  # botão voltar do Android / Esc no PC
            if not self.voltar_tela():
                self.stop()  # já está no menu: fecha o app
            return True
        return False

    def abrir_playlist(self, playlist_id):
        for p in self.playlists:
            if p["id"] == playlist_id:
                self.root.get_screen("playlist").carregar(p)
                self.ir("playlist")
                return

    # ---------------- FILA ----------------
    def iniciar_fila(self, musicas, comecar_em=None, aleatorio=False, origem=""):
        if not musicas:
            return
        self.lista_origem = list(musicas)
        self.fila_atual = fila.criar_fila(musicas, comecar_em=comecar_em, aleatorio=aleatorio)
        self.aleatorio = aleatorio
        self.origem = origem
        self._tocar(fila.musica_atual(self.fila_atual))

    def tocar_da_fila(self, indice):
        """Tocou numa música de 'A Seguir': ela vira a atual."""
        if self.fila_atual is None:
            return
        self.fila_atual["posicao"] = indice
        self._tocar(fila.musica_atual(self.fila_atual))

    def alternar_aleatorio(self):
        f = self.fila_atual
        if f is None:
            return
        if f["aleatorio"]:
            fila.desfazer_aleatorio(f, self.lista_origem)
        else:
            fila.embaralhar_restante(f)
        self.aleatorio = f["aleatorio"]
        self._atualizar_fila()

    def abrir_opcoes_fila(self, indice):
        f = self.fila_atual
        if f is None or not (0 <= indice < len(f["itens"])):
            return
        m = f["itens"][indice]
        OpcoesFila(indice=indice, titulo=m["titulo"], artista=m["artista"]).open()

    def acao_fila(self, nome, indice):
        f = self.fila_atual
        if f is None:
            return
        if nome == "seguir":
            fila.mover(f, indice, f["posicao"] + 1)
        elif nome == "subir" and indice - 1 > f["posicao"]:
            fila.mover(f, indice, indice - 1)
        elif nome == "descer" and indice + 1 < len(f["itens"]):
            fila.mover(f, indice, indice + 1)
        elif nome == "remover":
            fila.remover(f, indice)
        self._atualizar_fila()

    def _atualizar_fila(self):
        self.root.get_screen("fila").atualizar()

    # ---------------- BOTÕES DO PLAYER ----------------
    def pular(self):
        if self.fila_atual is None:
            return
        seguinte = fila.proxima(self.fila_atual)
        if seguinte:
            self._tocar(seguinte)
        else:
            self._parar_som()
            self.tocando = False
            self.posicao = 0
            self._atualizar_fila()

    def faixa_anterior(self):
        if self.fila_atual is None:
            return
        if self.posicao > 3:  # já tocou um pouco: "voltar" reinicia a música (como no Spotify)
            self._tocar(fila.musica_atual(self.fila_atual))
        else:
            self._tocar(fila.anterior(self.fila_atual))

    def pausar_ou_retomar(self):
        if not self.carregado:
            if self.fila_atual is not None:  # a fila tinha acabado: recomeça a última
                self._tocar(fila.musica_atual(self.fila_atual))
            return
        if self.tocando:
            self.motor.pausar()  # o motor guarda ONDE parou
            self.posicao = self.motor.posicao()
            self.tocando = False
        else:
            self.motor.retomar()  # continua de onde parou
            self.posicao = self.motor.posicao()
            self.tocando = True

    def buscar(self, fracao):
        """Arrastou a barra: pula para 'fracao' (0 a 1) da música."""
        if not self.carregado or not self.duracao:
            return
        segundos = fracao * self.duracao
        if self.motor.buscar(segundos):
            self.posicao = segundos

    def formatar_tempo(self, segundos):
        segundos = int(max(0, segundos or 0))
        return f"{segundos // 60}:{segundos % 60:02d}"

    # ---------------- PLAYER ----------------
    def _tocar(self, musica):
        self._parar_som()  # encerra a anterior (e grava no histórico)
        self.tem_musica = True
        self.musica_tocando = musica
        self.id_atual = musica["id"]
        self.titulo_atual = musica["titulo"]
        self.artista_atual = musica["artista"]
        self.capa_atual = capa_da_musica(musica)
        self.posicao = 0
        self.duracao = musica.get("duracao_seg") or 0

        if not self.motor.carregar(_caminho(musica["arquivo"])):  # False se não abrir
            self.carregado = False
            self.tocando = False
            self.titulo_atual = f"Não achei: {musica['arquivo']}"
            self._atualizar_fila()
            return
        self.carregado = True
        self.motor.tocar()
        self.tocando = True
        if not self.duracao:
            self.duracao = self.motor.duracao()  # plano B: pergunta ao próprio áudio
        self._atualizar_fila()

    def _tick(self, dt):
        """4x por segundo: lê a posição (a barra de progresso se move sozinha)
        e confere se a música acabou."""
        if not self.carregado or not self.tocando:
            return
        self.posicao = self.motor.posicao()
        if not self.duracao:
            self.duracao = self.motor.duracao()
        if self.motor.terminou():
            self._terminou()

    def _registrar(self, segundos, completa):
        """Só entra no histórico se ouviu >= 30 s ou >= metade (regra do db.py)."""
        m = self.musica_tocando
        if m is None:
            return
        duracao = m.get("duracao_seg") or 0
        if segundos >= 30 or (duracao and segundos >= duracao / 2):
            registrar_reproducao(m["id"], segundos, completa)

    def _parar_som(self):
        """Parada MANUAL (pular, voltar, trocar de música)."""
        if not self.carregado:
            return
        segundos = int(self.motor.posicao())  # (pausada ou não, é o quanto ouviu)
        self.motor.parar()
        self.carregado = False
        self._registrar(segundos, completa=False)

    def _terminou(self):
        m = self.musica_tocando
        self.motor.parar()
        self.carregado = False
        self._registrar(m.get("duracao_seg") or 0, completa=True)
        self.pular()  # emenda na próxima da fila

    def on_stop(self):
        self.motor.parar()


if __name__ == "__main__":
    MainApp().run()
