# buildozer.spec - configuração para gerar o APK do My Music
#
# Sintaxe tipo .ini. ATENÇÃO: comentário só vale se a linha COMEÇAR com "#"
# (nada de comentário no fim da linha) e linhas indentadas viram parte da
# linha de cima.
#
# Esta versão é enxuta: tirei as opções comentadas que o app não usa (iOS, OSX,
# etc.). Se um dia precisar delas, rode "buildozer init" numa pasta vazia para
# ver o arquivo completo com todas as explicações.

[app]

# Nome que aparece embaixo do ícone
title = My Music

# Identificação do app (não pode ter espaço nem acento)
package.name = mymusic
package.domain = org.test.mymusic

# Pasta do código (onde está o main.py) - "." = a pasta do projeto
source.dir = .

# Tipos de arquivo que entram no APK.
#   ttf  -> fonte DM Sans (arquivos/fontes)
#   db   -> o banco musica.db (dados/), copiado para o celular na 1ª abertura
#   jpeg -> por garantia, além do jpg
# O mp3 NÃO entra de propósito: as músicas ficam fora do APK (veja as
# instruções de copiar a pasta para o celular).
source.include_exts = py,png,jpg,jpeg,kv,atlas,ttf,db

# Pastas que não precisam ir no APK (ambientes virtuais, cache, build antigo...).
# Se uma pasta de ambiente virtual entrar aqui por engano, o build fica lentíssimo.
source.exclude_dirs = bin, venv, .venv, kivy_venv, __pycache__, .git, .buildozer

version = 0.1

# Versão do python-for-android (p4a), a ferramenta que empacota o app.
# A versão mais nova (develop) passou a usar Python 3.14 no Android e quebra
# o build fora do Linux/Python 3.14. Esta versão é estável e testada com o Kivy.
p4a.branch = v2024.01.21

# NDK (compilador do Android) compatível com essa versão do p4a
android.ndk = 25b

# Bibliotecas do app:
#   python3, kivy -> o básico
#   sqlite3       -> o banco de dados (db.py usa o módulo sqlite3)
#   pyjnius       -> deixa o Python falar com o Android (audio.py usa o
#                    MediaPlayer do sistema para pausar/continuar a música)
requirements = python3,kivy,sqlite3,pyjnius

# Só em pé (retrato)
orientation = portrait

fullscreen = 0

# Permissões de leitura de músicas e capas.
#   READ_MEDIA_AUDIO / READ_MEDIA_IMAGES -> Android 13 ou mais novo
#   READ_EXTERNAL_STORAGE                -> Android 12 ou mais antigo
# O pedido na tela (a janelinha "Permitir?") é feito pelo main.py, mas a
# permissão precisa estar declarada aqui, senão o Android recusa sozinho.
android.permissions = android.permission.READ_MEDIA_AUDIO, android.permission.READ_MEDIA_IMAGES, (name=android.permission.READ_EXTERNAL_STORAGE;maxSdkVersion=32)

# Versão do Android para a qual o app é compilado e a mais antiga aceita
android.api = 33
android.minapi = 24

# Processador do celular. arm64-v8a = quase todo celular atual (build mais
# rápido, APK menor). Se o seu celular for MUITO antigo (32 bits), troque por
# "armeabi-v7a" ou use as duas: arm64-v8a, armeabi-v7a
android.archs = arm64-v8a

# Mantém o backup automático do Android (guarda o seu histórico de reproduções)
android.allow_backup = True


[buildozer]

# 0 = só erros | 1 = informações | 2 = tudo (bom para achar problema)
log_level = 2

# Avisa se rodar como root
warn_on_root = 1
