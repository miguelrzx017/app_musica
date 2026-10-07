"""
db.py - camada de banco de dados do app_musica (SQLite).

Uso, uma vez no PC, na raiz do projeto:
    python db.py        -> cria o banco, importa o biblioteca.py,
                           lê a duração dos mp3 e confere se os arquivos existem

No app (Kivy), use as funções de consulta e de registro:
    from db import listar_musicas, registrar_reproducao, ...

Convenções:
  - 'arquivo' e 'capa' ficam RELATIVOS à pasta raiz da biblioteca. Quem usa
    (player/interface) junta com a raiz: Path(raiz) / musica["arquivo"].
  - Datas ficam em texto ISO local ("2026-10-02T14:30:00"), o que permite
    filtrar por mês com strftime('%Y-%m', reproduzida_em).
"""

import sqlite3
import unicodedata
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

try:
    import mutagen
except ImportError:  # só é necessário para ler a duração dos mp3
    mutagen = None


BASE_DIR = Path(__file__).resolve().parent
PASTA_DADOS = BASE_DIR / "dados"
BANCO = PASTA_DADOS / "musica.db"

VERSAO_SCHEMA = 2


ESQUEMA = """
    CREATE TABLE IF NOT EXISTS artistas (
        id   INTEGER PRIMARY KEY AUTOINCREMENT,
        nome TEXT NOT NULL UNIQUE
    );

    CREATE TABLE IF NOT EXISTS albuns (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        nome       TEXT NOT NULL,
        artista_id INTEGER NOT NULL,
        ano        INTEGER,
        capa       TEXT,

        FOREIGN KEY (artista_id) REFERENCES artistas(id),
        UNIQUE (nome, artista_id)
    );

    CREATE TABLE IF NOT EXISTS generos (
        id   INTEGER PRIMARY KEY AUTOINCREMENT,
        nome TEXT NOT NULL UNIQUE
    );

    CREATE TABLE IF NOT EXISTS musicas (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        titulo      TEXT NOT NULL,
        artista_id  INTEGER NOT NULL,
        album_id    INTEGER NOT NULL,
        arquivo     TEXT NOT NULL UNIQUE,
        duracao_seg INTEGER,

        FOREIGN KEY (artista_id) REFERENCES artistas(id),
        FOREIGN KEY (album_id)   REFERENCES albuns(id)
    );

    CREATE TABLE IF NOT EXISTS musica_genero (
        musica_id INTEGER NOT NULL,
        genero_id INTEGER NOT NULL,

        PRIMARY KEY (musica_id, genero_id),
        FOREIGN KEY (musica_id) REFERENCES musicas(id) ON DELETE CASCADE,
        FOREIGN KEY (genero_id) REFERENCES generos(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS playlists (
        id   INTEGER PRIMARY KEY AUTOINCREMENT,
        nome TEXT NOT NULL UNIQUE
    );

    CREATE TABLE IF NOT EXISTS playlist_musicas (
        playlist_id INTEGER NOT NULL,
        musica_id   INTEGER NOT NULL,
        posicao     INTEGER NOT NULL,

        PRIMARY KEY (playlist_id, musica_id),
        FOREIGN KEY (playlist_id) REFERENCES playlists(id) ON DELETE CASCADE,
        FOREIGN KEY (musica_id)   REFERENCES musicas(id)   ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS historico (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        musica_id        INTEGER NOT NULL,
        usuario          TEXT NOT NULL DEFAULT '',
        reproduzida_em   TEXT NOT NULL,
        segundos_ouvidos INTEGER NOT NULL DEFAULT 0,
        completa         INTEGER NOT NULL DEFAULT 0 CHECK (completa IN (0, 1)),

        FOREIGN KEY (musica_id) REFERENCES musicas(id) ON DELETE CASCADE
    );

    CREATE INDEX IF NOT EXISTS idx_musicas_artista   ON musicas(artista_id);
    CREATE INDEX IF NOT EXISTS idx_musicas_album     ON musicas(album_id);
    CREATE INDEX IF NOT EXISTS idx_albuns_artista    ON albuns(artista_id);
    CREATE INDEX IF NOT EXISTS idx_mgenero_genero    ON musica_genero(genero_id);
    CREATE INDEX IF NOT EXISTS idx_historico_musica  ON historico(musica_id);
    CREATE INDEX IF NOT EXISTS idx_historico_data    ON historico(reproduzida_em);
    CREATE INDEX IF NOT EXISTS idx_historico_usuario ON historico(usuario);
"""


# ----------------------------------------------------------------------
# Conexão
# ----------------------------------------------------------------------

def _sem_acento(texto):
    """'Águas Profundas' -> 'aguas profundas' (para busca)."""
    if texto is None:
        return ""
    nfd = unicodedata.normalize("NFD", str(texto))
    return "".join(c for c in nfd if not unicodedata.combining(c)).lower()


@contextmanager
def conexao(caminho=None):
    """
    Abre o banco, faz commit se tudo deu certo, rollback se der erro e
    sempre fecha. Uso:

        with conexao() as con:
            con.execute(...)
    """
    caminho = Path(caminho) if caminho else BANCO
    caminho.parent.mkdir(parents=True, exist_ok=True)

    con = sqlite3.connect(caminho)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")  # vale só para esta conexão
    con.create_function("sem_acento", 1, _sem_acento, deterministic=True)

    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def _dicts(linhas):
    return [dict(linha) for linha in linhas]


# ----------------------------------------------------------------------
# Criação do banco
# ----------------------------------------------------------------------

def criar_banco():
    with conexao() as con:
        versao = con.execute("PRAGMA user_version").fetchone()[0]

        if versao == 0:
            ja_existe = con.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type = 'table' AND name = 'musicas'"
            ).fetchone()
            if ja_existe:
                raise RuntimeError(
                    f"{BANCO} foi criado por uma versão antiga do db.py "
                    "(sem user_version). Se o histórico estiver vazio, apague "
                    "o arquivo e rode de novo; ele é recriado do biblioteca.py."
                )

        if versao > VERSAO_SCHEMA:
            raise RuntimeError(
                f"Banco na versão {versao}, mas este código só conhece "
                f"até a {VERSAO_SCHEMA}."
            )

        if versao == 1:
            con.execute(
                "ALTER TABLE historico ADD COLUMN usuario TEXT NOT NULL DEFAULT ''"
            )

        con.executescript(ESQUEMA)
        con.execute(f"PRAGMA user_version = {VERSAO_SCHEMA}")


# ----------------------------------------------------------------------
# Importação do biblioteca.py (pode rodar quantas vezes quiser)
# ----------------------------------------------------------------------

def _id_artista(con, nome):
    con.execute("INSERT OR IGNORE INTO artistas (nome) VALUES (?)", (nome,))
    return con.execute(
        "SELECT id FROM artistas WHERE nome = ?", (nome,)
    ).fetchone()[0]


def _id_album(con, nome, artista_id, ano, capa):
    # Se o álbum já existe, atualiza ano e capa (INSERT OR IGNORE não faria).
    con.execute("""
        INSERT INTO albuns (nome, artista_id, ano, capa)
        VALUES (?, ?, ?, ?)
        ON CONFLICT (nome, artista_id)
        DO UPDATE SET ano = excluded.ano, capa = excluded.capa
    """, (nome, artista_id, ano, capa))
    return con.execute(
        "SELECT id FROM albuns WHERE nome = ? AND artista_id = ?",
        (nome, artista_id),
    ).fetchone()[0]


def _id_genero(con, nome):
    con.execute("INSERT OR IGNORE INTO generos (nome) VALUES (?)", (nome,))
    return con.execute(
        "SELECT id FROM generos WHERE nome = ?", (nome,)
    ).fetchone()[0]


def importar_biblioteca(dicionario=None):
    """
    Lê o dicionário 'musicas' do biblioteca.py e grava no banco.
    Reimportar é seguro: o que mudou no biblioteca.py (título, ano, capa,
    gêneros...) é atualizado, e o histórico e a duração são preservados.
    Não apaga músicas que você tirou do biblioteca.py.
    Devolve (totais, avisos).
    """
    if dicionario is None:
        from biblioteca import musicas as dicionario  # import tardio

    avisos = []
    vistos = {}  # (artista, álbum) -> (ano, capa) da primeira faixa do álbum

    with conexao() as con:
        for item in dicionario.values():
            # Ano e capa são do ÁLBUM; se as faixas dele discordarem, avisa.
            chave = (item["artista"], item["album"])
            valores = (item.get("ano"), item.get("capa"))
            if vistos.setdefault(chave, valores) != valores:
                avisos.append(
                    f"Álbum '{item['album']}' ({item['artista']}): ano/capa de "
                    f"'{item['titulo']}' difere da 1ª faixa do álbum; valeu o último."
                )

            artista_id = _id_artista(con, item["artista"])
            album_id = _id_album(
                con, item["album"], artista_id, item.get("ano"), item.get("capa")
            )

            musica_existente = con.execute(
                "SELECT id FROM musicas WHERE arquivo = ?", (item["arquivo"],)
            ).fetchone()

            con.execute("""
                INSERT INTO musicas (titulo, artista_id, album_id, arquivo)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (arquivo)
                DO UPDATE SET titulo = excluded.titulo,
                              artista_id = excluded.artista_id,
                              album_id = excluded.album_id
            """, (item["titulo"], artista_id, album_id, item["arquivo"]))

            musica_id = con.execute(
                "SELECT id FROM musicas WHERE arquivo = ?", (item["arquivo"],)
            ).fetchone()[0]

            # Faixas novas entram na playlist principal em bancos já existentes.
            # Não recoloca faixas antigas que o usuário possa ter removido.
            if musica_existente is None:
                playlist = con.execute(
                    "SELECT id FROM playlists WHERE nome = 'Rock Gospel'"
                ).fetchone()
                if playlist is not None:
                    con.execute("""
                        INSERT OR IGNORE INTO playlist_musicas
                            (playlist_id, musica_id, posicao)
                        SELECT ?, ?, COALESCE(MAX(posicao), 0) + 1
                        FROM playlist_musicas WHERE playlist_id = ?
                    """, (playlist["id"], musica_id, playlist["id"]))

            # Regrava os gêneros para refletir exatamente o biblioteca.py.
            con.execute(
                "DELETE FROM musica_genero WHERE musica_id = ?", (musica_id,)
            )
            for nome_genero in item.get("generos", []):
                con.execute(
                    "INSERT OR IGNORE INTO musica_genero (musica_id, genero_id) "
                    "VALUES (?, ?)",
                    (musica_id, _id_genero(con, nome_genero)),
                )

        totais = {
            tabela: con.execute(f"SELECT COUNT(*) FROM {tabela}").fetchone()[0]
            for tabela in ("artistas", "albuns", "musicas", "generos")
        }

    return totais, avisos


def atualizar_duracoes(raiz, refazer=False):
    """
    Lê a duração (em segundos) dos mp3 com a mutagen e grava em
    musicas.duracao_seg. Por padrão só preenche as que estão vazias.
    Retorna (quantas_atualizadas, lista_de_arquivos_com_problema).
    """
    if mutagen is None:
        raise RuntimeError("Falta a mutagen: pip install mutagen")

    raiz = Path(raiz)
    filtro = "" if refazer else "WHERE duracao_seg IS NULL"
    atualizadas, problemas = 0, []

    with conexao() as con:
        for linha in con.execute(f"SELECT id, arquivo FROM musicas {filtro}").fetchall():
            try:
                arq = mutagen.File(raiz / linha["arquivo"])
                segundos = round(arq.info.length)
            except Exception:  # arquivo ausente, corrompido ou sem tags
                problemas.append(linha["arquivo"])
                continue

            con.execute(
                "UPDATE musicas SET duracao_seg = ? WHERE id = ?",
                (segundos, linha["id"]),
            )
            atualizadas += 1

    return atualizadas, problemas


def verificar_arquivos(raiz):
    """Lista mp3 e capas que estão no banco mas não existem em disco."""
    raiz = Path(raiz)
    faltando = []

    with conexao() as con:
        for m in con.execute("""
            SELECT m.id, m.titulo, m.arquivo, al.capa
            FROM musicas m JOIN albuns al ON al.id = m.album_id
        """):
            if not (raiz / m["arquivo"]).is_file():
                faltando.append(
                    {"id": m["id"], "titulo": m["titulo"],
                     "tipo": "mp3", "caminho": m["arquivo"]}
                )
            if m["capa"] and not (raiz / m["capa"]).is_file():
                faltando.append(
                    {"id": m["id"], "titulo": m["titulo"],
                     "tipo": "capa", "caminho": m["capa"]}
                )

    return faltando


# ----------------------------------------------------------------------
# Consultas (biblioteca)
# ----------------------------------------------------------------------

_SELECT_MUSICA = """
    SELECT m.id, m.titulo, m.arquivo, m.duracao_seg,
           a.id  AS artista_id, a.nome  AS artista,
           al.id AS album_id,   al.nome AS album,
           al.ano, al.capa
    FROM musicas m
    JOIN artistas a  ON a.id  = m.artista_id
    JOIN albuns   al ON al.id = m.album_id
"""


def listar_musicas(busca=None, artista_id=None, album_id=None, genero=None):
    """
    Lista músicas (como dicionários), ordenadas por artista, ano do álbum e
    id. Os filtros se combinam. 'busca' ignora acentos e maiúsculas e olha
    título, artista e álbum.
    """
    onde, params = [], []

    if busca:
        padrao = (
            _sem_acento(busca)
            .replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        )
        onde.append(
            "sem_acento(m.titulo || ' ' || a.nome || ' ' || al.nome) "
            "LIKE ? ESCAPE '\\'"
        )
        params.append(f"%{padrao}%")

    if artista_id is not None:
        onde.append("m.artista_id = ?")
        params.append(artista_id)

    if album_id is not None:
        onde.append("m.album_id = ?")
        params.append(album_id)

    if genero:
        onde.append("""m.id IN (
            SELECT mg.musica_id FROM musica_genero mg
            JOIN generos g ON g.id = mg.genero_id WHERE g.nome = ?)""")
        params.append(genero)

    sql = _SELECT_MUSICA
    if onde:
        sql += " WHERE " + " AND ".join(onde)
    sql += " ORDER BY sem_acento(a.nome), al.ano, al.nome, m.id"

    with conexao() as con:
        return _dicts(con.execute(sql, params))


def obter_musica(musica_id):
    """Uma música (dict) com a lista de gêneros, ou None se não existir."""
    with conexao() as con:
        linha = con.execute(_SELECT_MUSICA + " WHERE m.id = ?", (musica_id,)).fetchone()
        if linha is None:
            return None

        musica = dict(linha)
        musica["generos"] = [
            g["nome"] for g in con.execute("""
                SELECT g.nome FROM musica_genero mg
                JOIN generos g ON g.id = mg.genero_id
                WHERE mg.musica_id = ? ORDER BY g.nome
            """, (musica_id,))
        ]
        return musica


def listar_artistas():
    with conexao() as con:
        return _dicts(con.execute("""
            SELECT a.id, a.nome, COUNT(m.id) AS total_musicas
            FROM artistas a LEFT JOIN musicas m ON m.artista_id = a.id
            GROUP BY a.id ORDER BY sem_acento(a.nome)
        """))


def listar_albuns(artista_id=None):
    sql = """
        SELECT al.id, al.nome, al.ano, al.capa,
               a.id AS artista_id, a.nome AS artista,
               COUNT(m.id) AS total_musicas
        FROM albuns al
        JOIN artistas a ON a.id = al.artista_id
        LEFT JOIN musicas m ON m.album_id = al.id
    """
    params = []
    if artista_id is not None:
        sql += " WHERE al.artista_id = ?"
        params.append(artista_id)
    sql += " GROUP BY al.id ORDER BY sem_acento(a.nome), al.ano, al.nome"

    with conexao() as con:
        return _dicts(con.execute(sql, params))


def listar_generos():
    with conexao() as con:
        return _dicts(con.execute("""
            SELECT g.id, g.nome, COUNT(mg.musica_id) AS total_musicas
            FROM generos g LEFT JOIN musica_genero mg ON mg.genero_id = g.id
            GROUP BY g.id ORDER BY total_musicas DESC, g.nome
        """))


# ----------------------------------------------------------------------
# Histórico de reprodução
# ----------------------------------------------------------------------

def registrar_reproducao(musica_id, segundos_ouvidos, completa=False, quando=None,
                         usuario=""):
    """
    Grava uma reprodução no histórico e devolve o id dela.
    'quando' é o início da reprodução (datetime); padrão: agora.
    Decida no player quando contar (sugestão: >= 30 s ou >= 50% da faixa).
    """
    quando = (quando or datetime.now()).isoformat(timespec="seconds")

    with conexao() as con:
        cursor = con.execute(
            "INSERT INTO historico "
            "(musica_id, usuario, reproduzida_em, segundos_ouvidos, completa) "
            "VALUES (?, ?, ?, ?, ?)",
            (musica_id, str(usuario or ""), quando, int(segundos_ouvidos),
             1 if completa else 0),
        )
        return cursor.lastrowid


def atualizar_reproducao(registro_id, segundos_ouvidos, completa=False):
    """Atualiza o progresso já salvo de uma sessão de escuta."""
    with conexao() as con:
        con.execute(
            "UPDATE historico SET segundos_ouvidos = ?, completa = ? WHERE id = ?",
            (int(segundos_ouvidos), 1 if completa else 0, int(registro_id)),
        )


def historico_recente(limite=50):
    with conexao() as con:
        return _dicts(con.execute("""
            SELECT h.id, h.reproduzida_em, h.segundos_ouvidos, h.completa,
                   m.id AS musica_id, m.titulo, a.nome AS artista
            FROM historico h
            JOIN musicas m  ON m.id = h.musica_id
            JOIN artistas a ON a.id = m.artista_id
            ORDER BY h.reproduzida_em DESC, h.id DESC
            LIMIT ?
        """, (limite,)))


def estatisticas_musicas(usuario, desde=None, limite=100):
    """Faixas mais ouvidas pelo usuário, com execuções, tempo e capa."""
    where = "h.usuario = ?"
    params = [str(usuario)]
    if desde:
        where += " AND h.reproduzida_em >= ?"
        params.append(desde)
    params.append(int(limite))
    with conexao() as con:
        return _dicts(con.execute(f"""
            SELECT m.id, m.titulo, a.nome AS artista, al.nome AS album,
                   al.capa, COUNT(h.id) AS reproducoes,
                   SUM(h.segundos_ouvidos) AS segundos_ouvidos,
                   SUM(h.completa) AS completas,
                   MAX(h.reproduzida_em) AS ultima_reproducao
            FROM historico h
            JOIN musicas m ON m.id = h.musica_id
            JOIN artistas a ON a.id = m.artista_id
            JOIN albuns al ON al.id = m.album_id
            WHERE {where}
            GROUP BY m.id
            ORDER BY reproducoes DESC, segundos_ouvidos DESC,
                     sem_acento(m.titulo)
            LIMIT ?
        """, params))


def estatisticas_artistas(usuario, desde=None, limite=100):
    """Artistas mais ouvidos pelo usuário, incluindo faixas distintas."""
    where = "h.usuario = ?"
    params = [str(usuario)]
    if desde:
        where += " AND h.reproduzida_em >= ?"
        params.append(desde)
    params.append(int(limite))
    with conexao() as con:
        return _dicts(con.execute(f"""
            SELECT a.id, a.nome, COUNT(h.id) AS reproducoes,
                   COUNT(DISTINCT m.id) AS musicas_diferentes,
                   SUM(h.segundos_ouvidos) AS segundos_ouvidos,
                   SUM(h.completa) AS completas
            FROM historico h
            JOIN musicas m ON m.id = h.musica_id
            JOIN artistas a ON a.id = m.artista_id
            WHERE {where}
            GROUP BY a.id
            ORDER BY reproducoes DESC, segundos_ouvidos DESC,
                     sem_acento(a.nome)
            LIMIT ?
        """, params))


def resumo_estatisticas(usuario, desde=None):
    """Resumo geral do histórico de escuta do usuário."""
    where = "h.usuario = ?"
    params = [str(usuario)]
    if desde:
        where += " AND h.reproduzida_em >= ?"
        params.append(desde)
    with conexao() as con:
        linha = con.execute(f"""
            SELECT COUNT(h.id) AS reproducoes,
                   COUNT(DISTINCT h.musica_id) AS musicas_diferentes,
                   COUNT(DISTINCT m.artista_id) AS artistas_diferentes,
                   COALESCE(SUM(h.segundos_ouvidos), 0) AS segundos_ouvidos,
                   COALESCE(SUM(h.completa), 0) AS completas
            FROM historico h
            JOIN musicas m ON m.id = h.musica_id
            WHERE {where}
        """, params).fetchone()
        return dict(linha)


# ----------------------------------------------------------------------
# Playlists (básico; remover/reordenar ficam para quando a tela precisar)
# ----------------------------------------------------------------------

def criar_playlist(nome):
    """Cria a playlist (ou devolve o id da que já existe com esse nome)."""
    with conexao() as con:
        con.execute("INSERT OR IGNORE INTO playlists (nome) VALUES (?)", (nome,))
        return con.execute(
            "SELECT id FROM playlists WHERE nome = ?", (nome,)
        ).fetchone()[0]


def listar_playlists():
    with conexao() as con:
        return _dicts(con.execute("""
            SELECT p.id, p.nome, COUNT(pm.musica_id) AS total_musicas
            FROM playlists p
            LEFT JOIN playlist_musicas pm ON pm.playlist_id = p.id
            GROUP BY p.id ORDER BY sem_acento(p.nome)
        """))


def adicionar_a_playlist(playlist_id, musica_id):
    """Adiciona ao fim. Devolve False se a música já estava na playlist."""
    with conexao() as con:
        cursor = con.execute("""
            INSERT OR IGNORE INTO playlist_musicas (playlist_id, musica_id, posicao)
            SELECT ?, ?, COALESCE(MAX(posicao), 0) + 1
            FROM playlist_musicas WHERE playlist_id = ?
        """, (playlist_id, musica_id, playlist_id))
        return cursor.rowcount == 1


def musicas_da_playlist(playlist_id):
    with conexao() as con:
        return _dicts(con.execute(
            _SELECT_MUSICA
            + " JOIN playlist_musicas pm ON pm.musica_id = m.id"
            + " WHERE pm.playlist_id = ? ORDER BY pm.posicao",
            (playlist_id,),
        ))


# ----------------------------------------------------------------------

if __name__ == "__main__":
    criar_banco()
    totais, avisos = importar_biblioteca()
    print("Banco criado e biblioteca importada:", totais)
    for aviso in avisos:
        print("  AVISO:", aviso)

    try:
        n, problemas = atualizar_duracoes(BASE_DIR)
        print(f"Durações lidas: {n}" + (f" | com problema: {problemas}" if problemas else ""))
    except RuntimeError as erro:
        print("Aviso:", erro)

    for item in verificar_arquivos(BASE_DIR):
        print(f"  FALTANDO ({item['tipo']}): #{item['id']} {item['titulo']} -> {item['caminho']}")
