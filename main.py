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
import math
import os
import random
import threading
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


def _salvaguarda(funcao, *args):
    """Roda uma etapa do save; se falhar, só registra (o app nunca deve cair por causa dele)."""
    try:
        resultado = funcao(*args)
        if isinstance(resultado, str) and resultado:
            trilha(f"save: {resultado}")
    except Exception as erro:
        trilha(f"save: {funcao.__name__} falhou: {erro!r}")


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
from kivy.animation import Animation, AnimationTransition
from kivy.clock import Clock
from kivy.core.image import Image as CoreImage
from kivy.core.text import LabelBase
from kivy.core.window import Window
from kivy.graphics import Color, RoundedRectangle
from kivy.graphics.texture import Texture
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
from kivy.utils import escape_markup, platform

import db
import salvamento
import fila
import letras
from auth import AuthStore
from audio import criar_motor
from db import (_sem_acento, alternar_favorito, atualizar_reproducao, ids_favoritos, estatisticas_artistas, estatisticas_musicas,
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
    alfa = NumericProperty(1)  # animado: a imagem surge com fade em vez de "piscar"
    escala = NumericProperty(1)  # animado: a capa do player "respira" ao pausar/tocar
    sombra = BooleanProperty(False)  # sombra suave sob a imagem

    def __init__(self, **kwargs):
        self._original = None
        self._animar = False
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
                    self._animar = True
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
        if abs(tw / th - w / h) < 0.01:
            self.tex = original          # mesma proporção: imagem inteira, sem recorte
        else:
            if tw / th > w / h:      # imagem mais larga que o espaço: corta as laterais
                cw, ch = th * w / h, th
            else:                    # mais alta: corta em cima e embaixo
                cw, ch = tw, tw * h / w
            cw, ch = min(tw, max(1, int(round(cw)))), min(th, max(1, int(round(ch))))
            x, y = (tw - cw) // 2, (th - ch) // 2
            self.tex = self._recorte_exato(original, x, y, cw, ch)
        if self.tex is not None and self._animar:
            self._animar = False
            self.alfa = 0
            Animation(alfa=1, duration=.35, t="out_quad").start(self)

    def _recorte_exato(self, original, x, y, cw, ch):
        """Sub-região da textura (sem copiar pixels). O desenho é feito com Rectangle no .kv,
        que respeita as coordenadas da região - assim a imagem não estica, não espelha
        e não fica de cabeça para baixo."""
        try:
            return original.get_region(x, y, cw, ch)
        except Exception:
            return None      # nunca estica: sem recorte válido, mostra só o fundo


class OndaProgresso(Widget):
    """
    Onda decorativa que também mostra o progresso: as barras já tocadas ficam
    vermelhas, as próximas ficam de vidro. O formato vem do id da música (sempre igual).
    """
    progresso = NumericProperty(0)
    semente = NumericProperty(0)
    total = 44

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._barras = []    # (Color, RoundedRectangle)
        self._alturas = []
        self._tocadas = -1
        self.bind(pos=self._posicionar, size=self._posicionar,
                  semente=self._criar, progresso=self._pintar)
        self._criar()

    def _criar(self, *_):
        rnd = random.Random(int(self.semente) + 7)
        self._alturas = [
            max(.14, min(1.0, abs(math.sin(i * .42 + rnd.random() * 1.4)) * (.45 + .55 * rnd.random())))
            for i in range(self.total)]
        self.canvas.clear()
        self._barras = []
        with self.canvas:
            for _i in range(self.total):
                cor = Color(1, 1, 1, .3)
                self._barras.append((cor, RoundedRectangle(radius=[dp(1.6)])))
        self._tocadas = -1
        self._posicionar()
        self._pintar()

    def _posicionar(self, *_):
        if not self._barras or self.width <= 0:
            return
        passo = self.width / self.total
        largura = max(dp(2), passo * .52)
        for i, (_cor, ret) in enumerate(self._barras):
            altura = max(dp(4), self.height * self._alturas[i])
            ret.size = (largura, altura)
            ret.pos = (self.x + i * passo + (passo - largura) / 2, self.center_y - altura / 2)

    def _pintar(self, *_):
        tocadas = int(round(max(0, min(1, self.progresso)) * self.total))
        if tocadas == self._tocadas:
            return
        self._tocadas = tocadas
        for i, (cor, _ret) in enumerate(self._barras):
            if i < tocadas:
                cor.rgba = (1, .18, .22, 1)
            else:
                cor.rgba = (1, 1, 1, .14 + .26 * (1 - i / self.total))


class CartaoVidro(BoxLayout):
    """Cartão de vidro (o desenho está no .kv). As propriedades nascem aqui, com valor
    inicial, porque o canvas do .kv é montado ANTES dos valores padrão do próprio .kv."""
    raio = NumericProperty(dp(28))


class BotaoRedondo(ButtonBehavior, Widget):
    """Botão redondo com ícone. estilo: "vidro", "destaque", "cheio" ou "limpo"."""
    icone = StringProperty("")
    tam_icone = NumericProperty(dp(22))
    estilo = StringProperty("vidro")
    tinta = ListProperty([1, 1, 1, 1])


class RaizSegura(BoxLayout):
    """
    Raiz do app. Reserva o espaço das barras do sistema (status/navegação):
    no Android 15+ o app é desenhado ATRÁS delas, e o topo ficava sobreposto.
    Também desenha o degradê colorido do player (cor tirada da capa).
    """
    alfa_fundo = NumericProperty(0)

    def __init__(self, gerenciador, **kwargs):
        kwargs.setdefault("orientation", "vertical")
        super().__init__(**kwargs)
        self.gerenciador = gerenciador
        self.add_widget(gerenciador)
        gerenciador.bind(current=self._ao_trocar_tela)

    # Atalhos: o resto do app continua usando self.root.current/get_screen/transition
    @property
    def current(self):
        return self.gerenciador.current

    @current.setter
    def current(self, valor):
        self.gerenciador.current = valor

    @property
    def transition(self):
        return self.gerenciador.transition

    def get_screen(self, nome):
        return self.gerenciador.get_screen(nome)

    def _ao_trocar_tela(self, _gerenciador, tela):
        Animation(alfa_fundo=.62 if tela in ("player", "letra") else 0,
                  duration=.4, t="out_quad").start(self)


def _degrade_vertical():
    """Textura 1x2 (opaca em cima, transparente embaixo); a GPU interpola o meio."""
    textura = Texture.create(size=(1, 2), colorfmt="rgba")
    textura.blit_buffer(bytes([255, 255, 255, 0, 255, 255, 255, 255]), colorfmt="rgba", bufferfmt="ubyte")
    textura.mag_filter = "linear"
    textura.min_filter = "linear"
    return textura


def _tex_orb(n=96):
    """Mancha de luz: círculo branco que esmaece nas bordas (a cor vem do Color do .kv)."""
    c = (n - 1) / 2
    dados = bytearray()
    for y in range(n):
        for x in range(n):
            d = min(1.0, (((x - c) ** 2 + (y - c) ** 2) ** 0.5) / c)
            a = 1.0 - d
            a = a * a * (3 - 2 * a)
            dados += bytes((255, 255, 255, int(255 * a)))
    textura = Texture.create(size=(n, n), colorfmt="rgba")
    textura.blit_buffer(bytes(dados), colorfmt="rgba", bufferfmt="ubyte")
    textura.mag_filter = "linear"
    textura.min_filter = "linear"
    return textura


_PIXELS = {}   # caminho -> bytes RGBA da imagem inteira (cache curto, p/ recortar sem esticar)


def _pixels_de(caminho, textura):
    achado = _PIXELS.get(caminho)
    if achado is None:
        if len(_PIXELS) >= 12:
            _PIXELS.pop(next(iter(_PIXELS)))
        achado = textura.pixels
        _PIXELS[caminho] = achado
    return achado


def _cor_dominante(textura):
    """Cor média da capa (amostrando ~400 pixels), ajustada para um fundo agradável."""
    dados = textura.pixels
    largura, altura = textura.size
    passo = max(1, (largura * altura) // 400)
    r = g = b = n = 0
    for i in range(0, len(dados) - 3, 4 * passo):
        r += dados[i]; g += dados[i + 1]; b += dados[i + 2]; n += 1
    if not n:
        return [.12, .12, .12, 1]
    r, g, b = r / n / 255, g / n / 255, b / n / 255
    maior = max(r, g, b, .01)
    fator = .6 / maior if maior > .6 else (.32 / maior if maior < .32 else 1)
    return [min(1, r * fator), min(1, g * fator), min(1, b * fator), 1]


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

def entrada_em_cascata(widgets, atraso=.045, duracao=.38, maximo=14):
    """Os itens surgem um após o outro (fade suave), como nos apps da Apple."""
    for i, w in enumerate(widgets):
        w.opacity = 0
        anim = Animation(opacity=1, duration=duracao, t="out_cubic")
        Clock.schedule_once(lambda _dt, w=w, anim=anim: anim.start(w), min(i, maximo) * atraso)


class ToqueSuave:
    """Mixin: ao tocar, o item 'afunda' rápido e volta devagar (feedback estilo iOS)."""
    def on_state(self, _widget, estado):
        Animation.cancel_all(self, "opacity")
        Animation(opacity=.55 if estado == "down" else 1,
                  duration=.07 if estado == "down" else .32, t="out_quad").start(self)


class TransicaoIOS(SlideTransition):
    """Deslize com desaceleração suave (estilo iPhone). As duas telas andam juntas, SEM se
    sobrepor: as telas do app são de vidro (sem fundo opaco), então sobrepor mostra um fantasma."""
    def on_progress(self, progression):
        entra, sai, gerente = self.screen_in, self.screen_out, self.manager
        x, y = gerente.pos
        w, h = gerente.size
        p = AnimationTransition.out_cubic(progression)
        d = self.direction
        if d == "left":
            entra.y = sai.y = y
            entra.x = x + w * (1 - p)
            sai.x = x - w * p
        elif d == "right":
            entra.y = sai.y = y
            entra.x = x - w * (1 - p)
            sai.x = x + w * p
        elif d == "up":
            entra.x = sai.x = x
            entra.y = y - h * (1 - p)
            sai.y = y + h * p
        else:
            entra.x = sai.x = x
            entra.y = y + h * (1 - p)
            sai.y = y - h * p


class ControleSegmentado(BoxLayout):
    """Seletor com a 'pílula' deslizante (estilo iPhone). O .kv desenha a pílula em 'indice'."""
    indice = NumericProperty(0)
    total = NumericProperty(3)

    def ir_para(self, indice, animar=True):
        Animation.cancel_all(self, "indice")
        if animar:
            Animation(indice=indice, duration=.38, t="out_cubic").start(self)
        else:
            self.indice = indice


class LinhaMusica(ToqueSuave, ButtonBehavior, BoxLayout):
    """Uma linha da playlist."""
    numero = NumericProperty(0)
    titulo = StringProperty("")
    artista = StringProperty("")
    capa = StringProperty("")
    indice = NumericProperty(0)      # posição na lista visível (0 = primeira)
    id_musica = NumericProperty(-1)  # para destacar a que está tocando

    def on_release(self):
        App.get_running_app().root.get_screen("playlist").tocar_a_partir_de(int(self.indice))

    def abrir_menu(self):
        """Botão dos três pontinhos da linha."""
        App.get_running_app().abrir_menu_musica(
            self.id_musica, self.titulo, self.artista, self.capa)


class ChipVidro(ButtonBehavior, BoxLayout):
    """Pílula de vidro clicável dos filtros (o desenho está no .kv)."""
    texto = StringProperty("")
    icone = StringProperty("")
    ativo = BooleanProperty(False)
    esmaecido = BooleanProperty(False)


class LinhaAcaoMenu(ButtonBehavior, BoxLayout):
    """Uma ação do menu da música (ícone + texto)."""
    texto = StringProperty("")
    icone = StringProperty("")
    tinta = ListProperty([1, 1, 1, 1])


class MenuMusica(ModalView):
    """Menu dos três pontinhos: favoritar e (no player) ver 'Sobre'."""
    id_musica = NumericProperty(-1)
    titulo = StringProperty("")
    artista = StringProperty("")
    capa = StringProperty("")
    do_player = BooleanProperty(False)

    def favoritar(self):
        App.get_running_app().alternar_favorita(self.id_musica)
        self.dismiss()

    def ver_sobre(self):
        self.dismiss()
        try:
            tela = App.get_running_app().root.get_screen("player")
            Animation(scroll_y=0, duration=.5, t="out_quad").start(tela.ids.rolagem)
        except Exception as erro:
            trilha(f"menu: não consegui rolar até o 'Sobre': {erro!r}")


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


class CartaoPlaylist(ToqueSuave, ButtonBehavior, BoxLayout):
    """Uma playlist no menu."""
    playlist_id = NumericProperty(0)
    nome = StringProperty("")
    subtitulo = StringProperty("")
    capa = StringProperty("")

    def on_release(self):
        App.get_running_app().abrir_playlist(int(self.playlist_id))


class LinhaArtista(ToqueSuave, ButtonBehavior, BoxLayout):
    """Um artista na lista 'Artistas' (fotinho redonda + nome)."""
    artista_id = NumericProperty(0)
    nome = StringProperty("")
    logo = StringProperty("")
    detalhe = StringProperty("")   # ex.: "3 álbuns"

    def on_release(self):
        App.get_running_app().abrir_artista(int(self.artista_id))


class CartaoAlbumMini(ButtonBehavior, BoxLayout):
    """Capa + nome do álbum no carrossel da discografia. Tocar abre o álbum."""
    album_id = NumericProperty(0)
    capa = StringProperty("")
    nome = StringProperty("")

    def on_release(self):
        App.get_running_app().abrir_album(int(self.album_id))


class CapaAlbum(ButtonBehavior, ImagemArredondada):
    """Capa quadrada da discografia (o tamanho está no .kv). Tocar abre o álbum."""
    album_id = NumericProperty(0)

    def on_release(self):
        App.get_running_app().abrir_album(int(self.album_id))


class LinhaMusicaAlbum(ToqueSuave, ButtonBehavior, BoxLayout):
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
        entrada_em_cascata(caixa.children[::-1])


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
        self._mover_pilula("ctl_aba", ("musicas", "artistas", "geral"), aba)
        self.atualizar()

    def selecionar_periodo(self, periodo):
        self.periodo = periodo
        self._mover_pilula("ctl_periodo", ("semana", "mes", "ano"), periodo)
        self.atualizar()

    def _mover_pilula(self, id_controle, opcoes, valor):
        try:
            self.ids[id_controle].ir_para(opcoes.index(valor))
        except Exception as erro:
            trilha(f"pílula do seletor: {erro!r}")

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
                ("Reproduções (30 s ou mais)", str(resumo.get("reproducoes", 0))),
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
            entrada_em_cascata(cartoes.children[::-1], atraso=.07)
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
                entrada_em_cascata(coluna.children[::-1])
                rolagem.add_widget(coluna)
                caixa.add_widget(rolagem)
        self._assinatura = assinatura

    def _aviso_vazio(self, usuario, desde):
        total, validas, todos = db.contagem_sessoes(usuario, desde)
        if total:
            texto = (f"{total} escuta(s) registrada(s), mas nenhuma chegou a 30 segundos.\n"
                     "Ouça uma música por pelo menos 30 segundos para ela contar.")
        elif todos:
            texto = "Há escutas de outra conta neste período.\nEntre nela para ver os números."
        else:
            texto = "Ainda não há reproduções neste período.\nOuça uma música por pelo menos 30 segundos."
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
        """Imagem 9:16 (1080x1920) no estilo 'Wrapped': capa grande, Top Artistas / Top Músicas,
        minutos ouvidos e reproduções. A cor de destaque vem da capa da música nº 1."""
        from kivy.graphics import Color, Ellipse, PopMatrix, PushMatrix, Rectangle, Rotate
        app = App.get_running_app()
        self.atualizar()
        self.mensagem = "Gerando captura…"
        resumo = self._resumo
        minutos = int((resumo.get("segundos_ouvidos") or 0) // 60)
        W, H, TOPO = 1080, 1920, 1080
        MESES = ("JANEIRO", "FEVEREIRO", "MARÇO", "ABRIL", "MAIO", "JUNHO", "JULHO", "AGOSTO",
                 "SETEMBRO", "OUTUBRO", "NOVEMBRO", "DEZEMBRO")
        titulo_grande = MESES[datetime.now().month - 1] if self.periodo == "mes" else str(datetime.now().year)

        # Imagem de destaque: FOTO do artista nº 1 (a capa do álbum fica só nas listas do app)
        foto_topo = ""
        if self._artistas:
            try:
                top = {"id": self._artistas[0]["id"], "nome": self._artistas[0]["nome"]}
                perfil = montar_perfil(top, _achar)
                foto_topo = perfil.get("foto") or perfil.get("logo") or ""
            except Exception as erro:
                trilha(f"foto do artista na captura: {erro!r}")
            if not foto_topo:
                foto_topo = logo_do_artista(top, _achar)
        if not foto_topo and self._musicas:
            foto_topo = _achar(self._musicas[0].get("capa"))
        acento = (0.91, 0.12, 0.12, 1)
        if foto_topo:
            try:
                r, g, b = _cor_dominante(_textura_compartilhada(resource_find(foto_topo) or foto_topo))[:3]
                m = max(r, g, b, .01)
                acento = (r / m * .95, g / m * .95, b / m * .95, 1)
            except Exception as erro:
                trilha(f"cor de destaque da captura: {erro!r}")

        relatorio = Widget(size_hint=(None, None), size=(W, H))
        with relatorio.canvas:
            Color(.165, .165, .165, 1)
            Rectangle(pos=(0, 0), size=(W, H))
            Color(.07, .07, .07, 1)
            Rectangle(pos=(0, H - TOPO), size=(W, TOPO))
            Color(.93, .93, .90, 1)                       # bolinhas na borda do topo
            for k in range(10):
                Ellipse(pos=(k * 118 - 10, H - TOPO - 31), size=(62, 62))
            for k in range(8):                            # e na lateral direita
                Ellipse(pos=(W - 40, H - TOPO + 40 + k * 118), size=(62, 62))

        def rotulo(texto, tam, x, y, w, h=None, cor=(1, 1, 1, 1), alinhar="left", negrito=False):
            h = h or int(tam * 1.5)
            r = Label(text=texto, font_size=tam, color=cor, bold=negrito, font_name="DMSansMedium",
                      halign=alinhar, valign="middle", size_hint=(None, None), size=(w, h),
                      pos=(x, y), text_size=(w, h), shorten=True, shorten_from="right")
            relatorio.add_widget(r)
            return r

        # título gigante na vertical (ano, ou nome do mês), na cor de destaque, atrás da foto
        tam = int(min(380, 930 / (max(len(titulo_grande), 4) * .64)))
        ano = Label(text=titulo_grande, font_size=tam, bold=True, font_name="DMSansMedium",
                    color=acento, size_hint=(None, None), size=(1100, 460), pos=(-440, H - 790))
        with ano.canvas.before:
            PushMatrix()
            Rotate(angle=90, origin=(ano.x + ano.width / 2, ano.y + ano.height / 2))
        with ano.canvas.after:
            PopMatrix()
        relatorio.add_widget(ano)

        moldura = Widget(size_hint=(None, None), size=(836, 836), pos=(222, H - 70 - 828))
        with moldura.canvas:
            Color(.96, .96, .94, 1)
            Rectangle(pos=moldura.pos, size=moldura.size)
        relatorio.add_widget(moldura)
        relatorio.add_widget(ImagemArredondada(
            source=foto_topo, size_hint=(None, None), size=(820, 820), pos=(230, H - 70 - 820), raio=0))

        # duas colunas: Top Artistas | Top Músicas
        cinza = (.62, .62, .62, 1)
        colunas = (
            (70, "Top Artistas", [nome_na_tela(x["nome"]) for x in self._artistas[:5]]),
            (570, "Top Músicas", [x["titulo"] for x in self._musicas[:5]]),
        )
        for x, titulo, itens in colunas:
            rotulo(titulo, 46, x, 730, 450, negrito=True)
            for n in range(5):
                texto = f"{n + 1}  {itens[n]}" if n < len(itens) else f"{n + 1}  —"
                rotulo(texto, 42, x, 650 - n * 66, 450, 62, cor=(1, 1, 1, 1) if n < len(itens) else cinza)

        rotulo("Minutos ouvidos", 46, 70, 310, 450, negrito=True)
        rotulo(f"{minutos:,}".replace(",", "."), 110, 70, 170, 470, 140, negrito=True)
        rotulo("Reproduções", 46, 570, 310, 450, negrito=True)
        rotulo(str(resumo.get("reproducoes", 0)), 110, 570, 170, 470, 140, negrito=True)
        rotulo(f"{app.usuario_atual}  •  {self._nome_periodo()}  •  reprodução = 30 s ou mais",
               28, 70, 128, 940, 44, cor=cinza)

        relatorio.add_widget(ImagemArredondada(
            source=_achar("arquivos/icones/icon.png"), size_hint=(None, None),
            size=(96, 96), pos=(70, 22), raio=48))
        rotulo("MY MUSIC GOSPEL", 44, 300, 22, 710, 96, alinhar="right", negrito=True)

        def salvar(_dt):
            temporario = None
            try:
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

        Clock.schedule_once(salvar, 0.7)   # espera o fade das imagens terminar

    def _salvar_em_downloads(self, temporario, nome):
        if platform == "android":
            from jnius import autoclass
            atividade = autoclass("org.kivy.android.PythonActivity").mActivity
            resolver = atividade.getContentResolver()
            sdk = int(autoclass("android.os.Build$VERSION").SDK_INT)
            if sdk >= 29:
                # Padrão Android 10+: MediaStore (scoped storage), sem permissão de armazenamento.
                ContentValues = autoclass("android.content.ContentValues")
                DownloadsStore = autoclass("android.provider.MediaStore$Downloads")
                Inteiro = autoclass("java.lang.Integer")  # put() é sobrecarregado: tipo explícito
                valores = ContentValues()
                valores.put("_display_name", nome)
                valores.put("mime_type", "image/png")
                valores.put("relative_path", "Download/My Music Gospel")
                valores.put("is_pending", Inteiro(1))
                uri = resolver.insert(DownloadsStore.EXTERNAL_CONTENT_URI, valores)
                if uri is None:
                    raise RuntimeError("O Android não criou o arquivo em Downloads.")
                try:
                    saida = resolver.openOutputStream(uri)
                    if saida is None:
                        raise RuntimeError("O Android não abriu o arquivo de destino.")
                    entrada = autoclass("java.io.FileInputStream")(str(temporario))
                    try:
                        # Cópia feita 100% no lado Java (evita converter bytes via pyjnius).
                        autoclass("android.os.FileUtils").copy(entrada, saida)
                        saida.flush()
                    finally:
                        entrada.close()
                        saida.close()

                    publicado = ContentValues()
                    publicado.put("is_pending", Inteiro(0))
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


def _plural(n, singular, plural):
    return f"{n} {singular if n == 1 else plural}"


class TelaArtistas(Screen):
    """Grade de artistas (abre ao tocar em 'Artistas' no menu)."""
    resumo = StringProperty("")

    def montar(self):
        caixa = self.ids.lista_artistas
        caixa.clear_widgets()
        artistas = artistas_ordenados()
        albuns = listar_albuns()
        por_artista = {}
        for album in albuns:
            por_artista[album["artista_id"]] = por_artista.get(album["artista_id"], 0) + 1
        self.resumo = (f"{_plural(len(artistas), 'artista', 'artistas')} · "
                       f"{_plural(len(albuns), 'álbum', 'álbuns')}")
        for artista in artistas:
            caixa.add_widget(LinhaArtista(
                artista_id=artista["id"],
                nome=nome_na_tela(artista["nome"]),
                logo=logo_do_artista(artista, _achar),
                detalhe=_plural(por_artista.get(artista["id"], 0), "álbum", "álbuns"),
            ))
        entrada_em_cascata(caixa.children[::-1], atraso=.03)


class TelaArtista(Screen):
    """Página de UM artista. Estrutura fixa: foto, Biografia, Discografia, Integrantes."""
    nome = StringProperty("")
    logo = StringProperty("")
    foto = StringProperty("")
    resumo = StringProperty("")           # ex.: "3 álbuns · 24 músicas"
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
            integrantes.add_widget(LinhaInfo(
                text=f"[color=ff5a64][b]{escape_markup(funcao)}[/b][/color]\n{escape_markup(nomes)}"))

        self._albuns = perfil["albuns"]
        self.tem_albuns = bool(self._albuns)
        self.tem_mais = False          # a discografia agora é um carrossel com todos os álbuns
        self.expandido = False
        self.resumo = (f"{_plural(len(self._albuns), 'álbum', 'álbuns')} · "
                       f"{_plural(int(artista.get('total_musicas') or 0), 'música', 'músicas')}")
        self._mostrar_capas()
        self.ids.rolagem.scroll_y = 1   # sempre abre no topo

    def recarregar(self):
        """Refaz a página aberta (usado quando o Android libera a leitura das imagens)."""
        if self._artista is not None:
            self.carregar(self._artista)

    def _mostrar_capas(self):
        grade = self.ids.grade_albuns
        grade.clear_widgets()
        for album in self._albuns:
            grade.add_widget(CartaoAlbumMini(
                capa=album["capa"], album_id=album["id"], nome=album["nome"]))

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
    # Filtros: "padrao" (ordem da playlist), "titulo", "artista" ou "album"
    ordem_por = StringProperty("padrao")
    crescente = BooleanProperty(True)
    favoritos_em_cima = BooleanProperty(False)
    filtros_abertos = BooleanProperty(False)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._texto = ""
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
        self._texto = ""
        self.ids.campo_busca.text = ""
        self.reaplicar()

    def reaplicar(self):
        """Busca + ordenação + 'favoritos em cima' -> o que aparece na lista."""
        t = _sem_acento(self._texto)
        lista = [m for m in self.musicas
                 if not t or t in _sem_acento(m["titulo"] + " " + m["artista"])]
        chave = self.ordem_por
        if chave != "padrao":
            lista.sort(key=lambda m: (_sem_acento(m.get(chave) or ""),
                                      _sem_acento(m["titulo"])),
                       reverse=not self.crescente)
        if self.favoritos_em_cima:
            favoritas = set(App.get_running_app().favoritos)
            lista.sort(key=lambda m: m["id"] not in favoritas)  # estável: mantém a ordem
        self.mostrar(lista)

    def definir_ordem(self, chave):
        self.ordem_por = chave
        self.reaplicar()

    def alternar_direcao(self):
        if self.ordem_por != "padrao":
            self.crescente = not self.crescente
            self.reaplicar()

    def alternar_favoritos_em_cima(self):
        self.favoritos_em_cima = not self.favoritos_em_cima
        self.reaplicar()

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
            self._texto = ""
            self.ids.campo_busca.text = ""
            self.reaplicar()  # fechou a busca: volta a lista completa

    def filtrar(self, texto):
        self._texto = texto  # ignora acento e maiúscula (em reaplicar)
        self.reaplicar()

    # ---------------- BOTÕES ----------------
    def tocar_a_partir_de(self, indice):
        App.get_running_app().iniciar_fila(
            self.visiveis, comecar_em=indice, origem=self.nome_playlist)

    def tocar_aleatorio(self):
        App.get_running_app().iniciar_fila(
            self.visiveis, aleatorio=True, origem=self.nome_playlist)


class TelaPlayer(Screen):
    """
    Player. Além do que vem do "app" (veja o main.kv), cuida da LETRA:
      - letra_linha: só a linha cantada agora (letras sincronizadas), mostrada
        entre o artista e a barra de progresso;
      - letra_a2..letra_p2: duas linhas antes, a atual e duas depois, para o
        cartão "Prévia da letra". Letras sem tempo mostram só o começo.
    """
    letra_tem = BooleanProperty(False)
    letra_sincronizada = BooleanProperty(False)
    letra_msg = StringProperty("")
    letra_linha = StringProperty("")
    letra_a2 = StringProperty("")
    letra_a1 = StringProperty("")
    letra_atual = StringProperty("")
    letra_p1 = StringProperty("")
    letra_p2 = StringProperty("")
    letra_alfa = NumericProperty(1)  # pisca de leve quando a linha troca

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._id = None
        self._linhas, self._tempos = [], []
        self._indice = -2
        self._relogio = None

    def on_pre_enter(self, *args):
        self._carregar()  # relê o arquivo (a letra pode ter sido baixada na tela de letras)

    def on_enter(self, *args):
        if self._relogio is None:
            self._relogio = Clock.schedule_interval(self._tick, .25)

    def on_leave(self, *args):
        if self._relogio is not None:
            self._relogio.cancel()
            self._relogio = None

    def _carregar(self):
        app = App.get_running_app()
        musica = app.musica_tocando or {}
        self._id = app.id_atual
        self._linhas, self._tempos, self._indice = [], [], -2
        self.letra_tem = self.letra_sincronizada = False
        self.letra_linha = self.letra_a2 = self.letra_a1 = ""
        self.letra_atual = self.letra_p1 = self.letra_p2 = ""
        arquivo = None
        if musica:
            try:  # mesmas pastas da tela de letras (ela só usa o "app" e globais)
                arquivo = letras.achar_letra(musica, TelaLetra._pastas(self, musica))
            except Exception as erro:
                trilha(f"prévia da letra: busca falhou {erro!r}")
        if arquivo is None:
            self.letra_msg = "Esta música ainda não tem letra no aparelho."
            return
        try:
            linhas, sincronizada = letras.parse_lrc(
                arquivo.read_text(encoding="utf-8", errors="replace"))
        except Exception as erro:
            trilha(f"prévia da letra ilegível {arquivo}: {erro!r}")
            linhas = []
        if not linhas:
            self.letra_msg = "O arquivo de letra está vazio."
            return
        self._tempos = [t for t, _ in linhas]
        self._linhas = [texto for _, texto in linhas]
        self.letra_sincronizada = sincronizada
        self.letra_msg = ""
        self.letra_tem = True
        self._mostrar(-1 if sincronizada else 2)  # sem tempo: mostra as 5 primeiras linhas
        self._tick(0)

    def _mostrar(self, i):
        def linha(k):
            return self._linhas[k] if 0 <= k < len(self._linhas) else ""
        self.letra_a2, self.letra_a1 = linha(i - 2), linha(i - 1)
        self.letra_atual = linha(i) if i >= 0 else ("• • •" if self.letra_sincronizada else "")
        self.letra_p1, self.letra_p2 = linha(i + 1), linha(i + 2)
        self.letra_linha = linha(i) if (self.letra_sincronizada and i >= 0) else ""

    def _tick(self, _dt):
        app = App.get_running_app()
        if app.id_atual != self._id:            # a faixa mudou com o player aberto
            self._carregar()
            return
        if not (self.letra_tem and self.letra_sincronizada):
            return
        indice = letras.indice_atual(self._tempos, app.posicao)
        if indice == self._indice:
            return
        self._indice = indice
        self._mostrar(indice)
        Animation.cancel_all(self, "letra_alfa")
        self.letra_alfa = .3
        Animation(letra_alfa=1, duration=.35, t="out_quad").start(self)


class LinhaLetra(ButtonBehavior, Label):
    """Uma linha da letra. 'brilho' (animado) controla o destaque; tocar pula para o trecho."""
    tempo = NumericProperty(0)
    brilho = NumericProperty(.35)
    sincronizada = BooleanProperty(False)

    def on_release(self):
        app = App.get_running_app()
        if self.sincronizada and app.duracao:
            app.buscar(min(1, max(0, self.tempo / app.duracao)))


class TelaLetra(Screen):
    """Letra da música que está tocando, rolando e destacando a linha atual (estilo Spotify)."""
    titulo = StringProperty("")
    artista = StringProperty("")
    mensagem = StringProperty("")
    tem_letra = BooleanProperty(False)
    buscando = BooleanProperty(False)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._linhas, self._tempos = [], []
        self._sincronizada = False
        self._indice = -2
        self._id = None
        self._relogio = None

    # ---- ciclo de vida
    def on_pre_enter(self, *args):
        self.carregar()

    def on_enter(self, *args):
        if self._relogio is None:
            self._relogio = Clock.schedule_interval(self._tick, .2)
        # Tela já com tamanho final: recentraliza na linha atual sem animar.
        self._indice = -2
        Clock.schedule_once(lambda dt: self._tick(0, animar=False), .1)

    def on_leave(self, *args):
        if self._relogio is not None:
            self._relogio.cancel()
            self._relogio = None

    # ---- pastas onde a letra pode estar (na ordem de prioridade)
    def _pastas(self, musica):
        app = App.get_running_app()
        pastas = [Path(app.user_data_dir) / "letras"]            # baixadas pelo app
        arquivo = Path(_caminho(musica.get("arquivo", "")))
        pastas += [arquivo.parent,                              # .lrc ao lado do áudio
                   RAIZ / "arquivos" / "letras",
                   Path(db.BASE_DIR) / "arquivos" / "letras"]   # as que vão dentro do app
        return pastas

    def carregar(self):
        app = App.get_running_app()
        musica = app.musica_tocando or {}
        self._id = app.id_atual
        self.titulo = app.titulo_atual
        self.artista = app.artista_atual
        self._limpar()
        arquivo = letras.achar_letra(musica, self._pastas(musica)) if musica else None
        if arquivo is None:
            self.mensagem = ("Esta música ainda não tem letra no aparelho.\n"
                             "Toque em buscar para baixar (usa internet só desta vez).")
            return
        try:
            linhas, sincronizada = letras.parse_lrc(arquivo.read_text(encoding="utf-8", errors="replace"))
        except Exception as erro:
            trilha(f"letra ilegível {arquivo}: {erro!r}")
            linhas = []
        if not linhas:
            self.mensagem = "O arquivo de letra está vazio."
            return
        self._montar(linhas, sincronizada)

    def _limpar(self):
        caixa = self.ids.caixa
        caixa.clear_widgets()
        self._linhas, self._tempos, self._indice = [], [], -2
        self.tem_letra = False

    def _montar(self, linhas, sincronizada):
        caixa, rolagem = self.ids.caixa, self.ids.rolagem
        topo = Widget(size_hint_y=None, height=rolagem.height * .35)
        base = Widget(size_hint_y=None, height=rolagem.height * .55)
        rolagem.bind(height=lambda _w, h: (setattr(topo, "height", h * .35), setattr(base, "height", h * .55)))
        caixa.add_widget(topo)
        for tempo, texto in linhas:
            linha = LinhaLetra(text=texto, tempo=tempo, sincronizada=sincronizada,
                               brilho=.35 if sincronizada else .85)
            self._linhas.append(linha)
            caixa.add_widget(linha)
        caixa.add_widget(base)
        self._tempos = [t for t, _ in linhas]
        self._sincronizada = sincronizada
        self.tem_letra = True
        rolagem.scroll_y = 1
        Clock.schedule_once(lambda dt: self._tick(0, animar=False), .05)

    # ---- acompanhar a música
    def _tick(self, _dt, animar=True):
        app = App.get_running_app()
        if app.id_atual != self._id:                 # trocou de faixa com a tela aberta
            self.carregar()
            return
        if not (self.tem_letra and self._sincronizada):
            return
        indice = letras.indice_atual(self._tempos, app.posicao)
        if indice == self._indice:
            return
        self._indice = indice
        for i, linha in enumerate(self._linhas):
            distancia = abs(i - indice)
            alvo = 1 if distancia == 0 else (.55 if distancia == 1 else .32)
            Animation.cancel_all(linha, "brilho")
            Animation(brilho=alvo, duration=.3 if animar else 0, t="out_quad").start(linha)
        if indice >= 0:
            self._centralizar(indice, animar)

    def _centralizar(self, indice, animar=True):
        rolagem, caixa = self.ids.rolagem, self.ids.caixa
        altura, visivel = caixa.height, rolagem.height
        if altura <= visivel:
            return
        linha = self._linhas[indice]
        do_topo = altura - (linha.center_y - caixa.y)
        deslocamento = min(max(do_topo - visivel / 2, 0), altura - visivel)
        alvo = 1 - deslocamento / (altura - visivel)
        Animation.cancel_all(rolagem, "scroll_y")
        if animar:
            Animation(scroll_y=alvo, duration=.45, t="out_cubic").start(rolagem)
        else:
            rolagem.scroll_y = alvo

    # ---- baixar a letra (uma vez; depois fica salva no aparelho)
    def buscar_online(self):
        app = App.get_running_app()
        musica = dict(app.musica_tocando or {})
        if self.buscando or not musica:
            return
        self.buscando = True
        self.mensagem = "Buscando letra…"

        def trabalho():
            texto, erro = None, ""
            try:
                texto, _ = letras.buscar_online(
                    musica.get("titulo", ""), musica.get("artista", ""),
                    musica.get("album", ""), musica.get("duracao_seg") or app.duracao)
            except Exception as falha:
                trilha(f"busca de letra falhou: {falha!r}")
                erro = "Sem conexão com a internet (ou o serviço está fora do ar)."
            Clock.schedule_once(lambda dt: self._fim_busca(musica, texto, erro))

        threading.Thread(target=trabalho, daemon=True).start()

    def _fim_busca(self, musica, texto, erro):
        self.buscando = False
        if texto:
            try:
                letras.salvar(texto, Path(App.get_running_app().user_data_dir) / "letras", musica)
            except Exception as falha:
                trilha(f"não consegui salvar a letra: {falha!r}")
                self.mensagem = "Achei a letra, mas não consegui salvar no aparelho."
                return
            if App.get_running_app().id_atual == self._id:
                self.carregar()
        else:
            self.mensagem = erro or "Não encontrei a letra desta música no LRCLIB."


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
    cor_capa = ListProperty([.12, .12, .12, 1])
    gradiente = ObjectProperty(None)
    save_status = StringProperty("Save automático ativo")   # texto do menu (main.kv)
    favoritos = ListProperty([])   # ids das músicas favoritas (o .kv reage a mudanças)
    tex_orb = ObjectProperty(None, allownone=True)

    def on_capa_atual(self, _instancia, caminho):
        """Troca suave da cor de fundo do player conforme a capa."""
        alvo = [.12, .12, .12, 1]
        try:
            achado = resource_find(caminho) if caminho else None
            if achado:
                alvo = _cor_dominante(_textura_compartilhada(achado))
        except Exception as erro:
            trilha(f"cor da capa indisponível: {erro!r}")
        Animation(cor_capa=alvo, duration=.7, t="out_quad").start(self)

    def _atualizar_insets(self, *_):
        """Android 15+ (edge-to-edge): afasta o conteúdo das barras do sistema."""
        if platform != "android" or not isinstance(self.root, RaizSegura):
            return
        try:
            from jnius import autoclass
            sdk = int(autoclass("android.os.Build$VERSION").SDK_INT)
            if sdk < 35:
                return  # antes do Android 15 o conteúdo já fica fora das barras
            atividade = autoclass("org.kivy.android.PythonActivity").mActivity
            insets = atividade.getWindow().getDecorView().getRootWindowInsets()
            if insets is None:
                Clock.schedule_once(self._atualizar_insets, .4)
                return
            Tipo = autoclass("android.view.WindowInsets$Type")
            barras = insets.getInsets(Tipo.systemBars() | Tipo.displayCutout())
            self.root.padding = [0, int(barras.top), 0, int(barras.bottom)]
        except Exception as erro:
            trilha(f"não consegui medir as barras do sistema: {erro!r}")
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

    def on_tocando(self, _app, tocando):
        """A capa do player 'respira': encolhe suave ao pausar e volta ao tocar."""
        try:
            capa = self.root.get_screen("player").ids.capa_player
            Animation.cancel_all(capa, "escala")
            Animation(escala=1 if tocando else .86, duration=.45,
                      t="out_back" if tocando else "out_cubic").start(capa)
        except Exception:
            pass  # a tela do player ainda não existe: sem animação, sem problema

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

        # SAVE SEPARADO (ver salvamento.py): contas, estatísticas, favoritos e músicas
        # adicionadas ficam num save próprio, que uma atualização/reinstalação não apaga.
        _salvaguarda(salvamento.restaurar_arquivos, self.user_data_dir)
        _salvaguarda(salvamento.snapshot_antes_de_atualizar, self.user_data_dir)
        db.criar_banco()
        # Atualiza o catálogo em instalações que já tinham um banco persistente.
        self._importar_se_mudou()
        _salvaguarda(salvamento.restaurar_dados, self.user_data_dir)
        try:
            self.favoritos = ids_favoritos()
        except Exception as erro:
            trilha(f"favoritos: não consegui carregar: {erro!r}")
        db.limpar_historico_vazio()
        try:
            db.preencher_duracoes_vazias(_caminho)
        except Exception as erro:
            trilha(f"durações pendentes: {erro!r}")
        self.auth_store = AuthStore(Path(self.user_data_dir) / "contas.db")

        gerenciador = ScreenManager(transition=TransicaoIOS(duration=0.36))
        for classe, nome in ((TelaAcesso, "acesso"), (TelaMenu, "menu"),
                             (TelaEstatisticas, "estatisticas"),
                             (TelaPlaylist, "playlist"),
                             (TelaPlayer, "player"), (TelaFila, "fila"),
                             (TelaArtistas, "artistas"), (TelaArtista, "artista"), (TelaLetra, "letra"),
                             (TelaAlbum, "album")):
            gerenciador.add_widget(classe(name=nome))
        Window.bind(on_keyboard=self._tecla)
        # O Android avisa quando está ficando sem memória (logo antes de matar apps).
        Window.bind(on_memorywarning=lambda *a: trilha("!!! AVISO DE MEMÓRIA BAIXA do sistema"))
        self.gradiente = _degrade_vertical()
        self.tex_orb = _tex_orb()
        raiz = RaizSegura(gerenciador)
        Window.bind(size=lambda *a: Clock.schedule_once(self._atualizar_insets, .1))
        Clock.schedule_once(self._atualizar_insets, .2)
        return raiz  # o "root" do app

    def _importar_se_mudou(self):
        """
        Reimporta o biblioteca.py quando ele mudou OU quando o banco está incompleto.

        Antes só valia a "marca" (hash do biblioteca.py) guardada na pasta de dados
        do Kivy, SEPARADA do banco. Se a marca dizia "já importei" mas o banco era
        outro (cópia nova do projeto, banco restaurado, atualização...), as músicas
        novas nunca apareciam. Agora:
          1) a marca é guardada POR BANCO (o nome do arquivo leva o caminho dele);
          2) mesmo com a marca igual, se falta no banco QUALQUER faixa do
             biblioteca.py, importa. O banco manda; a marca só evita trabalho à toa.
        """
        try:
            import biblioteca
            marca = hashlib.md5((db.BASE_DIR / "biblioteca.py").read_bytes()).hexdigest()
            marca += f"-{db.VERSAO_SCHEMA}"
            id_banco = hashlib.md5(str(db.BANCO).encode("utf-8")).hexdigest()[:8]
            arquivo = Path(self.user_data_dir) / f"biblioteca-{id_banco}.hash"
            esperados = {m["arquivo"] for m in biblioteca.musicas.values()}
            with db.conexao() as con:
                no_banco = {linha[0] for linha in con.execute("SELECT arquivo FROM musicas")}
            faltam = esperados - no_banco
            if not faltam and arquivo.exists() and arquivo.read_text() == marca:
                return
            if faltam:
                trilha(f"{len(faltam)} faixa(s) do biblioteca.py faltavam no banco; importando")
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
        self._agendar_save()
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

    def _salvar_dados(self, *_):
        """Grava o save separado (rápido: só reescreve se algo mudou)."""
        _salvaguarda(salvamento.exportar, self.user_data_dir)
        self.save_status = "Último save: " + time.strftime("%H:%M")

    def abrir_salvamento(self):
        """Botão do menu: salva agora e mostra onde o save está."""
        self._salvar_dados()
        destino = "cópia também em Documents/MyMusicGospel" if salvamento.pasta_publica() else "guardado no app"
        self.save_status = f"Salvo às {time.strftime('%H:%M')} · {destino}"

    def _agendar_save(self):
        Clock.unschedule(self._salvar_dados)
        Clock.schedule_once(self._salvar_dados, 4)

    def on_start(self):
        self.playlists = listar_playlists()
        self.root.get_screen("menu").montar(self.playlists)
        try:
            self.root.get_screen("artistas").montar()
        except Exception as erro:       # uma tela com problema não pode derrubar o app inteiro
            trilha(f"artistas: não consegui montar: {erro!r}")
        Clock.schedule_interval(lambda dt: self._salvar_dados(), 120)   # lambda: o unschedule do _agendar_save não pode cancelar este
        self._agendar_save()
        usuario = self.auth_store.usuario_da_sessao()
        if usuario:
            self.usuario_atual = usuario
            self.foto_perfil = self.auth_store.obter_foto_perfil(usuario)
            self.root.current = "menu"
        Clock.schedule_interval(self._tick, 0.25)  # atualiza a barra de progresso

    def on_pause(self):
        trilha("on_pause (app foi para segundo plano / tela bloqueada)")
        self._salvar_dados()
        if self.carregado and self.tocando:
            self._salvar_progresso_estatistica(self.motor.posicao())
            # No Android, o MediaSessionService já é dono do áudio e da fila.
            self._iniciar_servico_audio()
        return True  # sem isso o Android mata o app ao bloquear a tela

    def on_resume(self):
        trilha("on_resume (app voltou)")
        Clock.schedule_once(self._atualizar_insets, .1)
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
    # ---------------- FAVORITOS / MENU DA MÚSICA ----------------
    def alternar_favorita(self, id_musica):
        id_musica = int(id_musica)
        if id_musica < 0:
            return False
        ficou = alternar_favorito(id_musica)
        atuais = set(self.favoritos)
        (atuais.add if ficou else atuais.discard)(id_musica)
        self.favoritos = sorted(atuais)
        self._agendar_save()
        try:    # com "favoritos em cima" ligado a lista precisa se reordenar
            tela = self.root.get_screen("playlist")
            if tela.favoritos_em_cima:
                tela.reaplicar()
        except Exception:
            pass
        return ficou

    def abrir_menu_musica(self, id_musica, titulo, artista, capa, do_player=False):
        if int(id_musica) < 0:
            return
        MenuMusica(id_musica=int(id_musica), titulo=titulo, artista=artista,
                   capa=capa, do_player=do_player).open()

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
        self._salvar_dados()
        if platform == "android":
            # Fechar a Activity não deve destruir o player que vive no serviço.
            return
        if self.carregado:
            self._parar_som()
        else:
            self.motor.parar()


if __name__ == "__main__":
    MainApp().run()
