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
# Padrão do mercado: visualização = pelo menos 30 segundos ouvidos.
LIMIAR_VISUALIZACAO_SEGUNDOS = 30


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

    CREATE TABLE IF NOT EXISTS favoritos (
        musica_id     INTEGER PRIMARY KEY,
        favoritada_em TEXT NOT NULL DEFAULT (datetime('now')),

        FOREIGN KEY (musica_id) REFERENCES musicas(id) ON DELETE CASCADE
    );

    CREATE INDEX IF NOT EXISTS idx_musicas_artista   ON musicas(artista_id);
    CREATE INDEX IF NOT EXISTS idx_musicas_album     ON musicas(album_id);
    CREATE INDEX IF NOT EXISTS idx_albuns_artista    ON albuns(artista_id);
    CREATE INDEX IF NOT EXISTS idx_mgenero_genero    ON musica_genero(genero_id);
    CREATE INDEX IF NOT EXISTS idx_historico_musica  ON historico(musica_id);
    CREATE INDEX IF NOT EXISTS idx_historico_data    ON historico(reproduzida_em);
    CREATE INDEX IF NOT EXISTS idx_historico_usuario ON historico(usuario);
    CREATE INDEX IF NOT EXISTS idx_historico_usuario_data ON historico(usuario, reproduzida_em);
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
    con.execute("PRAGMA busy_timeout = 5000")
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

def _colunas_tabela(con, tabela):
    """Devolve os nomes das colunas de uma tabela existente."""
    return {
        linha["name"]
        for linha in con.execute(f'PRAGMA table_info("{tabela}")')
    }


def _migracoes_banco_sem_versao(con):
    """Prepara migrações aditivas para bancos antigos, sem descartar dados."""
    tabelas = {
        linha[0]
        for linha in con.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name NOT LIKE 'sqlite_%'"
        )
    }
    if not tabelas:
        return []
    if "musicas" not in tabelas:
        raise RuntimeError(
            "O banco local não tem uma tabela de músicas reconhecida. "
            "Os dados foram preservados; não apague o arquivo musica.db."
        )

    # Estas colunas são usadas pelas consultas atuais. Só adicionamos as duas
    # colunas opcionais que versões anteriores deste app deixavam de gravar.
    obrigatorias = {
        "artistas": {"id", "nome"},
        "albuns": {"id", "nome", "artista_id"},
        "musicas": {"id", "titulo", "artista_id", "album_id", "arquivo"},
        "historico": {
            "id", "musica_id", "reproduzida_em", "segundos_ouvidos", "completa"
        },
    }
    for tabela, colunas_necessarias in obrigatorias.items():
        if tabela not in tabelas:
            continue  # ESQUEMA recria tabelas auxiliares que ainda não existam.
        faltantes = colunas_necessarias - _colunas_tabela(con, tabela)
        if faltantes:
            lista = ", ".join(sorted(faltantes))
            raise RuntimeError(
                f"O banco local tem um formato antigo não reconhecido "
                f"({tabela}: {lista}). Os dados foram preservados."
            )

    migracoes = []
    colunas_musicas = _colunas_tabela(con, "musicas")
    if "duracao_seg" not in colunas_musicas:
        migracoes.append("ALTER TABLE musicas ADD COLUMN duracao_seg INTEGER;")

    if "historico" in tabelas and "usuario" not in _colunas_tabela(con, "historico"):
        migracoes.append(
            "ALTER TABLE historico ADD COLUMN usuario TEXT NOT NULL DEFAULT '';"
        )
    return migracoes


def criar_banco():
    with conexao() as con:
        # SQLite wal permite consultas enquanto o serviço grava o progresso.
        con.execute("PRAGMA journal_mode = WAL")
        con.execute("PRAGMA synchronous = NORMAL")
        versao = con.execute("PRAGMA user_version").fetchone()[0]

        if versao > VERSAO_SCHEMA:
            raise RuntimeError(
                f"Banco na versão {versao}, mas este código só conhece "
                f"até a {VERSAO_SCHEMA}."
            )

        migracoes = []
        if versao == 0:
            migracoes = _migracoes_banco_sem_versao(con)
        if versao == 1:
            if ("historico" in {
                    linha[0] for linha in con.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
            } and "usuario" not in _colunas_tabela(con, "historico")):
                migracoes.append(
                    "ALTER TABLE historico ADD COLUMN usuario TEXT NOT NULL DEFAULT '';"
                )

        # Faz as alterações e a atualização da versão na mesma transação. Se
        # uma tabela for incompatível, o SQLite reverte a migração sem apagá-la.
        sql = "BEGIN IMMEDIATE;\n" + "\n".join(migracoes) + "\n" + ESQUEMA
        sql += f"\nPRAGMA user_version = {VERSAO_SCHEMA};\nCOMMIT;"
        con.executescript(sql)


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
    Grava o progresso de uma sessão e devolve o id dela.
    'quando' é o início da reprodução (datetime); padrão: agora.
    As consultas estatísticas contam a sessão a partir de 30 s ouvidos.
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


def atualizar_duracao_musica(musica_id, segundos):
    """Grava a duração de uma faixa (usado ao descobrir a duração tocando)."""
    with conexao() as con:
        con.execute("UPDATE musicas SET duracao_seg = ? WHERE id = ?",
                    (int(segundos), int(musica_id)))


def preencher_duracoes_vazias(resolver):
    """Calcula a duração das faixas sem duração. 'resolver' converte o caminho do banco em arquivo."""
    from duracao import duracao_do_arquivo
    with conexao() as con:
        for linha in con.execute(
                "SELECT id, arquivo FROM musicas WHERE duracao_seg IS NULL OR duracao_seg <= 0").fetchall():
            segundos = duracao_do_arquivo(resolver(linha["arquivo"]))
            if segundos:
                con.execute("UPDATE musicas SET duracao_seg = ? WHERE id = ?",
                            (segundos, linha["id"]))


def contagem_sessoes(usuario, desde=None):
    """(sessões do usuário, sessões com 30 s ou mais, sessões de todos) - para diagnóstico."""
    filtro, params = ("AND reproduzida_em >= ?", [desde]) if desde else ("", [])
    with conexao() as con:
        total, validas = con.execute(
            f"SELECT COUNT(*), COALESCE(SUM(segundos_ouvidos >= {LIMIAR_VISUALIZACAO_SEGUNDOS}), 0) "
            f"FROM historico WHERE usuario = ? {filtro}", [str(usuario)] + params).fetchone()
        todos = con.execute(
            f"SELECT COUNT(*) FROM historico WHERE 1=1 {filtro}", params).fetchone()[0]
    return total, int(validas), todos


def limpar_historico_vazio():
    """Remove sessões sem nenhum segundo ouvido (ruído deixado por pulos rápidos)."""
    with conexao() as con:
        con.execute("DELETE FROM historico WHERE segundos_ouvidos <= 0")


def adicionar_musica(titulo, artista, album, arquivo, ano=None, capa="",
                     generos=(), playlist_id=None, duracao=None):
    """
    Cadastra uma música enviada pelo usuário. Reaproveita artista/álbum que já
    existem (ignorando acento e maiúscula) e cria os que não existem.
    'arquivo' e 'capa' são caminhos absolutos. Devolve o id da música.
    """
    titulo, artista, album = (str(x or "").strip() for x in (titulo, artista, album))
    if not (titulo and artista and album and arquivo):
        raise ValueError("Preencha título, artista, álbum e escolha o arquivo.")
    try:
        ano = int(ano) if str(ano or "").strip() else None
    except ValueError:
        raise ValueError("O ano precisa ser um número (ex.: 2024).")

    with conexao() as con:
        if con.execute("SELECT 1 FROM musicas WHERE arquivo = ?", (arquivo,)).fetchone():
            raise ValueError("Esta música já está na biblioteca.")

        linha = con.execute(
            "SELECT id FROM artistas WHERE sem_acento(nome) = sem_acento(?)", (artista,)
        ).fetchone()
        artista_id = linha[0] if linha else _id_artista(con, artista)

        linha = con.execute(
            "SELECT id FROM albuns WHERE artista_id = ? AND sem_acento(nome) = sem_acento(?)",
            (artista_id, album),
        ).fetchone()
        if linha:
            album_id = linha[0]
            if capa:
                con.execute("UPDATE albuns SET capa = ? WHERE id = ?", (capa, album_id))
            if ano:
                con.execute("UPDATE albuns SET ano = ? WHERE id = ?", (ano, album_id))
        else:
            album_id = con.execute(
                "INSERT INTO albuns (nome, artista_id, ano, capa) VALUES (?, ?, ?, ?)",
                (album, artista_id, ano, capa or None),
            ).lastrowid

        musica_id = con.execute(
            "INSERT INTO musicas (titulo, artista_id, album_id, arquivo, duracao_seg) "
            "VALUES (?, ?, ?, ?, ?)",
            (titulo, artista_id, album_id, arquivo, duracao),
        ).lastrowid

        for nome in generos:
            nome = str(nome).strip().lower()
            if nome:
                con.execute(
                    "INSERT OR IGNORE INTO musica_genero (musica_id, genero_id) VALUES (?, ?)",
                    (musica_id, _id_genero(con, nome)),
                )
        if playlist_id is not None:
            con.execute("""
                INSERT OR IGNORE INTO playlist_musicas (playlist_id, musica_id, posicao)
                SELECT ?, ?, COALESCE(MAX(posicao), 0) + 1
                FROM playlist_musicas WHERE playlist_id = ?
            """, (playlist_id, musica_id, playlist_id))
        return musica_id


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
    """Faixas visualizadas (ao menos 30 s), com tempo total e capa."""
    where = "h.usuario = ?"
    params = [str(usuario)]
    if desde:
        where += " AND h.reproduzida_em >= ?"
        params.append(desde)
    params.append(int(limite))
    with conexao() as con:
        return _dicts(con.execute(f"""
            SELECT m.id, m.titulo, a.nome AS artista, al.nome AS album,
                   al.capa,
                   COUNT(CASE WHEN h.segundos_ouvidos >= {LIMIAR_VISUALIZACAO_SEGUNDOS}
                              THEN h.id END) AS reproducoes,
                   SUM(h.segundos_ouvidos) AS segundos_ouvidos,
                   SUM(CASE WHEN h.segundos_ouvidos >= {LIMIAR_VISUALIZACAO_SEGUNDOS}
                            THEN h.completa ELSE 0 END) AS completas,
                   MAX(CASE
                       WHEN h.segundos_ouvidos >= {LIMIAR_VISUALIZACAO_SEGUNDOS}
                       THEN h.reproduzida_em END) AS ultima_reproducao
            FROM historico h
            JOIN musicas m ON m.id = h.musica_id
            JOIN artistas a ON a.id = m.artista_id
            JOIN albuns al ON al.id = m.album_id
            WHERE {where}
            GROUP BY m.id
            HAVING COUNT(CASE WHEN h.segundos_ouvidos >= {LIMIAR_VISUALIZACAO_SEGUNDOS}
                              THEN h.id END) > 0
            ORDER BY reproducoes DESC, segundos_ouvidos DESC,
                     sem_acento(m.titulo)
            LIMIT ?
        """, params))


def estatisticas_artistas(usuario, desde=None, limite=100):
    """Artistas de faixas visualizadas, incluindo faixas distintas."""
    where = "h.usuario = ?"
    params = [str(usuario)]
    if desde:
        where += " AND h.reproduzida_em >= ?"
        params.append(desde)
    params.append(int(limite))
    with conexao() as con:
        return _dicts(con.execute(f"""
            SELECT a.id, a.nome,
                   COUNT(CASE WHEN h.segundos_ouvidos >= {LIMIAR_VISUALIZACAO_SEGUNDOS}
                              THEN h.id END) AS reproducoes,
                   COUNT(DISTINCT CASE
                       WHEN h.segundos_ouvidos >= {LIMIAR_VISUALIZACAO_SEGUNDOS}
                       THEN m.id END) AS musicas_diferentes,
                   SUM(h.segundos_ouvidos) AS segundos_ouvidos,
                   SUM(CASE WHEN h.segundos_ouvidos >= {LIMIAR_VISUALIZACAO_SEGUNDOS}
                            THEN h.completa ELSE 0 END) AS completas
            FROM historico h
            JOIN musicas m ON m.id = h.musica_id
            JOIN artistas a ON a.id = m.artista_id
            WHERE {where}
            GROUP BY a.id
            HAVING COUNT(CASE WHEN h.segundos_ouvidos >= {LIMIAR_VISUALIZACAO_SEGUNDOS}
                              THEN h.id END) > 0
            ORDER BY reproducoes DESC, segundos_ouvidos DESC,
                     sem_acento(a.nome)
            LIMIT ?
        """, params))


def resumo_estatisticas(usuario, desde=None):
    """Resumo: conta visualizações com 30 s e soma todo o tempo ouvido."""
    where = "h.usuario = ?"
    params = [str(usuario)]
    if desde:
        where += " AND h.reproduzida_em >= ?"
        params.append(desde)
    with conexao() as con:
        linha = con.execute(f"""
            SELECT COUNT(CASE
                       WHEN h.segundos_ouvidos >= {LIMIAR_VISUALIZACAO_SEGUNDOS}
                       THEN h.id END) AS reproducoes,
                   COUNT(DISTINCT CASE
                       WHEN h.segundos_ouvidos >= {LIMIAR_VISUALIZACAO_SEGUNDOS}
                       THEN h.musica_id END) AS musicas_diferentes,
                   COUNT(DISTINCT CASE
                       WHEN h.segundos_ouvidos >= {LIMIAR_VISUALIZACAO_SEGUNDOS}
                       THEN m.artista_id END) AS artistas_diferentes,
                   COALESCE(SUM(h.segundos_ouvidos), 0) AS segundos_ouvidos,
                   COALESCE(SUM(CASE
                       WHEN h.segundos_ouvidos >= {LIMIAR_VISUALIZACAO_SEGUNDOS}
                       THEN h.completa ELSE 0 END), 0) AS completas
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


def ids_favoritos():
    """Ids das músicas favoritas (lista de inteiros)."""
    with conexao() as con:
        return [linha[0] for linha in con.execute(
            "SELECT musica_id FROM favoritos ORDER BY favoritada_em")]


def alternar_favorito(musica_id):
    """Favorita/desfavorita. Devolve True se a música ficou favorita."""
    with conexao() as con:
        apagou = con.execute(
            "DELETE FROM favoritos WHERE musica_id = ?", (musica_id,)).rowcount
        if apagou:
            return False
        con.execute("INSERT OR IGNORE INTO favoritos (musica_id) VALUES (?)", (musica_id,))
        return True


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
