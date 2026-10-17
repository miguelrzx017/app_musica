"""
SAVE SEPARADO do My Music Gospel.

Problema: estatísticas, contas e músicas adicionadas viviam dentro dos mesmos arquivos
que o app recria/atualiza (musica.db, contas.db). Se o banco era refeito ou o app
precisava ser reinstalado, tudo sumia.

Agora os dados do USUÁRIO ficam num "save" independente do catálogo:
  <user_data_dir>/save/usuario.json   histórico, favoritos e músicas adicionadas
                                       (ligados às músicas pelo caminho do arquivo,
                                       não por ids do banco, que podem mudar)
  <user_data_dir>/save/contas.db      cópia consistente das contas
  <user_data_dir>/save/backups/       versões antigas do save e do banco
  Documents/MyMusicGospel/            espelho público (Android), quando o sistema
                                       permitir - é o que sobrevive a desinstalar o app

Na abertura o app RESTAURA o que faltar (sem apagar nada) e, enquanto roda, SALVA
sozinho (ao pausar, ao fechar, após mudanças e a cada 2 minutos).
"""
import hashlib
import json
import os
import shutil
import sqlite3
import threading
import time
import uuid
from pathlib import Path

import db

PUBLICO = Path("/storage/emulated/0/Documents/MyMusicGospel")
MAX_ESPELHO = 400 * 1024 * 1024      # teto do que é copiado de áudio para o espelho
_trava = threading.Lock()


def _log(texto):
    print("[save]", texto)


def pasta_save(user_dir):
    pasta = Path(user_dir) / "save"
    (pasta / "backups").mkdir(parents=True, exist_ok=True)
    return pasta


def pasta_publica():
    """Pasta pública (só Android) onde o espelho pode ser gravado; None se não der."""
    if "ANDROID_ARGUMENT" not in os.environ:
        return None
    try:
        PUBLICO.mkdir(parents=True, exist_ok=True)
        teste = PUBLICO / ".teste"
        teste.write_text("ok")
        teste.unlink()
        return PUBLICO
    except Exception:
        return None


def _copiar_atomico(origem, destino):
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    tmp = destino.with_name(destino.name + ".tmp")
    shutil.copyfile(origem, tmp)
    os.replace(tmp, destino)


def _backup_sqlite(origem, destino):
    """Cópia consistente de um .db (mesmo com o app usando o arquivo)."""
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    tmp = destino.with_name(destino.name + ".tmp")
    tmp.unlink(missing_ok=True)
    src = sqlite3.connect(str(origem))
    dst = sqlite3.connect(str(tmp))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    os.replace(tmp, destino)


def _podar(pasta, padrao, manter):
    arquivos = sorted(pasta.glob(padrao), key=lambda p: p.stat().st_mtime, reverse=True)
    for velho in arquivos[manter:]:
        velho.unlink(missing_ok=True)


# ----------------------------------------------------------------------
# COLETAR / EXPORTAR
# ----------------------------------------------------------------------
def _id_do_banco(con):
    con.execute("CREATE TABLE IF NOT EXISTS meta (chave TEXT PRIMARY KEY, valor TEXT)")
    linha = con.execute("SELECT valor FROM meta WHERE chave = 'save_id'").fetchone()
    if linha:
        return linha[0]
    novo = uuid.uuid4().hex
    con.execute("INSERT INTO meta (chave, valor) VALUES ('save_id', ?)", (novo,))
    return novo


def _coletar():
    with db.conexao() as con:
        save_id = _id_do_banco(con)
        historico = [list(r) for r in con.execute(
            "SELECT m.arquivo, h.usuario, h.reproduzida_em, h.segundos_ouvidos, h.completa "
            "FROM historico h JOIN musicas m ON m.id = h.musica_id ORDER BY h.id")]
        try:
            favoritos = [list(r) for r in con.execute(
                "SELECT m.arquivo, f.favoritada_em FROM favoritos f "
                "JOIN musicas m ON m.id = f.musica_id ORDER BY f.favoritada_em")]
        except sqlite3.Error:
            favoritos = []
        adicionadas = []
        for r in con.execute(
                "SELECT m.id, m.titulo, a.nome, al.nome, al.ano, al.capa, m.arquivo, m.duracao_seg "
                "FROM musicas m JOIN artistas a ON a.id = m.artista_id "
                "JOIN albuns al ON al.id = m.album_id "
                "WHERE m.arquivo LIKE '%minhas_musicas%'").fetchall():
            generos = [x[0] for x in con.execute(
                "SELECT g.nome FROM musica_genero mg JOIN generos g ON g.id = mg.genero_id "
                "WHERE mg.musica_id = ?", (r[0],))]
            playlists = [x[0] for x in con.execute(
                "SELECT p.nome FROM playlist_musicas pm JOIN playlists p ON p.id = pm.playlist_id "
                "WHERE pm.musica_id = ?", (r[0],))]
            adicionadas.append({"titulo": r[1], "artista": r[2], "album": r[3], "ano": r[4],
                                "capa": r[5] or "", "arquivo": r[6], "duracao": r[7],
                                "generos": generos, "playlists": playlists})
    return {"versao": 1, "save_id": save_id, "historico": historico,
            "favoritos": favoritos, "adicionadas": adicionadas}


def exportar(user_dir):
    """Grava o save (rápido; só reescreve se algo mudou). Devolve True se gravou."""
    with _trava:
        dados = _coletar()
        pasta = pasta_save(user_dir)
        corpo = json.dumps(dados, ensure_ascii=False, indent=1, sort_keys=True)
        alvo = pasta / "usuario.json"
        mudou = (not alvo.exists()) or alvo.read_text(encoding="utf-8") != corpo
        if mudou:
            if alvo.exists():
                stamp = time.strftime("%Y%m%d-%H%M%S")
                shutil.copyfile(alvo, pasta / "backups" / f"usuario-{stamp}.json")
                _podar(pasta / "backups", "usuario-*.json", 8)
            tmp = alvo.with_name("usuario.json.tmp")
            tmp.write_text(corpo, encoding="utf-8")
            os.replace(tmp, alvo)
        contas = Path(user_dir) / "contas.db"
        if contas.exists():
            _backup_sqlite(contas, pasta / "contas.db")
    threading.Thread(target=_espelhar, args=(user_dir,), daemon=True).start()
    return mudou


def _copiar_arvore(origem, destino, limite=None):
    if not origem.is_dir():
        return
    total = 0
    for arquivo in origem.rglob("*"):
        if not arquivo.is_file() or arquivo.suffix == ".tmp":
            continue
        alvo = destino / arquivo.relative_to(origem)
        tamanho = arquivo.stat().st_size
        if alvo.exists() and alvo.stat().st_size == tamanho:
            continue
        total += tamanho
        if limite and total > limite:
            _log("espelho: limite de tamanho atingido; o restante fica só no app")
            return
        _copiar_atomico(arquivo, alvo)


def _espelhar(user_dir):
    pub = pasta_publica()
    if pub is None:
        return
    try:
        pasta = pasta_save(user_dir)
        for nome in ("usuario.json", "contas.db"):
            if (pasta / nome).exists():
                _copiar_atomico(pasta / nome, pub / nome)
        _copiar_arvore(Path(user_dir) / "fotos_perfil", pub / "fotos_perfil")
        _copiar_arvore(Path(user_dir) / "minhas_musicas", pub / "minhas_musicas", MAX_ESPELHO)
    except Exception as erro:
        _log(f"espelho público falhou: {erro!r}")


# ----------------------------------------------------------------------
# RESTAURAR
# ----------------------------------------------------------------------
def _fontes(user_dir):
    pub = pasta_publica()
    return [pasta_save(user_dir)] + ([pub] if pub else [])


def restaurar_arquivos(user_dir):
    """ANTES de abrir o banco/contas: traz de volta o que sumiu."""
    fontes = _fontes(user_dir)
    contas = Path(user_dir) / "contas.db"
    if not contas.exists():
        for fonte in fontes:
            if (fonte / "contas.db").exists():
                _copiar_atomico(fonte / "contas.db", contas)
                _log(f"contas restauradas de {fonte}")
                break
    fotos = Path(user_dir) / "fotos_perfil"
    for fonte in fontes[1:]:
        if not fotos.exists() and (fonte / "fotos_perfil").is_dir():
            shutil.copytree(fonte / "fotos_perfil", fotos)


def snapshot_antes_de_atualizar(user_dir):
    """Guarda uma cópia do banco sempre que o app/catálogo mudou (antes de migrar)."""
    pasta = pasta_save(user_dir)
    marca = hashlib.md5((db.BASE_DIR / "biblioteca.py").read_bytes()).hexdigest()
    marca += f"-{db.VERSAO_SCHEMA}"
    estado = pasta / "estado.json"
    anterior = json.loads(estado.read_text("utf-8")).get("marca") if estado.exists() else None
    if anterior != marca and Path(db.BANCO).exists():
        stamp = time.strftime("%Y%m%d-%H%M%S")
        _backup_sqlite(db.BANCO, pasta / "backups" / f"musica-{stamp}.db")
        _podar(pasta / "backups", "musica-*.db", 3)
    estado.write_text(json.dumps({"marca": marca}), encoding="utf-8")


def _ler_save(user_dir):
    for fonte in _fontes(user_dir):
        arquivo = fonte / "usuario.json"
        if arquivo.exists():
            try:
                return json.loads(arquivo.read_text(encoding="utf-8"))
            except Exception as erro:
                _log(f"save ilegível em {arquivo}: {erro!r}")
    return None


def _recuperar_arquivo(caminho, user_dir):
    """Se o áudio/capa sumiu do app mas está no espelho público, copia de volta."""
    caminho = Path(caminho)
    if caminho.exists() or not str(caminho):
        return caminho.exists()
    pub = pasta_publica()
    if pub:
        for candidato in (pub / "minhas_musicas" / caminho.name,
                          pub / "minhas_musicas" / "capas" / caminho.name):
            if candidato.exists():
                _copiar_atomico(candidato, caminho)
                return True
    return False


def restaurar_dados(user_dir):
    """
    DEPOIS de criar/importar o banco: se este banco NÃO é o da linhagem do save
    (banco novo, refeito ou reinstalado), devolve histórico, favoritos e músicas
    adicionadas. Só ACRESCENTA; nunca apaga. Retorna um texto para o log.
    """
    dados = _ler_save(user_dir)
    if not dados:
        return "sem save anterior"
    with db.conexao() as con:
        if _id_do_banco(con) == dados.get("save_id"):
            return "banco já é o do save (nada a restaurar)"
    novas = 0
    for m in dados.get("adicionadas", []):
        if not _recuperar_arquivo(m["arquivo"], user_dir):
            _log(f"música adicionada sem arquivo, ignorada: {m['titulo']}")
            continue
        _recuperar_arquivo(m.get("capa") or "", user_dir)
        with db.conexao() as con:
            existe = con.execute("SELECT 1 FROM musicas WHERE arquivo = ?", (m["arquivo"],)).fetchone()
        if existe:
            continue
        playlist_id = None
        for nome in m.get("playlists", []):
            with db.conexao() as con:
                linha = con.execute("SELECT id FROM playlists WHERE nome = ?", (nome,)).fetchone()
            playlist_id = linha[0] if linha else db.criar_playlist(nome)
            break
        db.adicionar_musica(m["titulo"], m["artista"], m["album"], m["arquivo"], m.get("ano"),
                            m.get("capa") or "", m.get("generos", ()), playlist_id,
                            duracao=m.get("duracao"))
        novas += 1
    with db.conexao() as con:
        ids = {arq: i for i, arq in con.execute("SELECT id, arquivo FROM musicas")}
        h = 0
        for arq, usuario, quando, seg, completa in dados.get("historico", []):
            mid = ids.get(arq)
            if mid is None or con.execute(
                    "SELECT 1 FROM historico WHERE musica_id=? AND usuario=? AND reproduzida_em=?",
                    (mid, usuario, quando)).fetchone():
                continue
            con.execute("INSERT INTO historico (musica_id, usuario, reproduzida_em, segundos_ouvidos, completa) "
                        "VALUES (?, ?, ?, ?, ?)", (mid, usuario, quando, seg, completa))
            h += 1
        f = 0
        try:
            for arq, quando in dados.get("favoritos", []):
                if arq in ids:
                    f += con.execute("INSERT OR IGNORE INTO favoritos (musica_id, favoritada_em) VALUES (?, ?)",
                                     (ids[arq], quando)).rowcount
        except sqlite3.Error as erro:
            _log(f"favoritos não restaurados: {erro!r}")
        con.execute("INSERT OR REPLACE INTO meta (chave, valor) VALUES ('save_id', ?)",
                    (dados["save_id"],))
    return f"restaurado: {novas} músicas adicionadas, {h} reproduções, {f} favoritos"
