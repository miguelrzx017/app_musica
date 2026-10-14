"""Duração de arquivos de áudio em segundos, sem depender de bibliotecas externas.

Usa a mutagen se ela existir; senão lê os cabeçalhos de mp3, wav, flac, m4a/aac e ogg.
Devolve None quando não consegue (o app então aprende a duração ao tocar a faixa).
"""
import struct
import wave
from pathlib import Path

_BR_V1 = [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320]
_BR_V2 = [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160]
_SR = {3: [44100, 48000, 32000], 2: [22050, 24000, 16000], 0: [11025, 12000, 8000]}


def _mp3(caminho):
    tamanho = Path(caminho).stat().st_size
    with open(caminho, "rb") as f:
        dados = f.read(512 * 1024)
        inicio = 0
        if dados[:3] == b"ID3":
            inicio = 10 + ((dados[6] << 21) | (dados[7] << 14) | (dados[8] << 7) | dados[9])
            if dados[5] & 0x10:
                inicio += 10
            if inicio + 4096 > len(dados):
                f.seek(inicio)
                dados = f.read(64 * 1024)
                base, inicio = inicio, 0
            else:
                base = 0
        else:
            base = 0
        i = inicio
        while i + 4 <= len(dados):
            if dados[i] == 0xFF and (dados[i + 1] & 0xE0) == 0xE0:
                versao = (dados[i + 1] >> 3) & 3          # 3=MPEG1, 2=MPEG2, 0=2.5
                camada = (dados[i + 1] >> 1) & 3           # 1=Layer III
                br_i, sr_i = dados[i + 2] >> 4, (dados[i + 2] >> 2) & 3
                if versao != 1 and camada == 1 and 0 < br_i < 15 and sr_i < 3:
                    break
            i += 1
        else:
            return None
        sr = _SR[versao][sr_i]
        bitrate = (_BR_V1 if versao == 3 else _BR_V2)[br_i] * 1000
        amostras = 1152 if versao == 3 else 576
        mono = (dados[i + 3] >> 6) == 3
        lado = (17 if mono else 32) if versao == 3 else (9 if mono else 17)
        for pos, marca in ((i + 4 + lado, (b"Xing", b"Info")), (i + 36, (b"VBRI",))):
            if dados[pos:pos + 4] in marca:
                if marca[0] == b"VBRI":
                    quadros = struct.unpack(">I", dados[pos + 14:pos + 18])[0]
                elif dados[pos + 7] & 1:
                    quadros = struct.unpack(">I", dados[pos + 8:pos + 12])[0]
                else:
                    break
                return quadros * amostras / sr
        f.seek(max(0, tamanho - 128))
        fim = 128 if f.read(3) == b"TAG" else 0
    return max(0, tamanho - (base + i) - fim) * 8 / bitrate


def _wav(caminho):
    with wave.open(str(caminho)) as w:
        return w.getnframes() / w.getframerate()


def _flac(caminho):
    with open(caminho, "rb") as f:
        if f.read(4) != b"fLaC":
            return None
        f.read(4)
        d = f.read(34)
    sr = (d[10] << 12) | (d[11] << 4) | (d[12] >> 4)
    total = ((d[13] & 0x0F) << 32) | int.from_bytes(d[14:18], "big")
    return total / sr if sr else None


def _mp4(caminho):
    with open(caminho, "rb") as f:
        f.seek(0, 2)
        fim = f.tell()
        pos = 0
        while pos + 8 <= fim:
            f.seek(pos)
            tam, tipo = struct.unpack(">I4s", f.read(8))
            if tam == 1:
                tam = struct.unpack(">Q", f.read(8))[0]
            if tam < 8:
                return None
            if tipo == b"moov":
                moov = f.read(min(tam, 8 * 1024 * 1024))
                k = moov.find(b"mvhd")
                if k < 0:
                    return None
                v = moov[k + 4]
                if v == 1:
                    esc, dur = struct.unpack(">IQ", moov[k + 24:k + 36])
                else:
                    esc, dur = struct.unpack(">II", moov[k + 16:k + 24])
                return dur / esc if esc else None
            pos += tam
    return None


def _ogg(caminho):
    tamanho = Path(caminho).stat().st_size
    with open(caminho, "rb") as f:
        inicio = f.read(4096)
        f.seek(max(0, tamanho - 65536))
        fim = f.read()
    k = fim.rfind(b"OggS")
    if k < 0:
        return None
    granule = struct.unpack("<q", fim[k + 6:k + 14])[0]
    if b"OpusHead" in inicio:
        return max(0, granule - 3840) / 48000
    v = inicio.find(b"\x01vorbis")
    if v < 0:
        return None
    sr = struct.unpack("<I", inicio[v + 12:v + 16])[0]
    return granule / sr if sr else None


_LEITORES = {".mp3": _mp3, ".wav": _wav, ".flac": _flac, ".m4a": _mp4,
             ".aac": _mp4, ".mp4": _mp4, ".ogg": _ogg, ".oga": _ogg, ".opus": _ogg}


def duracao_do_arquivo(caminho):
    """Duração em segundos (int) ou None se não for possível ler."""
    try:
        import mutagen
        arq = mutagen.File(str(caminho))
        if arq is not None and arq.info.length:
            return int(round(arq.info.length))
    except Exception:
        pass
    try:
        leitor = _LEITORES.get(Path(caminho).suffix.lower())
        segundos = leitor(caminho) if leitor else None
        return int(round(segundos)) if segundos and segundos > 0 else None
    except Exception:
        return None
