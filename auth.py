"""Cadastro e autenticação local de contas do My Music Gospel."""

import hashlib
import hmac
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path


class AuthStore:
    """Guarda contas neste dispositivo; não sincroniza com um servidor."""

    ITERACOES = 240_000

    def __init__(self, caminho):
        self.caminho = Path(caminho)
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        with self._conectar() as con:
            con.execute("""
                CREATE TABLE IF NOT EXISTS usuarios (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    nome TEXT NOT NULL,
                    nome_chave TEXT NOT NULL UNIQUE,
                    sal BLOB NOT NULL,
                    senha_hash BLOB NOT NULL,
                    criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    foto_perfil TEXT NOT NULL DEFAULT ''
                )
            """)
            colunas = {linha[1] for linha in con.execute("PRAGMA table_info(usuarios)")}
            if "foto_perfil" not in colunas:
                con.execute("ALTER TABLE usuarios ADD COLUMN foto_perfil TEXT NOT NULL DEFAULT ''")
            con.execute("""
                CREATE TABLE IF NOT EXISTS sessao_ativa (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    nome_chave TEXT NOT NULL
                )
            """)

    @contextmanager
    def _conectar(self):
        con = sqlite3.connect(str(self.caminho))
        con.row_factory = sqlite3.Row
        try:
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    @classmethod
    def _hash_senha(cls, senha, sal):
        return hashlib.pbkdf2_hmac(
            "sha256", senha.encode("utf-8"), sal, cls.ITERACOES
        )

    def cadastrar(self, nome, senha):
        nome = " ".join(str(nome).split())
        senha = str(senha)
        if len(nome) < 2 or len(nome) > 40:
            raise ValueError("Informe um nome com 2 a 40 caracteres.")
        if len(senha) < 6:
            raise ValueError("A senha precisa ter pelo menos 6 caracteres.")

        sal = os.urandom(16)
        senha_hash = self._hash_senha(senha, sal)
        try:
            with self._conectar() as con:
                con.execute(
                    "INSERT INTO usuarios (nome, nome_chave, sal, senha_hash) "
                    "VALUES (?, ?, ?, ?)",
                    (nome, nome.casefold(), sal, senha_hash),
                )
        except sqlite3.IntegrityError as erro:
            raise ValueError("Esse nome já está cadastrado neste aparelho.") from erro
        return nome

    def autenticar(self, nome, senha):
        nome_chave = " ".join(str(nome).split()).casefold()
        with self._conectar() as con:
            usuario = con.execute(
                "SELECT nome, sal, senha_hash FROM usuarios WHERE nome_chave = ?",
                (nome_chave,),
            ).fetchone()
        if usuario is None:
            return None
        tentativa = self._hash_senha(str(senha), usuario["sal"])
        if not hmac.compare_digest(tentativa, usuario["senha_hash"]):
            return None
        return usuario["nome"]

    def obter_foto_perfil(self, nome):
        with self._conectar() as con:
            usuario = con.execute(
                "SELECT foto_perfil FROM usuarios WHERE nome_chave = ?",
                (" ".join(str(nome).split()).casefold(),),
            ).fetchone()
        return usuario["foto_perfil"] if usuario else ""

    def salvar_foto_perfil(self, nome, caminho):
        with self._conectar() as con:
            con.execute(
                "UPDATE usuarios SET foto_perfil = ? WHERE nome_chave = ?",
                (str(caminho or ""), " ".join(str(nome).split()).casefold()),
            )

    def salvar_sessao(self, nome):
        nome_chave = " ".join(str(nome).split()).casefold()
        with self._conectar() as con:
            con.execute(
                "INSERT INTO sessao_ativa (id, nome_chave) VALUES (1, ?) "
                "ON CONFLICT(id) DO UPDATE SET nome_chave = excluded.nome_chave",
                (nome_chave,),
            )

    def usuario_da_sessao(self):
        with self._conectar() as con:
            usuario = con.execute("""
                SELECT u.nome
                FROM sessao_ativa s
                JOIN usuarios u ON u.nome_chave = s.nome_chave
                WHERE s.id = 1
            """).fetchone()
        return usuario["nome"] if usuario else None

    def limpar_sessao(self):
        with self._conectar() as con:
            con.execute("DELETE FROM sessao_ativa WHERE id = 1")
