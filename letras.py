"""
letras.py - letras sincronizadas (formato LRC) via LRCLIB, guardadas no aparelho.

Sem dependências do Kivy: roda tanto no app quanto no PC (buscar_letras.py).
A internet só é usada para BAIXAR uma letra uma vez; depois ela fica salva como
<nome_do_audio>.lrc e funciona 100% offline.
"""
import json
import os
import re
import urllib.parse
import urllib.request
from bisect import bisect_right
from pathlib import Path

API = "https://lrclib.net/api"
USER_AGENT = "MyMusicGospel/1.2 (app pessoal; letras offline)"
TOLERANCIA_DURACAO = 8      # segundos: evita baixar a letra de outra versão/música

_MARCA = re.compile(r"\[(\d+):(\d+(?:[.,]\d+)?)\]")
_METADADO = re.compile(r"^\[[A-Za-z]+:.*\]$")     # [ar:...] [ti:...] [length:...]


def parse_lrc(texto):
    """Devolve (linhas, sincronizada). linhas = [(segundos, texto)]."""
    sincronizadas, planas = [], []
    for bruta in (texto or "").splitlines():
        corpo = _MARCA.sub("", bruta).strip()
        marcas = _MARCA.findall(bruta)
        if marcas:
            for minutos, segundos in marcas:
                sincronizadas.append((int(minutos) * 60 + float(segundos.replace(",", ".")), corpo or "• • •"))
        elif corpo and not _METADADO.match(corpo):
            planas.append(corpo)
    if sincronizadas:
        sincronizadas.sort(key=lambda item: item[0])
        return sincronizadas, True
    return [(0.0, linha) for linha in planas], False


def indice_atual(tempos, posicao, adiantamento=0.3):
    """Índice da linha que está cantando agora (-1 = ainda não começou)."""
    return bisect_right(tempos, posicao + adiantamento) - 1


def nome_do_arquivo(musica):
    return Path(str(musica.get("arquivo", "")).replace("\\", "/")).stem


def achar_letra(musica, pastas):
    """Primeiro <stem>.lrc encontrado nas pastas (na ordem dada) ou None."""
    stem = nome_do_arquivo(musica)
    if not stem:
        return None
    for pasta in pastas:
        candidato = Path(pasta) / f"{stem}.lrc"
        if candidato.is_file():
            return candidato
    return None


def salvar(texto, pasta, musica):
    pasta = Path(pasta)
    pasta.mkdir(parents=True, exist_ok=True)
    destino = pasta / f"{nome_do_arquivo(musica)}.lrc"
    destino.write_text(texto.strip() + "\n", encoding="utf-8")
    return destino


# ----------------------------------------------------------------- rede
def _http_get(url, timeout=12):
    """GET -> texto, ou None se 404. No Android usa o HTTPS nativo (certificados do sistema)."""
    if os.environ.get("ANDROID_ARGUMENT"):
        from jnius import autoclass, cast
        URL = autoclass("java.net.URL")
        conexao = cast("java.net.HttpURLConnection", URL(url).openConnection())
        conexao.setRequestProperty("User-Agent", USER_AGENT)
        conexao.setConnectTimeout(timeout * 1000)
        conexao.setReadTimeout(timeout * 1000)
        try:
            codigo = int(conexao.getResponseCode())
            if codigo == 404:
                return None
            if codigo != 200:
                raise RuntimeError(f"HTTP {codigo}")
            leitor = autoclass("java.io.BufferedReader")(
                autoclass("java.io.InputStreamReader")(conexao.getInputStream(), "UTF-8"))
            linhas = []
            while True:
                linha = leitor.readLine()
                if linha is None:
                    break
                linhas.append(str(linha))
            leitor.close()
            return "\n".join(linhas)
        finally:
            conexao.disconnect()
    pedido = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(pedido, timeout=timeout) as resposta:
            return resposta.read().decode("utf-8")
    except urllib.error.HTTPError as erro:
        if erro.code == 404:
            return None
        raise


def _escolher(dados, duracao):
    if not dados:
        return None, False
    if duracao and dados.get("duration") and abs(dados["duration"] - duracao) > TOLERANCIA_DURACAO:
        return None, False
    if dados.get("syncedLyrics"):
        return dados["syncedLyrics"], True
    if dados.get("plainLyrics"):
        return dados["plainLyrics"], False
    return None, False


def buscar_online(titulo, artista, album="", duracao=0):
    """(texto, sincronizada). (None, False) se não achou. Levanta exceção se sem rede."""
    duracao = int(round(duracao or 0))
    if album and duracao:                       # busca exata (mais precisa)
        consulta = urllib.parse.urlencode({
            "track_name": titulo, "artist_name": artista,
            "album_name": album, "duration": duracao})
        try:
            texto, sincronizada = _escolher(json.loads(_http_get(f"{API}/get?{consulta}") or "null"), duracao)
            if texto:
                return texto, sincronizada
        except (ValueError, RuntimeError):
            pass                                  # 400/erro de formato: cai na busca ampla
    consulta = urllib.parse.urlencode({"track_name": titulo, "artist_name": artista})
    lista = json.loads(_http_get(f"{API}/search?{consulta}") or "[]")
    if duracao:
        lista.sort(key=lambda d: abs((d.get("duration") or 0) - duracao))
    lista.sort(key=lambda d: not d.get("syncedLyrics"))      # sincronizadas primeiro (estável)
    for candidato in lista:
        texto, sincronizada = _escolher(candidato, duracao)
        if texto:
            return texto, sincronizada
    return None, False
