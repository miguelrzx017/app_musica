from pathlib import Path

from kivy.app import App
from kivy.core.audio import SoundLoader
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.boxlayout import BoxLayout

from biblioteca import musicas


BASE_DIR = Path(__file__).resolve().parent


class MusicaApp(App):

    def build(self):
        layout = BoxLayout(
            orientation='vertical',
            padding=20,
            spacing=20
        )

        musica = musicas[1]

        titulo = Label(
            text=musica['titulo'],
            font_size='24sp'
        )

        artista = Label(
            text=musica['artista'],
            font_size='18sp'
        )

        botao = Button(
            text='TOCAR',
            size_hint=(1, 0.25)
        )

        botao.bind(
            on_release=lambda instance: self.tocar(musica)
        )

        layout.add_widget(titulo)
        layout.add_widget(artista)
        layout.add_widget(botao)

        return layout

    def tocar(self, musica):
        caminho = BASE_DIR / musica['arquivo']

        self.sound = SoundLoader.load(str(caminho))

        if self.sound:
            self.sound.play()
            print(f'Tocando: {musica["titulo"]}')
        else:
            print(f'Não foi possível carregar: {caminho}')


if __name__ == '__main__':
    MusicaApp().run()