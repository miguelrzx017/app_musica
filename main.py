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

import faulthandler
import hashlib
import json
import os
import time
from functools import lru_cache
from datetime import datetime, timedelta

# ----------------------------------------------------------------------
# DIAGNÓSTICO DE QUEDAS (app que "fecha sozinho")
# O RegistradorDeErros (mais abaixo) só pega erros de PYTHON. Quando o app morre
# por um erro NATIVO (áudio, imagem, falta de memória), nada é registrado.
#  1) faulthandler: se houver erro nativo, o Python imprime onde estava
#     (PC: no terminal | Android: no logcat, tag "python").
#  2) trilha(): escreve cada passo importante do player (com a memória usada).
#     Depois de uma queda, a ÚLTIMA linha da trilha diz o que estava
#     acontecendo. Arquivo: trilha.log (PC: pasta do projeto | Android:
#     pasta privada do app). No Android também sai no logcat:
#         adb logcat -s python
# ----------------------------------------------------------------------
try:
    faulthandler.enable(all_threads=True)
except (OSError, RuntimeError, ValueError) as erro:
    # No Android, stderr do python-for-android pode não expor fileno().
    # O faulthandler é apenas diagnóstico e não deve impedir o app de abrir.
    print(f"AVISO: faulthandler indisponível nesta plataforma: {erro}")

_PASTA_LOG = os.environ.get("ANDROID_PRIVATE") or os.path.dirname(os.path.abspath(__file__))
_ARQ_TRILHA = os.path.join(_PASTA_LOG, "trilha.log")
_T_INICIO = time.time()


def _memoria_mb():
    """Memória usada pelo app (MB). Funciona no Linux/Android; no Windows devolve 0."""
    try:
        with open("/proc/self/status") as f:
            for linha in f:
                if linha.startswith("VmRSS:"):
                    return int(linha.split()[1]) // 1024
    except Exception:
        pass
    return 0


def trilha(texto):
    linha = f"[{time.time() - _T_INICIO:8.2f}s | {_memoria_mb():4d} MB] {texto}"
    print("TRILHA", linha, flush=True)
    try:
        # modo "w" na 1ª linha: o arquivo guarda só a execução mais recente
        modo = "a" if getattr(trilha, "_aberta", False) else "w"
        trilha._aberta = True
        with open(_ARQ_TRILHA, modo, encoding="utf-8") as f:
            f.write(linha + "\n")
            f.flush()
            os.fsync(f.fileno())  # força gravar no disco AGORA (sobrevive a uma queda)
    except Exception:
        pass


trilha("app iniciando")

# ÁUDIO NO PC: o motor é o pygame.mixer.music (veja audio.py, classe MotorPygame):
#     pip install pygame
# O ffpyplayer NÃO é mais usado: no Windows ele derrubava o app (access violation
# dentro de MediaPlayer(...)) ao abrir a segunda música. Se o pygame faltar, o app
# cai no áudio básico do Kivy, forçado para SDL2 (estável, mas sem pausar/pular).
# No Android, a sessão Media3 reproduz a fila e mantém os controles na notificação.
# (Precisa ser decidido ANTES de qualquer import do Kivy.)
if "ANDROID_ARGUMENT" not in os.environ:
    os.environ["KIVY_AUDIO"] = "sdl2"

# Tem que vir ANTES de importar a janela: no PC, Esc passa a ser "voltar"
# (tratado em _tecla) em vez de fechar o app na hora.
from kivy.config import Config

Config.set("kivy", "exit_on_escape", "0")

import shutil
from pathlib import Path

from kivy.app import App
from kivy.base import ExceptionHandler, ExceptionManager
from kivy.clock import Clock
from kivy.core.image import Image as CoreImage
from kivy.core.text import LabelBase
from kivy.core.window import Window
from kivy.metrics import dp
from kivy.properties import (BooleanProperty, ListProperty, NumericProperty,
                             ObjectProperty, StringProperty)
from kivy.resources import resource_add_path, resource_find
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.filechooser import FileChooserIconView
from kivy.uix.label import Label
from kivy.uix.modalview import ModalView
from kivy.uix.recycleboxlayout import RecycleBoxLayout
from kivy.uix.recycleview import RecycleView
from kivy.uix.screenmanager import Screen, ScreenManager, SlideTransition
from kivy.uix.scrollview import ScrollView
from kivy.uix.widget import Widget
from kivy.utils import platform

import db
import fila
from auth import AuthStore
from audio import criar_motor
from db import (_sem_acento, atualizar_reproducao, estatisticas_artistas, estatisticas_musicas,
                listar_albuns, listar_musicas, listar_playlists,
                musicas_da_playlist, registrar_reproducao, resumo_estatisticas)
from biblioteca import musicas as BIBLIOTECA_MUSICAS
from biblioteca import obter_artista
from perfis_artistas import artistas_ordenados, logo_do_artista, montar_perfil, nome_na_tela

Window.clearcolor = (0.07, 0.07, 0.07, 1)  # fundo escuro (R, G, B, opacidade de 0 a 1)

if platform in ("win", "linux", "macosx"):
    Window.size = (360, 740)  # no PC, janela com proporção de celular (só para testar)

# Ensina o Kivy a achar "arquivos/icones/play.png" (usado no .kv) a partir da pasta
# do projeto, não importa de onde você rodou o python (antes dependia da pasta atual).
resource_add_path(str(db.BASE_DIR))

# ----------------------------------------------------------------------
# QUANDO A FILA ACABA
# A fila é refeita sozinha: com o aleatório ligado ela é embaralhada de novo;
# desligado, volta ao começo da playlist (veja fila.proxima / fila.reiniciar).
#   True  -> a música seguinte já toca sozinha (a playlist "dá a volta")
#   False -> a fila é refeita, mas o app para na 1ª música, esperando o play
# ----------------------------------------------------------------------
TOCAR_AO_REINICIAR_FILA = True


# ----------------------------------------------------------------------
# REGISTRO DE ERROS
# Sem isso, qualquer erro de Python dentro de um botão FECHA o app sem dizer
# nada. Agora o erro é escrito no terminal e no arquivo erro.log (no PC fica na
# pasta do projeto; no celular, na pasta privada do app - dá para ler com
# "adb logcat -s python") e o app continua aberto.
# ----------------------------------------------------------------------
def _arquivo_de_erros():
    pasta = os.environ.get("ANDROID_PRIVATE") or str(db.BASE_DIR)
    return Path(pasta) / "erro.log"


class RegistradorDeErros(ExceptionHandler):
    def handle_exception(self, excecao):
        import datetime
        import traceback

        texto = "".join(traceback.format_exception(
            type(excecao), excecao, excecao.__traceback__))
        print("ERRO NO APP:\n" + texto)
        try:
            with open(_arquivo_de_erros(), "a", encoding="utf-8") as f:
                f.write(f"\n=== {datetime.datetime.now():%Y-%m-%d %H:%M:%S} ===\n{texto}")
        except Exception:
            pass
        return ExceptionManager.PASS  # PASS = não fecha o app


ExceptionManager.add_handler(RegistradorDeErros())

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


def _caminho(relativo):
    """Junta RAIZ com um caminho do banco, aceitando '\\' (banco criado no Windows)."""
    return RAIZ / str(relativo).replace("\\", "/")


def _achar(relativo):
    """
    Devolve o caminho de um arquivo do projeto ("arquivos/Artistas/pg.jpg").
    Procura primeiro em RAIZ (no celular: a pasta Music/AppMusica) e, se não
    achar, dentro do próprio app (os .jpg/.png também vão no APK). Se não
    existir em lugar nenhum, devolve "" (a interface mostra o fundo cinza).
    """
    if not relativo:
        return ""
    relativo = str(relativo).replace("\\", "/")
    for base in (RAIZ, db.BASE_DIR):
        caminho = Path(base) / relativo
        if caminho.exists():
            return str(caminho)
    return ""


def capa_da_playlist(nome):
    """'Rock Gospel' -> .../arquivos/capa_playlist/rock_gospel.jpg (ou '' se não achar)."""
    slug = _sem_acento(nome).replace(" ", "_")
    for pasta in ("capa_playlist", "capas_de_playlist"):  # aceita os dois nomes de pasta
        for ext in (".jpg", ".jpeg", ".png"):
            achado = _achar(f"arquivos/{pasta}/{slug}{ext}")
            if achado:
                return achado
    return ""


def capa_da_musica(musica):
    return _achar(musica.get("capa")) if musica else ""


# Dados extras de cada música que não ficam no banco (só no biblioteca.py):
# gêneros e, se você quiser, as chaves opcionais 'compositores' e 'fontes'.
_BIBLIOTECA_POR_ARQUIVO = {
    str(m.get("arquivo", "")).replace("\\", "/"): m for m in BIBLIOTECA_MUSICAS.values()
}


def _texto(valor):
    """Aceita texto ou lista ('PG', 'Juninho') e devolve sempre um texto."""
    if isinstance(valor, (list, tuple)):
        return ", ".join(str(v) for v in valor if v)
    return str(valor).strip() if valor else ""


def linhas_sobre_a_musica(musica):
    """Texto do cartão 'Sobre a música'. Só aparece o que existe para a faixa."""
    extra = _BIBLIOTECA_POR_ARQUIVO.get(str(musica.get("arquivo", "")).replace("\\", "/"), {})
    generos = ", ".join(str(g).capitalize() for g in extra.get("generos", []))
    linhas = [
        ("Ano de lançamento", musica.get("ano")),
        ("Álbum", musica.get("album")),
        ("Compositores", _texto(extra.get("compositores"))),
        ("Gênero", generos),
        ("Fontes", _texto(extra.get("fontes"))),
    ]
    return "\n".join(f"{rotulo}: {valor}" for rotulo, valor in linhas if valor)


# ----------------------------------------------------------------------
# WIDGETS REUTILIZÁVEIS (o visual deles está no main.kv)
# ----------------------------------------------------------------------
@lru_cache(maxsize=24)
def _textura_compartilhada(caminho):
    """Reutiliza texturas recentes sem manter todo o catálogo em memória."""
    return CoreImage(caminho).texture


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
    # Para arredondar só alguns cantos: raios: [sup.esq, sup.dir, inf.dir, inf.esq]
    raios = ListProperty([])
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
                    self._original = _textura_compartilhada(caminho)
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
        cw, ch = min(tw, max(1, int(round(cw)))), min(th, max(1, int(round(ch))))
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

    def on_touch_down(self, touch):
        # As barrinhas à direita indicam a alça; o resto da linha continua
        # disponível para rolar a lista e tocar na música.
        if self.collide_point(*touch.pos) and touch.x >= self.right - dp(52):
            self._toque_fila = touch
            self._inicio_arraste = touch.pos
            self._arrastando = False
            resultado = super().on_touch_down(touch)
            touch.grab(self)
            return resultado or True
        return super().on_touch_down(touch)

    def on_touch_move(self, touch):
        if getattr(self, "_toque_fila", None) is touch and touch.grab_current is self:
            dx = touch.x - self._inicio_arraste[0]
            dy = touch.y - self._inicio_arraste[1]
            if not self._arrastando and (dx * dx + dy * dy) ** .5 > dp(12):
                self._arrastando = True
            if self._arrastando:
                # Depois que o gesto vira arraste, ele não pode terminar como
                # um clique do ButtonBehavior (nem depender do estado do toque).
                touch.ud.pop(self, None)
                self.state = "normal"
                return True
        return super().on_touch_move(touch)

    def on_touch_up(self, touch):
        if getattr(self, "_toque_fila", None) is touch:
            arrastando = self._arrastando
            self._toque_fila = None
            self._arrastando = False
            if arrastando:
                if touch.grab_current is self:
                    touch.ungrab(self)
                touch.ud.pop(self, None)
                self.state = "normal"
                alvos = [w for w in self.parent.children if isinstance(w, LinhaFila)] if self.parent else []
                if alvos:
                    alvo = min(alvos, key=lambda w: abs(w.center_y - touch.y))
                    App.get_running_app().mover_fila(int(self.indice), int(alvo.indice))
                return True
            # ButtonBehavior exige que o toque esteja registrado em touch.ud.
            # Alguns eventos do Android podem chegar sem esse registro; nesse
            # caso, encerra o gesto sem chamar a rotina que dispara AssertionError.
            if self in touch.ud and touch.grab_current is self:
                return super().on_touch_up(touch)
            if touch.grab_current is self:
                touch.ungrab(self)
                self.state = "normal"
                return True
            return super().on_touch_up(touch)
        # Protege também os toques fora da alça: o ButtonBehavior só pode
        # finalizar um toque se ele próprio o registrou no início.
        if touch.grab_current is self and self not in touch.ud:
            touch.ungrab(self)
            self.state = "normal"
            return True
        return super().on_touch_up(touch)

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


class LinhaArtista(ButtonBehavior, BoxLayout):
    """Um artista na lista 'Artistas' (fotinho redonda + nome)."""
    artista_id = NumericProperty(0)
    nome = StringProperty("")
    logo = StringProperty("")

    def on_release(self):
        App.get_running_app().abrir_artista(int(self.artista_id))


class CapaAlbum(ButtonBehavior, ImagemArredondada):
    """Capa quadrada da discografia (o tamanho está no .kv). Tocar abre o álbum."""
    album_id = NumericProperty(0)

    def on_release(self):
        App.get_running_app().abrir_album(int(self.album_id))


class LinhaMusicaAlbum(ButtonBehavior, BoxLayout):
    """Uma música na página do álbum (título + artista)."""
    titulo = StringProperty("")
    artista = StringProperty("")
    indice = NumericProperty(0)       # posição no álbum (0 = primeira)
    id_musica = NumericProperty(-1)   # para destacar a que está tocando

    def on_release(self):
        App.get_running_app().root.get_screen("album").tocar_a_partir_de(int(self.indice))


class ParagrafoBio(Label):
    """Um parágrafo da biografia (o visual está no .kv)."""


class LinhaInfo(Label):
    """Uma linha do cartão 'Integrantes' (o visual está no .kv)."""


class SetaCirculo(Widget):
    """Bolinha branca com seta: para a direita em 'Ver mais', para cima em 'Ver menos'."""
    aberto = BooleanProperty(False)


class OpcoesFila(ModalView):
    """Janelinha que abre ao tocar nas 3 barrinhas de uma música da fila."""
    indice = NumericProperty(0)
    titulo = StringProperty("")
    artista = StringProperty("")

    def acao(self, nome):
        App.get_running_app().acao_fila(nome, int(self.indice))
        self.dismiss()


class PerfilModal(ModalView):
    """Opções da conta aberta no aparelho."""


class SeletorFotoPerfil(ModalView):
    """Seletor local para escolher uma imagem de perfil."""

    pasta_inicial = StringProperty("")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.pasta_inicial = "/storage/emulated/0" if platform == "android" else str(Path.home())

    def escolher(self):
        selecao = self.ids.arquivos.selection
        if selecao:
            App.get_running_app().definir_foto_perfil(selecao[0])
            self.dismiss()


EXT_AUDIO = (".mp3", ".m4a", ".ogg", ".wav", ".flac", ".aac")
EXT_IMAGEM = (".png", ".jpg", ".jpeg", ".webp")
NOVO_ARTISTA = "+ Novo artista"
NOVO_ALBUM = "+ Novo álbum"


class LinhaAdicionar(ButtonBehavior, BoxLayout):
    """Última linha da playlist: abre o formulário de nova música."""

    def on_release(self):
        App.get_running_app().root.get_screen("playlist").abrir_adicionar()


class SeletorArquivo(ModalView):
    """Escolhe um arquivo (áudio ou imagem) e chama 'ao_escolher(caminho)'."""

    pasta_inicial = StringProperty("")
    titulo = StringProperty("Escolher arquivo")
    filtros = ListProperty([])

    def __init__(self, titulo, extensoes, ao_escolher, **kwargs):
        super().__init__(**kwargs)
        self.titulo = titulo
        self.filtros = [f"*{e}" for e in extensoes] + [f"*{e.upper()}" for e in extensoes]
        self._ao_escolher = ao_escolher
        self.pasta_inicial = "/storage/emulated/0" if platform == "android" else str(Path.home())

    def escolher(self):
        selecao = self.ids.arquivos.selection
        if selecao:
            self.dismiss()
            self._ao_escolher(selecao[0])


class AdicionarMusicaModal(ModalView):
    """Formulário: arquivo, título, artista, álbum (existentes ou novos), capa, ano, gêneros."""

    arquivo = StringProperty("")
    capa = StringProperty("")
    nome_arquivo = StringProperty("Escolher arquivo de áudio")
    novo_artista = BooleanProperty(False)
    novo_album = BooleanProperty(True)
    artistas = ListProperty([])
    albuns = ListProperty([NOVO_ALBUM])
    erro = StringProperty("")

    def __init__(self, playlist_id, **kwargs):
        super().__init__(**kwargs)
        self.playlist_id = playlist_id
        self._ids_artistas = {}
        for a in db.listar_artistas():
            self._ids_artistas[a["nome"]] = a["id"]
        self.artistas = sorted(self._ids_artistas, key=_sem_acento) + [NOVO_ARTISTA]
        Clock.schedule_once(lambda dt: self._iniciar(), 0)

    def _iniciar(self):
        self.ids.sp_artista.text = self.artistas[0]
        self.ao_mudar_artista(self.artistas[0])

    def ao_mudar_artista(self, texto):
        self.novo_artista = texto == NOVO_ARTISTA
        nomes = []
        if not self.novo_artista and texto in self._ids_artistas:
            nomes = [x["nome"] for x in db.listar_albuns(self._ids_artistas[texto])]
        self.albuns = nomes + [NOVO_ALBUM]
        self.ids.sp_album.text = self.albuns[0]
        self.ao_mudar_album(self.albuns[0])

    def ao_mudar_album(self, texto):
        self.novo_album = texto == NOVO_ALBUM

    def escolher_audio(self):
        SeletorArquivo("Escolher música", EXT_AUDIO, self._audio_escolhido).open()

    def _audio_escolhido(self, caminho):
        self.arquivo = caminho
        self.nome_arquivo = Path(caminho).name
        if not self.ids.titulo.text.strip():
            self.ids.titulo.text = Path(caminho).stem.replace("_", " ").replace("-", " ").strip().title()

    def escolher_capa(self):
        SeletorArquivo("Escolher capa do álbum", EXT_IMAGEM, lambda c: setattr(self, "capa", c)).open()

    def salvar(self):
        ids = self.ids
        artista = ids.nome_artista.text if self.novo_artista else ids.sp_artista.text
        album = ids.nome_album.text if self.novo_album else ids.sp_album.text
        self.erro = App.get_running_app().salvar_nova_musica(
            self.playlist_id, self.arquivo, ids.titulo.text, artista, album,
            ids.ano.text, self.capa, [g for g in ids.generos.text.split(",")],
        )
        if not self.erro:
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


class TelaAcesso(Screen):
    """Tela inicial de cadastro e login local."""
    modo_cadastro = BooleanProperty(True)
    mensagem = StringProperty("")

    def selecionar_modo(self, cadastro):
        self.modo_cadastro = bool(cadastro)
        self.mensagem = ""
        self.ids.nome.text = ""
        self.ids.senha.text = ""


class LinhaEstatistica(BoxLayout):
    numero = NumericProperty(0)
    titulo = StringProperty("")
    detalhe = StringProperty("")
    valor = StringProperty("")
    capa = StringProperty("")


class BotaoSegmentado(ButtonBehavior, Label):
    selecionado = BooleanProperty(False)


class CartaoResumo(BoxLayout):
    rotulo = StringProperty("")
    valor = StringProperty("")


class TelaEstatisticas(Screen):
    aba = StringProperty("musicas")
    periodo = StringProperty("semana")
    mensagem = StringProperty("")
    capturando = BooleanProperty(False)
    nome_periodo = StringProperty("")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._auto = None
        self._assinatura = None
        self._musicas = []
        self._artistas = []
        self._resumo = {}

    def on_enter(self, *args):
        # Escutas feitas em segundo plano (serviço nativo) aparecem sem reabrir a tela.
        if self._auto is None:
            self._auto = Clock.schedule_interval(lambda dt: self.atualizar(), 5)

    def on_leave(self, *args):
        if self._auto is not None:
            self._auto.cancel()
            self._auto = None

    def selecionar_aba(self, aba):
        self.aba = aba
        self.atualizar()

    def selecionar_periodo(self, periodo):
        self.periodo = periodo
        self.atualizar()

    def _periodo_desde(self):
        agora = datetime.now()
        if self.periodo == "semana":
            return (agora - timedelta(days=7)).isoformat(timespec="seconds")
        if self.periodo == "mes":
            return (agora - timedelta(days=30)).isoformat(timespec="seconds")
        return datetime(agora.year, 1, 1).isoformat(timespec="seconds")

    def _nome_periodo(self):
        return {
            "semana": "Últimos 7 dias",
            "mes": "Últimos 30 dias",
            "ano": f"Ano de {datetime.now().year}",
        }[self.periodo]

    def atualizar(self):
        if "lista_estatisticas" not in self.ids:
            return
        usuario = App.get_running_app().usuario_atual
        desde = self._periodo_desde()
        self.nome_periodo = self._nome_periodo()
        self._musicas = estatisticas_musicas(usuario, desde, limite=50)
        self._artistas = estatisticas_artistas(usuario, desde, limite=50)
        self._resumo = resumo_estatisticas(usuario, desde)
        caixa = self.ids.lista_estatisticas
        assinatura = (usuario, self.aba, self.periodo,
                      tuple((x["id"], x["reproducoes"], x["segundos_ouvidos"]) for x in self._musicas),
                      tuple((x["id"], x["reproducoes"], x["segundos_ouvidos"]) for x in self._artistas),
                      tuple(sorted(self._resumo.items())))
        if assinatura == self._assinatura and caixa.children:
            return  # nada mudou: não refaz a lista (evita piscar e perder a rolagem)
        caixa.clear_widgets()
        self.mensagem = ""

        if self.aba == "musicas":
            linhas = self._musicas
            dados = [
                {
                    "numero": posicao,
                    "titulo": item["titulo"],
                    "detalhe": item["artista"],
                    "valor": f"{item['reproducoes']} {'reprodução' if item['reproducoes'] == 1 else 'reproduções'}",
                    "capa": _achar(item.get("capa")),
                }
                for posicao, item in enumerate(linhas, 1)
            ]
        elif self.aba == "artistas":
            linhas = self._artistas
            dados = []
            for posicao, item in enumerate(linhas, 1):
                artista = {"id": item["id"], "nome": item["nome"]}
                dados.append({
                    "numero": posicao,
                    "titulo": item["nome"],
                    "detalhe": f"{item['musicas_diferentes']} músicas diferentes",
                    "valor": f"{item['reproducoes']} {'reprodução' if item['reproducoes'] == 1 else 'reproduções'}",
                    "capa": logo_do_artista(artista, _achar),
                })
        else:
            dados = None
            resumo = self._resumo
            segundos = int(resumo.get("segundos_ouvidos") or 0)
            tempo = f"{segundos // 3600} h {(segundos % 3600) // 60} min"
            metricas = (
                ("Reproduções (1 min ou mais)", str(resumo.get("reproducoes", 0))),
                ("Músicas diferentes", str(resumo.get("musicas_diferentes", 0))),
                ("Artistas ouvidos", str(resumo.get("artistas_diferentes", 0))),
                ("Tempo ouvindo", tempo),
                ("Faixas completas", str(resumo.get("completas", 0))),
            )
            scroll = ScrollView(do_scroll_x=False, bar_width=dp(3))
            cartoes = BoxLayout(
                orientation="vertical", size_hint_y=None, spacing=dp(8)
            )
            cartoes.bind(minimum_height=cartoes.setter("height"))
            for rotulo, valor in metricas:
                cartoes.add_widget(CartaoResumo(rotulo=rotulo, valor=valor))
            scroll.add_widget(cartoes)
            caixa.add_widget(scroll)

        if dados is not None:
            if not dados:
                caixa.add_widget(self._aviso_vazio(usuario, desde))
            else:
                rolagem = ScrollView(do_scroll_x=False, bar_width=dp(3))
                coluna = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(8))
                coluna.bind(minimum_height=coluna.setter("height"))
                for dado in dados:
                    coluna.add_widget(LinhaEstatistica(**dado))
                rolagem.add_widget(coluna)
                caixa.add_widget(rolagem)
        self._assinatura = assinatura

    def _aviso_vazio(self, usuario, desde):
        total, validas, todos = db.contagem_sessoes(usuario, desde)
        if total:
            texto = (f"{total} escuta(s) registrada(s), mas nenhuma chegou a 1 minuto.\n"
                     "Ouça uma música por pelo menos 1 minuto para ela contar.")
        elif todos:
            texto = "Há escutas de outra conta neste período.\nEntre nela para ver os números."
        else:
            texto = "Ainda não há reproduções neste período.\nOuça uma música por pelo menos 1 minuto."
        aviso = Label(text=texto, color=(.55, .55, .55, 1), halign="center", valign="middle")
        aviso.bind(size=lambda w, s: setattr(w, "text_size", s))
        return aviso

    def gerar_captura(self):
        if self.capturando:
            return
        self.capturando = True
        try:
            self._preparar_captura()
        except Exception as erro:
            self.capturando = False
            trilha(f"falha ao preparar captura das estatísticas: {erro!r}")
            self.mensagem = f"Falha ao preparar a captura ({type(erro).__name__})."

    def _preparar_captura(self):
        from kivy.graphics import Color, Rectangle
        app = App.get_running_app()
        self.atualizar()
        self.mensagem = "Gerando captura…"
        resumo = self._resumo
        segundos = int(resumo.get("segundos_ouvidos") or 0)
        tempo = f"{segundos // 3600} h {(segundos % 3600) // 60} min"
        plural = lambda n: "reprodução" if n == 1 else "reproduções"

        if self.aba == "geral":
            nome_aba = "Geral"
            linhas = [
                ("Reproduções (1 min ou mais)", "", str(resumo.get("reproducoes", 0))),
                ("Músicas diferentes", "", str(resumo.get("musicas_diferentes", 0))),
                ("Artistas ouvidos", "", str(resumo.get("artistas_diferentes", 0))),
                ("Tempo ouvindo", "", tempo),
                ("Faixas completas", "", str(resumo.get("completas", 0))),
            ]
        elif self.aba == "artistas":
            nome_aba = "Artistas"
            linhas = [(f"{i}. {nome_na_tela(x['nome'])}", f"{x['musicas_diferentes']} músicas diferentes",
                       f"{x['reproducoes']} {plural(x['reproducoes'])}")
                      for i, x in enumerate(self._artistas[:10], 1)]
        else:
            nome_aba = "Músicas"
            linhas = [(f"{i}. {x['titulo']}", x["artista"],
                       f"{x['reproducoes']} {plural(x['reproducoes'])}")
                      for i, x in enumerate(self._musicas[:10], 1)]
        if not linhas:
            linhas = [("Ainda não há reproduções neste período.", "", "")]

        largura, margem = 1080, 60
        relatorio = BoxLayout(
            orientation="vertical", size_hint=(None, None),
            size=(largura, 420 + 124 * len(linhas)),
            padding=(margem, 56, margem, 40), spacing=10,
        )
        with relatorio.canvas.before:
            Color(.07, .07, .07, 1)
            fundo = Rectangle(pos=relatorio.pos, size=relatorio.size)
        relatorio.bind(pos=lambda w, v: setattr(fundo, "pos", v),
                       size=lambda w, v: setattr(fundo, "size", v))

        def rotulo(texto, tamanho, altura, cor=(1, 1, 1, 1), alinhar="left", negrito=False):
            r = Label(text=texto, font_size=tamanho, color=cor, bold=negrito,
                      halign=alinhar, valign="middle", size_hint_y=None, height=altura,
                      shorten=True, shorten_from="right")
            r.bind(size=lambda w, s: setattr(w, "text_size", s))
            return r

        relatorio.add_widget(rotulo("My Music Gospel", 58, 96, (.91, .12, .12, 1), negrito=True))
        relatorio.add_widget(rotulo(f"Estatísticas • {nome_aba} • {self._nome_periodo()}",
                                    30, 56, (.75, .75, .75, 1)))
        relatorio.add_widget(rotulo(
            f"{resumo.get('reproducoes', 0)} {plural(resumo.get('reproducoes', 0))} • {tempo} ouvindo",
            32, 70))
        for principal, secundario, valor in linhas:
            linha = BoxLayout(size_hint_y=None, height=114, spacing=12)
            esquerda = BoxLayout(orientation="vertical")
            esquerda.add_widget(rotulo(principal, 34, 60, negrito=True))
            if secundario:
                esquerda.add_widget(rotulo(secundario, 25, 44, (.6, .6, .6, 1)))
            linha.add_widget(esquerda)
            direita = rotulo(valor, 30, 114, (.91, .12, .12, 1), alinhar="right", negrito=True)
            direita.size_hint_x = None
            direita.width = 330
            linha.add_widget(direita)
            relatorio.add_widget(linha)
        relatorio.add_widget(Widget())
        relatorio.add_widget(rotulo(
            f"{app.usuario_atual} • {datetime.now():%d/%m/%Y %H:%M} • contam reproduções de 1 min ou mais",
            24, 48, (.5, .5, .5, 1)))

        def salvar(_dt):
            temporario = None
            try:
                relatorio.do_layout()
                for filho in relatorio.walk():
                    if isinstance(filho, Label):
                        filho.texture_update()
                pasta_temporaria = Path(app.user_data_dir)
                pasta_temporaria.mkdir(parents=True, exist_ok=True)
                nome = f"estatisticas_{datetime.now():%Y%m%d_%H%M%S_%f}.png"
                temporario = pasta_temporaria / nome
                relatorio.export_to_png(str(temporario))
                destino = self._salvar_em_downloads(temporario, nome)
                self.mensagem = (
                    "Captura salva em Downloads/My Music Gospel."
                    if platform == "android" else f"Imagem salva em: {destino}"
                )
            except Exception as erro:
                trilha(f"falha ao gerar imagem das estatísticas: {erro!r}")
                self.mensagem = f"Falha ao salvar a captura ({type(erro).__name__})."
            finally:
                self.capturando = False
                relatorio.clear_widgets()
                if temporario is not None:
                    try:
                        temporario.unlink(missing_ok=True)
                    except OSError as erro:
                        trilha(f"falha ao limpar captura temporária: {erro!r}")

        Clock.schedule_once(salvar, 0.3)

    def _salvar_em_downloads(self, temporario, nome):
        if platform == "android":
            from jnius import autoclass
            atividade = autoclass("org.kivy.android.PythonActivity").mActivity
            resolver = atividade.getContentResolver()
            sdk = int(autoclass("android.os.Build$VERSION").SDK_INT)
            if sdk >= 29:
                ContentValues = autoclass("android.content.ContentValues")
                DownloadsStore = autoclass("android.provider.MediaStore$Downloads")
                valores = ContentValues()
                # MediaStore exige os nomes reais das colunas, em minúsculas.
                valores.put("_display_name", nome)
                valores.put("mime_type", "image/png")
                valores.put("relative_path", "Download/My Music Gospel")
                valores.put("is_pending", 1)
                uri = resolver.insert(DownloadsStore.EXTERNAL_CONTENT_URI, valores)
                if uri is None:
                    raise RuntimeError("O Android não criou o arquivo em Downloads.")
                try:
                    saida = resolver.openOutputStream(uri, "w")
                    if saida is None:
                        raise RuntimeError("O Android não abriu o arquivo de destino.")
                    try:
                        with temporario.open("rb") as origem:
                            while True:
                                bloco = origem.read(64 * 1024)
                                if not bloco:
                                    break
                                saida.write(bloco)
                        saida.flush()
                    finally:
                        saida.close()

                    publicado = ContentValues()
                    publicado.put("is_pending", 0)
                    if resolver.update(uri, publicado, None, None) <= 0:
                        raise RuntimeError("O Android não finalizou o arquivo em Downloads.")
                except Exception:
                    try:
                        resolver.delete(uri, None, None)
                    except Exception as erro_limpeza:
                        trilha(f"falha ao remover captura incompleta: {erro_limpeza!r}")
                    raise
                return "Downloads/My Music Gospel/" + nome

            ambiente = autoclass("android.os.Environment")
            downloads = Path(str(ambiente.getExternalStoragePublicDirectory(
                ambiente.DIRECTORY_DOWNLOADS).getAbsolutePath()))
            pasta_downloads = downloads / "My Music Gospel"
            pasta_downloads.mkdir(parents=True, exist_ok=True)
            destino = pasta_downloads / nome
            shutil.copyfile(temporario, destino)
            return str(destino)

        downloads = Path.home() / "Downloads" / "My Music Gospel"
        downloads.mkdir(parents=True, exist_ok=True)
        destino = downloads / nome
        shutil.copyfile(temporario, destino)
        return str(destino)


class TelaArtistas(Screen):
    """Lista de artistas (abre ao tocar em 'Banco de dados' no menu)."""

    def montar(self):
        caixa = self.ids.lista_artistas
        caixa.clear_widgets()
        for artista in artistas_ordenados():
            caixa.add_widget(LinhaArtista(
                artista_id=artista["id"],
                nome=nome_na_tela(artista["nome"]),
                logo=logo_do_artista(artista, _achar),
            ))


class TelaArtista(Screen):
    """Página de UM artista. Estrutura fixa: foto, Biografia, Discografia, Integrantes."""
    nome = StringProperty("")
    logo = StringProperty("")
    foto = StringProperty("")
    tem_albuns = BooleanProperty(False)
    tem_mais = BooleanProperty(False)     # True = há mais de 3 álbuns (mostra 'Ver mais')
    expandido = BooleanProperty(False)    # True = discografia completa na tela
    ALBUNS_RESUMO = 3                     # quantas capas aparecem antes do 'Ver mais'

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._artista = None
        self._albuns = []

    def carregar(self, artista):
        """Preenche a página com os dados de 'artista' (linha de db.listar_artistas)."""
        self._artista = artista
        perfil = montar_perfil(artista, _achar)
        self.nome = perfil["nome"]
        self.logo = perfil["logo"]
        self.foto = perfil["foto"]

        bio = self.ids.caixa_bio
        bio.clear_widgets()
        for paragrafo in perfil["biografia"]:
            bio.add_widget(ParagrafoBio(text=paragrafo))

        integrantes = self.ids.caixa_integrantes
        integrantes.clear_widgets()
        for funcao, nomes in perfil["integrantes"]:
            integrantes.add_widget(LinhaInfo(text=f"{funcao}: {nomes}"))

        self._albuns = perfil["albuns"]
        self.tem_albuns = bool(self._albuns)
        self.tem_mais = len(self._albuns) > self.ALBUNS_RESUMO
        self.expandido = False
        self._mostrar_capas()
        self.ids.rolagem.scroll_y = 1   # sempre abre no topo

    def recarregar(self):
        """Refaz a página aberta (usado quando o Android libera a leitura das imagens)."""
        if self._artista is not None:
            self.carregar(self._artista)

    def _mostrar_capas(self):
        grade = self.ids.grade_albuns
        grade.clear_widgets()
        visiveis = self._albuns if self.expandido else self._albuns[:self.ALBUNS_RESUMO]
        for album in visiveis:
            grade.add_widget(CapaAlbum(source=album["capa"], album_id=album["id"]))

    def alternar_discografia(self):
        self.expandido = not self.expandido
        self._mostrar_capas()


class TelaAlbum(Screen):
    """Página de UM álbum: capa, título, artista, ano e as músicas que existem no app."""
    nome = StringProperty("")
    artista = StringProperty("")
    logo = StringProperty("")
    capa = StringProperty("")
    lancamento = StringProperty("")
    sem_musicas = BooleanProperty(False)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._album_id = None
        self.musicas = []

    def carregar(self, album_id):
        """Preenche a página. Devolve False se o álbum não existe mais no banco."""
        album = next((a for a in listar_albuns() if a["id"] == album_id), None)
        if album is None:
            return False
        self._album_id = album_id
        self.nome = album["nome"]
        self.artista = nome_na_tela(album["artista"])
        self.logo = logo_do_artista({"nome": album["artista"]}, _achar)
        self.capa = _achar(album.get("capa"))
        self.lancamento = f"Lançado em {album['ano']}" if album.get("ano") else ""

        # só as músicas que estão no app (na ordem em que foram cadastradas)
        self.musicas = listar_musicas(album_id=album_id)
        self.sem_musicas = not self.musicas
        caixa = self.ids.caixa_musicas
        caixa.clear_widgets()
        for i, m in enumerate(self.musicas):
            caixa.add_widget(LinhaMusicaAlbum(
                titulo=m["titulo"], artista=m["artista"], indice=i, id_musica=m["id"]))
        self.ids.rolagem.scroll_y = 1   # sempre abre no topo
        return True

    def recarregar(self):
        """Refaz a página aberta (usado quando o Android libera a leitura das imagens)."""
        if self._album_id is not None:
            self.carregar(self._album_id)

    def tocar_a_partir_de(self, indice):
        App.get_running_app().iniciar_fila(
            self.musicas, comecar_em=indice, origem=self.nome)


class TelaPlaylist(Screen):
    nome_playlist = StringProperty("")
    subtitulo = StringProperty("")
    capa_playlist = StringProperty("")
    busca_aberta = BooleanProperty(False)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.playlist_id = None
        self.musicas = []   # todas as músicas da playlist
        self.visiveis = []  # as que aparecem (muda quando você busca)

    def carregar(self, playlist):
        self.playlist_id = playlist["id"]
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
                "viewclass": "LinhaMusica",
                "numero": i + 1,
                "titulo": m["titulo"],
                "artista": m["artista"],
                "capa": capa_da_musica(m),
                "indice": i,
                "id_musica": m["id"],
            }
            for i, m in enumerate(lista)
        ] + [{"viewclass": "LinhaAdicionar"}]  # botão no fim da playlist

    def abrir_adicionar(self):
        if self.playlist_id is not None:
            AdicionarMusicaModal(self.playlist_id).open()

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
    # Aba "Sobre o artista / Sobre a música" (tudo texto: o Label só aceita str)
    info_artista_nome = StringProperty("")
    info_artista_descricao = StringProperty("")
    info_artista_foto = StringProperty("")   # foto grande (arquivos/Artistas)
    info_artista_logo = StringProperty("")   # fotinho redonda (arquivos/logos)
    info_musica = StringProperty("")
    usuario_atual = StringProperty("")
    foto_perfil = StringProperty("")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.playlists = []
        self.lista_origem = []   # músicas na ordem da playlist (para desligar o aleatório)
        self.fila_atual = None   # dicionário criado pelo fila.criar_fila
        self.motor = criar_motor()  # motor de áudio (audio.py): Android ou Kivy
        self.carregado = False      # True = há uma música aberta no motor (tocando ou pausada)
        self.musica_tocando = None
        self._token = 0             # numera cada pedido de "tocar" (veja _tocar)
        self.auth_store = None
        self._usuario_da_faixa = ""
        self._inicio_da_faixa = None
        self._registro_atual = None
        self._ultima_gravacao_segundos = 0
        self._servico_audio_ativo = False
        self.pilha = []          # telas por onde passamos (para o botão voltar)

    def build(self):
        if platform == "android":
            from android.permissions import request_permissions  # só existe no Android
            from jnius import autoclass
            sdk = autoclass("android.os.Build$VERSION").SDK_INT
            permissoes = []
            if sdk >= 33:
                permissoes.extend((
                    "android.permission.READ_MEDIA_AUDIO",
                    "android.permission.READ_MEDIA_IMAGES",
                    "android.permission.POST_NOTIFICATIONS",
                ))
            else:
                permissoes.append("android.permission.READ_EXTERNAL_STORAGE")
            if sdk <= 28:
                permissoes.append("android.permission.WRITE_EXTERNAL_STORAGE")
            request_permissions(permissoes, self._permissoes_respondidas)
            preparar_banco(self.user_data_dir)

        db.criar_banco()
        # Atualiza o catálogo em instalações que já tinham um banco persistente.
        self._importar_se_mudou()
        db.limpar_historico_vazio()
        try:
            db.preencher_duracoes_vazias(_caminho)
        except Exception as erro:
            trilha(f"durações pendentes: {erro!r}")
        self.auth_store = AuthStore(Path(self.user_data_dir) / "contas.db")

        gerenciador = ScreenManager(transition=SlideTransition(duration=0.2))
        for classe, nome in ((TelaAcesso, "acesso"), (TelaMenu, "menu"),
                             (TelaEstatisticas, "estatisticas"),
                             (TelaPlaylist, "playlist"),
                             (TelaPlayer, "player"), (TelaFila, "fila"),
                             (TelaArtistas, "artistas"), (TelaArtista, "artista"),
                             (TelaAlbum, "album")):
            gerenciador.add_widget(classe(name=nome))
        Window.bind(on_keyboard=self._tecla)
        # O Android avisa quando está ficando sem memória (logo antes de matar apps).
        Window.bind(on_memorywarning=lambda *a: trilha("!!! AVISO DE MEMÓRIA BAIXA do sistema"))
        return gerenciador  # o "root" do app

    def _importar_se_mudou(self):
        """Só reimporta o biblioteca.py quando ele mudou (abertura bem mais rápida)."""
        try:
            marca = hashlib.md5((db.BASE_DIR / "biblioteca.py").read_bytes()).hexdigest()
            marca += f"-{db.VERSAO_SCHEMA}"
            arquivo = Path(self.user_data_dir) / "biblioteca.hash"
            with db.conexao() as con:
                vazio = con.execute("SELECT COUNT(*) FROM musicas").fetchone()[0] == 0
            if not vazio and arquivo.exists() and arquivo.read_text() == marca:
                return
            db.importar_biblioteca()
            arquivo.write_text(marca)
        except Exception as erro:
            trilha(f"importação condicional falhou: {erro!r}")
            db.importar_biblioteca()

    def cadastrar_usuario(self):
        tela = self.root.get_screen("acesso")
        try:
            nome = self.auth_store.cadastrar(
                tela.ids.nome.text, tela.ids.senha.text
            )
        except ValueError as erro:
            tela.mensagem = str(erro)
            return
        except Exception as erro:
            trilha(f"falha no cadastro: {erro!r}")
            tela.mensagem = "Não foi possível salvar o cadastro neste aparelho."
            return
        self._entrar_como(nome, manter_conectado=tela.ids.manter_conectado.active)

    def entrar_usuario(self):
        tela = self.root.get_screen("acesso")
        try:
            nome = self.auth_store.autenticar(
                tela.ids.nome.text, tela.ids.senha.text
            )
        except Exception as erro:
            trilha(f"falha no login: {erro!r}")
            tela.mensagem = "Não foi possível validar a conta neste aparelho."
            return
        if nome is None:
            tela.mensagem = "Nome ou senha incorretos."
            return
        self._entrar_como(nome, manter_conectado=tela.ids.manter_conectado.active)

    def _entrar_como(self, nome, manter_conectado=False):
        self.usuario_atual = nome
        self.foto_perfil = self.auth_store.obter_foto_perfil(nome)
        if manter_conectado:
            self.auth_store.salvar_sessao(nome)
        else:
            self.auth_store.limpar_sessao()
        self.pilha.clear()
        tela = self.root.get_screen("acesso")
        tela.ids.senha.text = ""
        tela.mensagem = ""
        self.root.current = "menu"

    def sair_da_conta(self):
        self._token += 1
        if self.carregado:
            self._parar_som()
        else:
            self.motor.parar()
        self._parar_servico_audio()
        self.tocando = False
        self.auth_store.limpar_sessao()
        self.usuario_atual = ""
        self.foto_perfil = ""
        self.pilha.clear()
        self.root.get_screen("acesso").selecionar_modo(False)
        self.root.current = "acesso"

    def abrir_perfil(self):
        PerfilModal().open()

    def abrir_seletor_foto_perfil(self):
        SeletorFotoPerfil().open()

    def definir_foto_perfil(self, origem):
        origem = Path(origem)
        if not origem.is_file() or origem.suffix.lower() not in (".png", ".jpg", ".jpeg"):
            return
        pasta = Path(self.user_data_dir) / "fotos_perfil"
        pasta.mkdir(parents=True, exist_ok=True)
        extensao = origem.suffix.lower()
        chave = hashlib.sha256(self.usuario_atual.casefold().encode("utf-8")).hexdigest()[:16]
        destino = pasta / f"{chave}{extensao}"
        shutil.copyfile(origem, destino)
        self.auth_store.salvar_foto_perfil(self.usuario_atual, str(destino))
        self.foto_perfil = str(destino)

    def salvar_nova_musica(self, playlist_id, arquivo, titulo, artista, album, ano, capa, generos):
        """Copia o áudio (e a capa) para a pasta privada do app e cadastra no banco.
        Devolve "" se deu certo ou a mensagem de erro para o formulário."""
        origem = Path(arquivo) if arquivo else None
        if origem is None or not origem.is_file():
            return "Escolha o arquivo de áudio."
        if origem.suffix.lower() not in EXT_AUDIO:
            return "Formato não suportado. Use mp3, m4a, ogg, wav, flac ou aac."
        if not (titulo.strip() and artista.strip() and album.strip()):
            return "Preencha título, artista e álbum."
        pasta = Path(self.user_data_dir) / "minhas_musicas"
        (pasta / "capas").mkdir(parents=True, exist_ok=True)
        chave = hashlib.sha1(f"{origem.name}{origem.stat().st_size}".encode()).hexdigest()[:10]
        destino = pasta / f"{chave}{origem.suffix.lower()}"
        if destino.exists():
            return "Esta música já foi adicionada."
        destino_capa = None
        try:
            shutil.copyfile(origem, destino)
            if capa and Path(capa).is_file() and Path(capa).suffix.lower() in EXT_IMAGEM:
                destino_capa = pasta / "capas" / f"{chave}{Path(capa).suffix.lower()}"
                shutil.copyfile(capa, destino_capa)
            from duracao import duracao_do_arquivo
            db.adicionar_musica(titulo, artista, album, str(destino), ano,
                                str(destino_capa) if destino_capa else "", generos, playlist_id,
                                duracao=duracao_do_arquivo(destino))
        except ValueError as erro:
            destino.unlink(missing_ok=True)
            if destino_capa:
                destino_capa.unlink(missing_ok=True)
            return str(erro)
        except Exception as erro:
            trilha(f"falha ao adicionar música: {erro!r}")
            destino.unlink(missing_ok=True)
            if destino_capa:
                destino_capa.unlink(missing_ok=True)
            return "Não foi possível salvar a música."
        self.playlists = listar_playlists()
        self.root.get_screen("menu").montar(self.playlists)
        self.root.get_screen("artistas").montar()
        self._sincronizar_fila_android()
        for p in self.playlists:
            if p["id"] == playlist_id:
                self.root.get_screen("playlist").carregar(p)
        return ""

    def abrir_estatisticas(self):
        tela = self.root.get_screen("estatisticas")
        tela.atualizar()
        self.ir("estatisticas")

    def _permissoes_respondidas(self, permissoes, concedidas):
        # Pode ser chamado fora da thread do Kivy: o Clock traz de volta.
        Clock.schedule_once(lambda dt: self._recarregar_capas(), 0)

    def _recarregar_capas(self):
        """Antes da permissão o Android escondia as capas (exists() dava False): refaz."""
        if self.root is None or not self.playlists:
            return  # ainda não terminou de abrir; o on_start monta tudo
        self.root.get_screen("artistas").montar()
        self.root.get_screen("artista").recarregar()
        self.root.get_screen("album").recarregar()
        self.root.get_screen("menu").montar(self.playlists)
        tela = self.root.get_screen("playlist")
        if tela.nome_playlist:
            tela.capa_playlist = capa_da_playlist(tela.nome_playlist)

    def on_start(self):
        self.playlists = listar_playlists()
        self.root.get_screen("menu").montar(self.playlists)
        self.root.get_screen("artistas").montar()
        usuario = self.auth_store.usuario_da_sessao()
        if usuario:
            self.usuario_atual = usuario
            self.foto_perfil = self.auth_store.obter_foto_perfil(usuario)
            self.root.current = "menu"
        Clock.schedule_interval(self._tick, 0.25)  # atualiza a barra de progresso

    def on_pause(self):
        trilha("on_pause (app foi para segundo plano / tela bloqueada)")
        if self.carregado and self.tocando:
            self._salvar_progresso_estatistica(self.motor.posicao())
            # No Android, o MediaSessionService já é dono do áudio e da fila.
            self._iniciar_servico_audio()
        return True  # sem isso o Android mata o app ao bloquear a tela

    def on_resume(self):
        trilha("on_resume (app voltou)")
        self._sincronizar_estado_android()

    def _iniciar_servico_audio(self):
        """Compatibilidade: o motor Android inicia o serviço Media3 ao tocar."""
        return

    def _parar_servico_audio(self):
        # Pausar não encerra a sessão: o sistema mantém a notificação e o botão
        # de retomar disponíveis. Parar a faixa é responsabilidade do motor.
        return

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

    def abrir_artista(self, artista_id):
        """Abre a página do artista tocado na lista 'Artistas'."""
        for artista in artistas_ordenados():
            if artista["id"] == artista_id:
                self.root.get_screen("artista").carregar(artista)
                self.ir("artista")
                return

    def abrir_album(self, album_id):
        """Abre a página do álbum tocado na discografia do artista."""
        if self.root.get_screen("album").carregar(album_id):
            self.ir("album")

    def abrir_artista_atual(self):
        """Toque no cartão 'Sobre o Artista' do player: abre a página do artista que está tocando."""
        procurado = _sem_acento(self.artista_atual)
        if not procurado:
            return
        for artista in artistas_ordenados():
            if _sem_acento(artista["nome"]) == procurado:   # ignora acento e maiúscula
                self.root.get_screen("artista").carregar(artista)
                self.ir("artista")   # o botão voltar traz de volta ao player
                return
        trilha(f"abrir_artista_atual: '{self.artista_atual}' não está na lista de artistas")

    # ---------------- FILA ----------------
    def iniciar_fila(self, musicas, comecar_em=None, aleatorio=False, origem=""):
        if not musicas:
            return
        self.lista_origem = list(musicas)
        self.fila_atual = fila.criar_fila(musicas, comecar_em=comecar_em, aleatorio=aleatorio)
        self.aleatorio = aleatorio
        self.origem = origem
        self._tocar(fila.musica_atual(self.fila_atual))
        self.ir("player", "up")

    def tocar_da_fila(self, indice):
        """Tocou numa música de 'A Seguir': ela vira a atual."""
        if self.fila_atual is None:
            return
        self.fila_atual["posicao"] = indice
        self._tocar(fila.musica_atual(self.fila_atual))
        self.ir("player", "up")

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
        self._sincronizar_fila_android()

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
        self._sincronizar_fila_android()

    def mover_fila(self, de, para):
        f = self.fila_atual
        if f is None or de <= f["posicao"] or para <= f["posicao"]:
            return
        fila.mover(f, de, para)
        self._atualizar_fila()
        self._sincronizar_fila_android()

    def _atualizar_fila(self):
        self.root.get_screen("fila").atualizar()

    def _payload_fila_android(self):
        f = self.fila_atual
        if platform != "android" or f is None:
            return ""
        musicas = f.get("itens", [])
        indice_atual = f.get("posicao", 0)
        musica_atual = musicas[indice_atual] if 0 <= indice_atual < len(musicas) else None
        id_atual = str(musica_atual.get("id", "")) if musica_atual else ""
        faixas = []
        indice_nativo = 0
        for musica in musicas:
            caminho = _achar(musica.get("arquivo"))
            if not caminho or not Path(caminho).is_file():
                continue
            if str(musica.get("id", "")) == id_atual:
                indice_nativo = len(faixas)
            capa = capa_da_musica(musica)
            faixas.append({
                "id": str(musica["id"]),
                "title": musica.get("titulo", ""),
                "artist": musica.get("artista", ""),
                "album": musica.get("album", ""),
                "path": caminho,
                "artwork": capa,
            })
        return json.dumps({
            "tracks": faixas,
            "index": indice_nativo,
            "user": self.usuario_atual,
            "database": str(db.BANCO),
        }, ensure_ascii=False)

    def _sincronizar_fila_android(self):
        if platform != "android" or self.fila_atual is None:
            return
        sincronizar = getattr(self.motor, "sincronizar_fila", None)
        if sincronizar is None:
            return
        try:
            sincronizar(self._payload_fila_android())
        except Exception as erro:
            trilha(f"não foi possível atualizar a fila do Media3: {erro!r}")

    def _aprender_duracao(self, segundos):
        """Faixa sem duração no banco: guarda a que o player descobriu ao tocar."""
        musica = self.musica_tocando
        if not musica or musica.get("duracao_seg") or not segundos or segundos < 1:
            return
        try:
            musica["duracao_seg"] = int(round(segundos))
            db.atualizar_duracao_musica(musica["id"], segundos)
        except Exception as erro:
            trilha(f"não gravei a duração: {erro!r}")

    def _sincronizar_estado_android(self):
        if platform != "android":
            return
        obter_estado = getattr(self.motor, "estado", None)
        if obter_estado is None:
            return
        estado = obter_estado()
        media_id = estado.get("media_id", "")
        if not media_id:
            return
        f = self.fila_atual
        if f is not None:
            for indice, musica in enumerate(f.get("itens", [])):
                if str(musica.get("id", "")) == media_id:
                    f["posicao"] = indice
                    if self.id_atual != musica["id"]:
                        self._sincronizar_faixa_android(musica)
                    break
        self.carregado = estado.get("carregado", False)
        self.tocando = estado.get("tocando", False)
        self.posicao = estado.get("posicao", 0.0)
        self.duracao = estado.get("duracao", 0.0) or self.duracao
        if estado.get("duracao", 0.0) > 0 and self.id_atual == self.musica_tocando.get("id") if self.musica_tocando else False:
            self._aprender_duracao(estado["duracao"])

    def _sincronizar_faixa_android(self, musica):
        """Atualiza a interface quando a sessão avança fora da Activity."""
        self._token += 1
        self.musica_tocando = musica
        self.id_atual = musica["id"]
        self.titulo_atual = musica["titulo"]
        self.artista_atual = musica["artista"]
        self.capa_atual = capa_da_musica(musica)
        self._atualizar_info(musica)
        self._usuario_da_faixa = self.usuario_atual
        self._inicio_da_faixa = datetime.now()
        self._registro_atual = None
        self._ultima_gravacao_segundos = 0
        self.posicao = 0
        self.duracao = musica.get("duracao_seg") or 0
        self.tem_musica = True
        self._atualizar_fila()

    # ---------------- BOTÕES DO PLAYER ----------------
    def pular(self, automatico=False):
        """Próxima faixa. 'automatico' = a anterior acabou sozinha (não foi toque no botão)."""
        f = self.fila_atual
        if f is None:
            return
        era_a_ultima = f["posicao"] + 1 >= len(f["itens"])
        trilha(f"pular(automatico={automatico}) posicao={f['posicao']}/{len(f['itens'])}")

        # fila.proxima() refaz a fila sozinha quando ela acaba: aleatório ligado =
        # embaralha de novo; desligado = volta ao começo da playlist.
        seguinte = fila.proxima(f, self.lista_origem)
        if seguinte is None:  # só acontece com a fila vazia
            self._parar_som()
            self.tocando = False
            self.posicao = 0
            self._atualizar_fila()
            return

        recomecou_parado = era_a_ultima and automatico and not TOCAR_AO_REINICIAR_FILA
        self._tocar(seguinte, iniciar=not recomecou_parado)

    def faixa_anterior(self):
        if self.fila_atual is None:
            return
        if self.posicao > 3:  # já tocou um pouco: "voltar" reinicia a música (como no Spotify)
            self._tocar(fila.musica_atual(self.fila_atual))
        else:
            self._tocar(fila.anterior(self.fila_atual))

    def pausar_ou_retomar(self):
        if not self.carregado:
            if self.fila_atual is not None:
                self._tocar(fila.musica_atual(self.fila_atual))
            return

        if self.tocando:
            self.motor.pausar()  # o motor guarda ONDE parou
            self.posicao = self.motor.posicao()
            self._salvar_progresso_estatistica(self.posicao)
            self.tocando = False
            self._parar_servico_audio()
        else:
            # Não marca a interface como "tocando" se o motor falhar ao retomar.
            if self.motor.retomar():
                self.posicao = self.motor.posicao()
                self.tocando = True
                self._iniciar_servico_audio()

    def buscar(self, fracao):
        """Arrastou a barra: pula para 'fracao' (0 a 1) da música."""
        if not self.carregado or not self.duracao:
            return
        segundos = fracao * self.duracao
        if self.motor.buscar(segundos):
            self.posicao = segundos
        else:
            trilha(f"buscar({segundos:.0f}s) ignorado: o motor {type(self.motor).__name__} "
                   "não sabe pular no tempo (instale o pygame)")

    def formatar_tempo(self, segundos):
        segundos = int(max(0, segundos or 0))
        return f"{segundos // 60}:{segundos % 60:02d}"

    # ---------------- PLAYER ----------------
    def _atualizar_info(self, musica):
        """Preenche a aba 'Sobre o artista / Sobre a música' da faixa atual."""
        artista = obter_artista(musica["artista"])
        self.info_artista_nome = artista.get("nome") or musica["artista"]
        self.info_artista_descricao = artista.get("descricao", "")
        self.info_artista_foto = _achar(artista.get("foto"))
        self.info_artista_logo = _achar(artista.get("foto_de_perfil"))
        self.info_musica = linhas_sobre_a_musica(musica)

    def _tocar(self, musica, iniciar=True):
        """
        Troca para 'musica'. A tela muda NA HORA; o áudio é aberto um instante
        depois (_abrir_no_motor). Essa pausa separa "fechar a faixa velha" de
        "abrir a nova" - trocar tudo no mesmo instante, ou várias vezes seguidas,
        era o que derrubava o app ao abrir a segunda música.
        """
        if not musica:
            return
        self._token += 1
        token = self._token
        trilha(f"_tocar #{token}: {musica['titulo']!r} (iniciar={iniciar})")

        self._parar_som()  # fecha a anterior (e grava no histórico)
        self.carregado = False
        self.tem_musica = True
        self.musica_tocando = musica
        self._usuario_da_faixa = self.usuario_atual
        self._inicio_da_faixa = datetime.now()
        self._registro_atual = None
        self._ultima_gravacao_segundos = 0
        self.id_atual = musica["id"]
        self.titulo_atual = musica["titulo"]
        self.artista_atual = musica["artista"]
        self.capa_atual = capa_da_musica(musica)
        self._atualizar_info(musica)
        self.posicao = 0
        self.duracao = musica.get("duracao_seg") or 0
        self.tocando = iniciar
        self._atualizar_fila()

        Clock.schedule_once(lambda dt: self._abrir_no_motor(musica, token, iniciar), 0.05)

    def _abrir_no_motor(self, musica, token, iniciar):
        if token != self._token:  # já pediram outra música: esta não vale mais
            return
        caminho = _achar(musica["arquivo"])
        if not caminho:
            trilha(f"  arquivo NÃO encontrado: {musica['arquivo']}")
            self._falha(f"Não achei: {musica['arquivo']}")
            return
        try:
            configurar_fila = getattr(self.motor, "configurar_fila", None)
            if configurar_fila is not None:
                configurar_fila(self._payload_fila_android())
            trilha(f"  motor.carregar(...) começando [{type(self.motor).__name__}]")
            if not self.motor.carregar(caminho):
                self._falha(f"Não consegui abrir: {musica['titulo']}")
                return
            self.carregado = True
            aviso = getattr(self.motor, "aviso", "")
            if aviso:
                trilha(f"  AVISO: {aviso}")
            trilha("  motor.carregar OK; chamando motor.tocar()")
            if iniciar:
                if not self.motor.tocar():
                    self.motor.parar()
                    self.carregado = False
                    self._falha(f"Não consegui reproduzir: {musica['titulo']}")
                    return
                self.tocando = True
                self._iniciar_servico_audio()
                trilha("  motor.tocar OK (tocando)")
            else:
                self.tocando = False
                preparar_pausado = getattr(self.motor, "preparar_pausado", None)
                if preparar_pausado is not None:
                    preparar_pausado()
            if not self.duracao:
                self.duracao = self.motor.duracao()  # plano B: pergunta ao próprio áudio
        except Exception as erro:  # erro de áudio nunca deve fechar o app
            import traceback
            traceback.print_exc()
            trilha(f"  ERRO de áudio: {erro!r}")
            self.motor.parar()
            self.carregado = False
            self._falha(f"Erro de áudio: {erro}")

    def _falha(self, mensagem):
        self.carregado = False
        self.tocando = False
        self._parar_servico_audio()
        self.titulo_atual = mensagem

    def _tick(self, dt):
        """4x por segundo: lê a posição (a barra de progresso se move sozinha)
        e confere se a música acabou."""
        if platform == "android":
            self._sincronizar_estado_android()
            return  # o MediaSessionService atualiza e registra o áudio nativo
        if not self.carregado or not self.tocando:
            return
        self.posicao = self.motor.posicao()
        if not self.duracao:
            self.duracao = self.motor.duracao()
            self._aprender_duracao(self.duracao)
        segundos = int(max(0, self.posicao))
        if segundos >= 1 and (
                self._registro_atual is None
                or segundos - self._ultima_gravacao_segundos >= 5):
            self._salvar_progresso_estatistica(segundos)
        if self.motor.terminou():
            self._terminou()

    def _salvar_progresso_estatistica(self, segundos, completa=False):
        if platform == "android":
            return  # a sessão nativa registra escutas durante todo o segundo plano
        segundos = int(max(0, segundos or 0))
        if self.musica_tocando is None or segundos <= 0:
            return
        try:
            if self._registro_atual is None:
                self._registro_atual = registrar_reproducao(
                    self.musica_tocando["id"], segundos, completa,
                    quando=self._inicio_da_faixa,
                    usuario=self._usuario_da_faixa or self.usuario_atual,
                )
            else:
                atualizar_reproducao(self._registro_atual, segundos, completa)
            self._ultima_gravacao_segundos = segundos
        except Exception as erro:
            print("historico: nao consegui atualizar a escuta:", erro)

    def _registrar(self, segundos, completa):
        """Finaliza o registro incremental da sessao de escuta atual."""
        self._salvar_progresso_estatistica(segundos, completa)
        self._registro_atual = None
        self._ultima_gravacao_segundos = 0

    def _parar_som(self):
        """Parada MANUAL (pular, voltar, trocar de música)."""
        if not self.carregado:
            return
        segundos = int(self.motor.posicao())  # (pausada ou não, é o quanto ouviu)
        self.motor.parar()
        self.carregado = False
        self._registrar(segundos, completa=False)

    def _terminou(self):
        """A faixa acabou sozinha: fecha, registra e emenda na próxima."""
        m = self.musica_tocando
        if m is None:
            return
        trilha(f"_terminou: faixa acabou sozinha: {m['titulo']!r}")
        self.motor.parar()
        self.carregado = False
        self.tocando = False
        trilha("  motor.parar OK; gravando histórico")
        self._registrar(m.get("duracao_seg") or 0, completa=True)
        trilha("  histórico OK; agendando a próxima")

        # A troca fica para o próximo ciclo do Clock (não no mesmo callback que
        # detectou o fim) e só vale se ninguém escolheu outra música enquanto isso.
        token_do_fim = self._token
        Clock.schedule_once(lambda dt: self._avancar_apos_fim(token_do_fim), 0)

    def _avancar_apos_fim(self, token_do_fim):
        if token_do_fim != self._token:
            trilha("_avancar_apos_fim: ignorado (já escolheram outra música)")
            return
        self.pular(automatico=True)

    def on_stop(self):
        self._parar_servico_audio()
        trilha("on_stop (app encerrando normalmente)")
        if platform == "android":
            # Fechar a Activity não deve destruir o player que vive no serviço.
            return
        if self.carregado:
            self._parar_som()
        else:
            self.motor.parar()


if __name__ == "__main__":
    MainApp().run()
