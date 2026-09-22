import ipywidgets as widgets
from IPython.display import display
import matplotlib.pyplot as plt

from DataManager import DataManager

VENTANA_INICIAL = 10000


def crear_widget(manager: DataManager, registro_inicial: int = 24):
    """Crea un navegador de registros compatible con cualquier numero de canales."""
    estado = {"inicio": 0, "ventana": VENTANA_INICIAL}
    actualizando = False
    out = widgets.Output()

    w_registro = widgets.IntText(
        value=registro_inicial,
        description="Registro:",
        layout=widgets.Layout(width="170px"),
    )
    w_inicio = widgets.IntText(value=0, description="Inicio:", layout=widgets.Layout(width="200px"))
    w_ventana = widgets.IntText(
        value=VENTANA_INICIAL,
        description="Ventana:",
        layout=widgets.Layout(width="200px"),
    )
    w_evento = widgets.IntText(value=20, description="Event win:", layout=widgets.Layout(width="170px"))
    w_media = widgets.Checkbox(value=True, description="show_mean_max", indent=False)

    b_ini = widgets.Button(description="Inicio", tooltip="Ir al inicio")
    b_prev = widgets.Button(description="◀ Ventana", tooltip="Retroceder una ventana")
    b_prev2 = widgets.Button(description="◁ ½", tooltip="Retroceder media ventana", layout=widgets.Layout(width="70px"))
    b_next2 = widgets.Button(description="½ ▷", tooltip="Avanzar media ventana", layout=widgets.Layout(width="70px"))
    b_next = widgets.Button(description="Ventana ▶", tooltip="Avanzar una ventana")
    b_zin = widgets.Button(description="Zoom +", button_style="info")
    b_zout = widgets.Button(description="Zoom −", button_style="info")

    def dibujar():
        nonlocal actualizando
        estado["ventana"] = max(10, min(estado["ventana"], manager.recording_length))
        estado["inicio"] = max(
            0,
            min(estado["inicio"], manager.recording_length - estado["ventana"]),
        )

        actualizando = True
        w_inicio.value = int(estado["inicio"])
        w_ventana.value = int(estado["ventana"])
        actualizando = False

        inicio, ventana = estado["inicio"], estado["ventana"]
        with out:
            out.clear_output(wait=True)
            manager.plot_channels(
                w_registro.value,
                show_mean_max=w_media.value,
                event_time_window=w_evento.value,
                zoom=(inicio, inicio + ventana),
            )
            plt.show()
            plt.close("all")

    def mover(fraccion_ventana):
        def callback(_):
            estado["inicio"] += int(fraccion_ventana * estado["ventana"])
            dibujar()

        return callback

    def ir_inicio(_):
        estado["inicio"] = 0
        dibujar()

    def cambiar_zoom(factor):
        def callback(_):
            centro = estado["inicio"] + estado["ventana"] / 2
            estado["ventana"] = max(10, int(estado["ventana"] * factor))
            estado["inicio"] = int(centro - estado["ventana"] / 2)
            dibujar()

        return callback

    def al_cambiar_control(_):
        nonlocal actualizando
        if actualizando:
            return
        estado["inicio"] = w_inicio.value
        estado["ventana"] = max(10, w_ventana.value)
        dibujar()

    b_ini.on_click(ir_inicio)
    b_prev.on_click(mover(-1))
    b_prev2.on_click(mover(-0.5))
    b_next2.on_click(mover(0.5))
    b_next.on_click(mover(1))
    b_zin.on_click(cambiar_zoom(0.5))
    b_zout.on_click(cambiar_zoom(2))
    for control in (w_inicio, w_ventana, w_registro, w_evento, w_media):
        control.observe(al_cambiar_control, names="value")

    fila_nav = widgets.HBox([b_ini, b_prev, b_prev2, b_next2, b_next, b_zin, b_zout])
    fila_parametros = widgets.HBox([w_registro, w_inicio, w_ventana, w_evento, w_media])
    interfaz = widgets.VBox([fila_nav, fila_parametros, out])
    display(interfaz)
    dibujar()
    return interfaz