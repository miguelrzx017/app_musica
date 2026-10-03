"""
criar_playlist.py - rode UMA vez no PC:  python criar_playlist.py

Cria a playlist "Rock Gospel" no banco e coloca nela as músicas do
biblioteca.py. É seguro rodar de novo (o db.py ignora músicas repetidas).
"""

from db import (_sem_acento, adicionar_a_playlist, criar_banco, criar_playlist,
                importar_biblioteca, listar_musicas, listar_playlists)


def chave_ordem(musica):
    # Na sua imagem a ordem é alfabética IGNORANDO acento e espaços
    # ("A Alegria..." < "Acusador" < "A Ele"). Reproduzimos isso aqui.
    # 'key=' diz ao sorted() o que ele deve comparar em cada item.
    return _sem_acento(musica["titulo"]).replace(" ", "")


criar_banco()          # garante que as tabelas existem
importar_biblioteca()  # garante que o biblioteca.py está no banco

playlist_id = criar_playlist("Rock Gospel")

for musica in sorted(listar_musicas(), key=chave_ordem):
    adicionar_a_playlist(playlist_id, musica["id"])

print("Playlists agora:", listar_playlists())
