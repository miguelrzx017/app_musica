# My Music Gospel

## Gerar o APK Android

O Buildozer precisa de Linux. No Windows, use uma distribuição Ubuntu no WSL 2 e instale nela as ferramentas do Buildozer e do Android. Na pasta do projeto, execute:

```bash
buildozer android debug
```

O APK de depuração será salvo em `bin/`. O primeiro build baixa SDK, NDK e dependências e pode demorar. Para instalar em um celular conectado por USB com depuração autorizada, use `buildozer android debug deploy run`.

## Músicas no celular

Os MP3 ficam fora do APK e do pacote de compilação. O catálogo do app espera as músicas em `Armazenamento interno/Music/AppMusica/arquivos/musicas/`, com os nomes cadastrados no catálogo. Para esta faixa, use `depois_da_guerra.mp3`. Se baixar um ZIP de áudio, extraia-o sem criar uma pasta extra no caminho.