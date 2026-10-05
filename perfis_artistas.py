"""
perfis_artistas.py - o que aparece nas telas "Artistas" e na página de cada artista.

Cada página de artista tem SEMPRE a mesma estrutura:
    foto  ->  Biografia  ->  Discografia  ->  Integrantes

De onde vem cada parte:
    foto / logo   -> biblioteca.py  (chaves "foto" e "foto_de_perfil" de cada artista)
    discografia   -> banco de dados (álbuns do artista, do mais antigo para o mais novo)
    biografia     -> PERFIS (abaixo) e, se o artista não estiver lá, o "descricao" do biblioteca.py
    integrantes   -> PERFIS (por função) e, se não estiver lá, a lista "integrantes" do biblioteca.py

COMO COMPLETAR UM ARTISTA (copie o modelo do Oficina G3):
    1. A chave é o nome EXATO do artista no banco (ex.: "Fernandinho", "fhop music").
    2. "biografia" é uma lista: cada texto entre aspas vira um parágrafo.
    3. "integrantes" é uma lista de pares (função, nomes). Cada par vira uma linha.
"""

import db
from biblioteca import obter_artista

# ----------------------------------------------------------------------
# ORDEM DA LISTA "ARTISTAS" (nomes como estão no banco).
# Quem não estiver aqui aparece depois, em ordem alfabética.
# ----------------------------------------------------------------------
ORDEM_ARTISTAS = [
    "Oficina G3",
    "Fernandinho",
    "Thalles Roberto",
    "Discopraise",
    "Resgate",
    "fhop music",
    "PG",
    "Juninho Afram",
    "Eli Soares",
    "David Quinlan",
]

# Como o nome aparece na tela, quando é diferente do nome do banco.
NOMES_EXIBICAO = {
    "Discopraise": "DiscoPraise",
    "fhop music": "fhop Music",
}

# ----------------------------------------------------------------------
# BIOGRAFIAS E INTEGRANTES COMPLETOS
# ----------------------------------------------------------------------
PERFIS = {
    "Oficina G3": {
        "biografia": [
            "O Oficina G3 é uma das maiores e mais influentes bandas de rock cristão e "
            "metal cristão do Brasil, fundada em 1987 na cidade de São Paulo. O grupo "
            "nasceu a partir de músicos da igreja Cristo Salva que formavam o terceiro "
            "grupo de louvor do local, apelidado inicialmente de “Grupo 3” ou “G3”. "
            "Posteriormente, o termo “Oficina” foi adicionado para simbolizar a proposta "
            "de conserto e restauração de vidas por meio do evangelho. Em seus primeiros "
            "anos, liderada pelo vocalista Luciano Manga e pelo virtuoso guitarrista "
            "Juninho Afram — único membro fundador presente em todas as fases da "
            "carreira —, a banda enfrentou considerável resistência de alas mais "
            "conservadoras das igrejas, que viam o rock com preconceito. Contudo, o "
            "Oficina G3 rompeu essas barreiras ao demonstrar alta qualidade técnica e "
            "profissionalismo, abrindo espaço para a música pesada dentro do cenário "
            "gospel nacional.",

            "Ao longo de quase quatro décadas de trajetória, a banda passou por diversas "
            "transições sonoras e mudanças marcantes em sua formação, transitando com "
            "maestria pelo hard rock, pop rock, nu metal e metal progressivo. A virada "
            "dos anos 1990 para os anos 2000 consolidou a entrada do baixista Duca "
            "Tambasco e do tecladista Jean Carlos, além da chegada do vocalista P.G., "
            "fase caracterizada por um som mais acessível e estrondoso sucesso comercial, "
            "evidenciado por álbuns emblemáticos como O Tempo. Após a saída de P.G., o "
            "grupo retomou uma sonoridade mais pesada e técnica. Sob o comando dos vocais "
            "de Mauro Henrique, lançaram o aclamado disco Depois da Guerra em 2008, "
            "trabalho que consagrou a relevância artística da banda ao vencer o "
            "prestigiado prêmio Grammy Latino em 2009 na categoria de Melhor Álbum de "
            "Música Cristã em Língua Portuguesa.",

            "Após um período de hiato iniciado no final da década de 2010, o Oficina G3 "
            "retornou aos palcos nos anos 2020 celebrando o seu rico legado musical por "
            "meio de turnês comemorativas de grande sucesso, como a Humanos Tour e a DDG "
            "Reloaded Tour, que reuniram diferentes gerações de fãs e ex-integrantes. "
            "Reconhecido internacionalmente por apresentações na Europa, Estados Unidos, "
            "América Latina e Japão, o Oficina G3 permanece ativo como o trio Juninho "
            "Afram, Duca Tambasco e Jean Carllos. A banda mantém firmada sua marca na "
            "história da música brasileira como o principal expoente do rock com temática "
            "cristã, provando que o estilo representa uma manifestação sólida, duradoura "
            "e de altíssimo nível artístico.",
        ],
        "integrantes": [
            ("Vocalista(s)", "Luciano Manga, PG, Mauro Henrique, Juninho Afram"),
            ("Guitarristas", "Juninho Afram"),
            ("Baixistas", "Duca Tambasco"),
            ("Tecladistas", "Jean Carllos"),
            ("Baterista(s)", "Walter Lopes, Lufe, Johnny Mazza, Alexandre Aposan, Maick Sousa"),
        ],
    },
}

SEM_BIOGRAFIA = "Ainda não há biografia cadastrada para este artista."


def artistas_ordenados():
    """Artistas do banco na ordem de ORDEM_ARTISTAS (os demais, em ordem alfabética)."""
    posicao = {nome: i for i, nome in enumerate(ORDEM_ARTISTAS)}
    fim = len(posicao)
    return sorted(
        db.listar_artistas(),
        key=lambda a: (posicao.get(a["nome"], fim), db._sem_acento(a["nome"])),
    )


def nome_na_tela(nome):
    return NOMES_EXIBICAO.get(nome, nome)


def logo_do_artista(artista, achar):
    """Fotinho redonda da lista. 'achar' é a função do main.py que localiza o arquivo."""
    return achar(obter_artista(artista["nome"]).get("foto_de_perfil"))


def montar_perfil(artista, achar):
    """
    Junta tudo o que a página do artista mostra. 'artista' é uma linha de
    db.listar_artistas() e 'achar' localiza arquivos (_achar do main.py).
    """
    info = obter_artista(artista["nome"])
    extra = PERFIS.get(artista["nome"], {})

    # Biografia: lista de parágrafos
    paragrafos = list(extra.get("biografia") or [])
    if not paragrafos:
        paragrafos = [info.get("descricao") or SEM_BIOGRAFIA]

    # Integrantes: lista de (função, nomes)
    integrantes = list(extra.get("integrantes") or [])
    if not integrantes:
        nomes = [n for n in info.get("integrantes", []) if n]
        if nomes:
            # "Artista: Fernandinho" (solo) x "Integrantes: A, B, C" (grupo ou equipe)
            solo = len(nomes) == 1 and db._sem_acento(nomes[0]) == db._sem_acento(artista["nome"])
            rotulo = "Artista" if solo else "Integrantes"
            integrantes = [(rotulo, ", ".join(nomes))]

    # Discografia: db.listar_albuns já vem do mais antigo para o mais novo
    capas = [achar(album.get("capa")) for album in db.listar_albuns(artista["id"])]

    return {
        "nome": nome_na_tela(artista["nome"]),
        "logo": achar(info.get("foto_de_perfil")),
        "foto": achar(info.get("foto")),
        "biografia": paragrafos,
        "integrantes": integrantes,
        "capas": capas,
    }
