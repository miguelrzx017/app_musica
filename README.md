# My Music Gospel

## Gerar o APK no Google Colab

Use o notebook `Buildozer_Google_Colab.ipynb` que você já tem no Colab e envie o arquivo atualizado `Projeto_My_Music_Gospel_Colab_Atualizado.zip` quando a primeira célula pedir um ZIP. O nome foi mantido para que o notebook existente continue compatível. Veja [INSTRUCOES_COLAB.md](INSTRUCOES_COLAB.md).

O notebook gera um APK de depuração para instalação direta e teste. Para publicar uma atualização na Play Store, compile a versão de lançamento em `.aab` e assine com a chave de upload do aplicativo publicado.

## Compatibilidade e conteúdo

- Android 7.0 (API 24) ou superior.
- Arquiteturas ARM de 32 e 64 bits (`armeabi-v7a` e `arm64-v8a`).
- O projeto usa Android API 36 como alvo.
- A versão do aplicativo nesta atualização é `1.0.5`; o identificador continua `org.miguelribeiro.mymusicgospel`.
- A versão 1.0.5 desativa o renderizador HWUI do Android apenas na activity Kivy como mitigação para o mutex destruído registrado no Android 16. A interface do Kivy continua usando a superfície SDL/OpenGL.
- As imagens mantêm os arquivos atuais sem nova compressão ou redimensionamento nesta atualização.
- O APK e o ZIP não incluem MP3. No aparelho, coloque as músicas em `Armazenamento interno/Music/AppMusica/arquivos/musicas/`.
- Esta atualização migra bancos locais antigos reconhecidos sem apagar o histórico.

Se o app ainda fechar ao abrir e você não tiver ADB, siga [as instruções para gerar um relatório no próprio Android](INSTRUCOES_DIAGNOSTICO_ANDROID.md).

## Música e estatísticas

No Android, o Media3 mantém a reprodução em segundo plano. As estatísticas contam uma reprodução depois de pelo menos 60 segundos efetivamente ouvidos. Arquivos de música e banco local ficam no dispositivo.
