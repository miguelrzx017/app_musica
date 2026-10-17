"""
buscar_letras.py - rode no COMPUTADOR (precisa de internet):

    python buscar_letras.py            # baixa as que faltam
    python buscar_letras.py --refazer  # baixa todas de novo

Salva <nome_do_audio>.lrc em arquivos/letras/ (vai junto com o app, funciona offline)
e mostra a cobertura: quantas músicas têm letra sincronizada.
"""
import sys
import time
from pathlib import Path

import db
import letras


def main():
    refazer = "--refazer" in sys.argv
    pasta = Path(db.BASE_DIR) / "arquivos" / "letras"
    pasta.mkdir(parents=True, exist_ok=True)
    musicas = db.listar_musicas()
    sincronizadas, so_texto, faltando, ja_tinha, erros = [], [], [], [], []
    for i, m in enumerate(musicas, 1):
        nome = f"{m['artista']} - {m['titulo']}"
        if not refazer and letras.achar_letra(m, [pasta]):
            ja_tinha.append(nome)
            print(f"[{i}/{len(musicas)}] já tinha: {nome}")
            continue
        try:
            texto, sinc = letras.buscar_online(
                m["titulo"], m["artista"], m.get("album", ""), m.get("duracao_seg") or 0)
        except Exception as erro:
            erros.append(nome)
            print(f"[{i}/{len(musicas)}] ERRO de rede ({erro!r}): {nome}")
            continue
        if texto:
            letras.salvar(texto, pasta, m)
            (sincronizadas if sinc else so_texto).append(nome)
            print(f"[{i}/{len(musicas)}] {'sincronizada' if sinc else 'só texto   '}: {nome}")
        else:
            faltando.append(nome)
            print(f"[{i}/{len(musicas)}] NÃO ENCONTRADA: {nome}")
        time.sleep(0.4)          # gentil com o serviço gratuito

    total = len(musicas)
    print("\n================ COBERTURA ================")
    print(f"Músicas no banco ....... {total}")
    print(f"Sincronizadas .......... {len(sincronizadas)}")
    print(f"Só texto (sem tempo) ... {len(so_texto)}")
    print(f"Já tinham .............. {len(ja_tinha)}")
    print(f"Não encontradas ........ {len(faltando)}")
    print(f"Erros de rede .......... {len(erros)}")
    for titulo, lista in (("Não encontradas", faltando), ("Só texto", so_texto), ("Erros", erros)):
        if lista:
            print(f"\n{titulo}:")
            for nome in lista:
                print("  -", nome)


if __name__ == "__main__":
    main()
