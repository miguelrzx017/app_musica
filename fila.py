"""
fila.py - a FILA de reprodução. Python puro: nada de Kivy aqui.

Por que separar? Porque dá para testar no PC (python fila.py) sem abrir
janela nenhuma. A regra de ouro: lógica em um arquivo, tela em outro.

A fila é só um DICIONÁRIO (você já sabe usar!):
    {
        "itens":    [musica1, musica2, ...],  # cada música é um dict que vem do db.py
        "posicao":  0,                        # índice da música tocando agora
        "aleatorio": False,                   # só informativo (para a interface mostrar)
    }
As funções abaixo recebem esse dicionário, mexem nele e devolvem a música.
"""

import random


def criar_fila(musicas, comecar_em=None, aleatorio=False):
    """
    Monta uma fila nova a partir de uma lista de músicas.

    - Modo normal: toca na ordem da playlist, começando em 'comecar_em'
      (o índice que você tocou na lista; padrão 0).
    - Modo aleatório: embaralha tudo. Se você tocou numa música específica,
      ela vai para o início e só o RESTANTE é embaralhado.
      (Assim ninguém repete até a fila acabar - diferente de "sortear a cada vez".)
    """
    itens = list(musicas)  # list(...) faz uma CÓPIA; não mexemos na lista original

    if aleatorio:
        if comecar_em is None:
            random.shuffle(itens)  # embaralha a lista "no lugar"
        else:
            escolhida = itens.pop(comecar_em)  # pop tira o item da lista e devolve ele
            random.shuffle(itens)
            itens.insert(0, escolhida)  # insert(posição, item) coloca no início
        posicao = 0
    else:
        posicao = comecar_em or 0  # 'None or 0' vira 0

    return {"itens": itens, "posicao": posicao, "aleatorio": aleatorio}


def reiniciar(fila, ordem_original, aleatorio=False, evitar_id=None):
    """
    Reinicia uma fila que chegou ao fim.

    Normal: volta para a primeira música da ordem original.
    Aleatório: cria um novo embaralhamento e, havendo mais de uma faixa,
    evita colocar imediatamente a mesma música que acabou de terminar.
    """
    itens = list(ordem_original or fila.get("itens", []))
    if not itens:
        fila["itens"] = []
        fila["posicao"] = 0
        fila["aleatorio"] = aleatorio
        return None

    if aleatorio:
        random.shuffle(itens)
        if evitar_id is not None and len(itens) > 1 and itens[0].get("id") == evitar_id:
            itens[0], itens[1] = itens[1], itens[0]

    fila["itens"] = itens
    fila["posicao"] = 0
    fila["aleatorio"] = aleatorio
    return musica_atual(fila)


def musica_atual(fila):
    """Devolve a música tocando agora (ou None se a fila estiver vazia)."""
    if 0 <= fila["posicao"] < len(fila["itens"]):
        return fila["itens"][fila["posicao"]]
    return None


def proxima(fila):
    """Avança uma posição. Devolve a nova música, ou None se a fila acabou."""
    if fila["posicao"] + 1 >= len(fila["itens"]):
        return None
    fila["posicao"] += 1
    return musica_atual(fila)


def anterior(fila):
    """Volta uma posição (na primeira música, continua nela)."""
    if fila["posicao"] > 0:
        fila["posicao"] -= 1
    return musica_atual(fila)


def proximas(fila):
    """Lista do que ainda vai tocar (útil para a tela 'Fila')."""
    return fila["itens"][fila["posicao"] + 1:]  # fatiamento: do próximo até o fim


def tocar_em_seguida(fila, musica):
    """'Tocar a seguir': encaixa a música logo depois da atual."""
    fila["itens"].insert(fila["posicao"] + 1, musica)


def _achar_posicao(fila, musica_id):
    """Procura em que índice está a música com esse id (ou None)."""
    for i, m in enumerate(fila["itens"]):  # enumerate dá (índice, item)
        if m["id"] == musica_id:
            return i
    return None


def mover(fila, de, para):
    """
    Arrasta uma música de um índice para outro (reordenar a fila).
    Depois, recalcula 'posicao' para continuar apontando para a música
    que está tocando - senão a fila "pularia" sozinha.
    """
    atual = musica_atual(fila)
    item = fila["itens"].pop(de)
    fila["itens"].insert(para, item)
    if atual is not None:
        fila["posicao"] = _achar_posicao(fila, atual["id"])


def remover(fila, indice):
    """Tira uma música da fila. Não deixa remover a que está tocando."""
    if indice == fila["posicao"]:
        return False
    fila["itens"].pop(indice)
    if indice < fila["posicao"]:  # removeu algo ANTES da atual: tudo andou 1 casa
        fila["posicao"] -= 1
    return True


def embaralhar_restante(fila):
    """
    Embaralha só o que AINDA VAI TOCAR (a música atual e as já tocadas ficam
    onde estão). É o que o botão "Ordem Aleatória" usa.
    """
    inicio = fila["posicao"] + 1
    restante = fila["itens"][inicio:]     # fatiamento = cópia do que falta
    random.shuffle(restante)
    fila["itens"][inicio:] = restante     # troca a fatia pela versão embaralhada
    fila["aleatorio"] = True


def desfazer_aleatorio(fila, ordem_original):
    """
    Desliga o aleatório: reordena o que falta tocar seguindo a ordem da
    playlist ('ordem_original' = lista de músicas na ordem da playlist).
    """
    ordem = {m["id"]: i for i, m in enumerate(ordem_original)}  # id -> posição original
    inicio = fila["posicao"] + 1
    fila["itens"][inicio:] = sorted(
        fila["itens"][inicio:], key=lambda m: ordem.get(m["id"], 0)
    )
    fila["aleatorio"] = False


# ----------------------------------------------------------------------
# Teste rápido no PC:  python fila.py
# ----------------------------------------------------------------------
if __name__ == "__main__":
    teste = [{"id": n, "titulo": f"Faixa {n}"} for n in range(1, 8)]

    f = criar_fila(teste, aleatorio=True)
    print("Aleatória :", [m["id"] for m in f["itens"]])

    f = criar_fila(teste, comecar_em=3)
    print("Começa na 4:", musica_atual(f)["titulo"])

    tocar_em_seguida(f, {"id": 99, "titulo": "Furou a fila"})
    print("Próxima   :", proxima(f)["titulo"])

    mover(f, 0, 6)  # joga a Faixa 1 para o fim
    print("Ainda toca:", musica_atual(f)["titulo"])
    print("Restantes :", [m["id"] for m in proximas(f)])

    embaralhar_restante(f)
    print("Embaralhou:", [m["id"] for m in proximas(f)])
    desfazer_aleatorio(f, teste)
    print("Desfez    :", [m["id"] for m in proximas(f)])
