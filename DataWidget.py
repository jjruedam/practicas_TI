from io import BytesIO

import ipywidgets as widgets
from IPython.display import display
import matplotlib.pyplot as plt
import numpy as np

from DataManager import DataManager, SAMPLE_INTERVAL

VENTANA_INICIAL = 1000


def crear_widget(
    manager: DataManager,
    registro_inicial: int = 24,
    n: int | None = None,
):
    """Crea un navegador de registros compatible con cualquier numero de canales."""
    if n is not None and (not isinstance(n, (int, np.integer)) or n < 1):
        raise ValueError("n debe ser un entero positivo.")

    estado = {"inicio": 0, "ventana": VENTANA_INICIAL}
    actualizando = False
    grafica = widgets.Image(format="png")

    w_registro = widgets.IntText(
        value=registro_inicial,
        description="Registro:",
        layout=widgets.Layout(width="170px"),
    )
    w_inicio = widgets.FloatText(value=0, description="Inicio (ms):", layout=widgets.Layout(width="200px"))
    w_ventana = widgets.FloatText(
        value=VENTANA_INICIAL,
        description="Ventana (ms):",
        layout=widgets.Layout(width="200px"),
    )
    w_evento = widgets.FloatText(value=2, description="Event win. (ms):", layout=widgets.Layout(width="190px"))
    w_media = widgets.Checkbox(value=True, description="show_mean_max", indent=False)
    w_n = (
        widgets.IntText(
            value=int(n),
            description="n (muestras):",
            layout=widgets.Layout(width="170px"),
        )
        if n is not None
        else None
    )

    b_ini = widgets.Button(description="Inicio", tooltip="Ir al inicio")
    b_prev = widgets.Button(description="◀ Ventana", tooltip="Retroceder una ventana")
    b_prev2 = widgets.Button(description="◁ ½", tooltip="Retroceder media ventana", layout=widgets.Layout(width="70px"))
    b_next2 = widgets.Button(description="½ ▷", tooltip="Avanzar media ventana", layout=widgets.Layout(width="70px"))
    b_next = widgets.Button(description="Ventana ▶", tooltip="Avanzar una ventana")
    b_zin = widgets.Button(description="Zoom +", button_style="info")
    b_zout = widgets.Button(description="Zoom −", button_style="info")

    def dibujar():
        nonlocal actualizando
        max_window_ms = manager.recording_length * SAMPLE_INTERVAL
        estado["ventana"] = max(SAMPLE_INTERVAL, min(estado["ventana"], max_window_ms))
        estado["inicio"] = max(
            0,
            min(estado["inicio"], max_window_ms - estado["ventana"]),
        )

        actualizando = True
        w_inicio.value = estado["inicio"]
        w_ventana.value = estado["ventana"]
        actualizando = False

        inicio, ventana = estado["inicio"], estado["ventana"]
        if w_n is None:
            fig, _ = manager.plot_channels(
                w_registro.value,
                show_mean_max=w_media.value,
                event_time_window=w_evento.value,
                zoom=(inicio, inicio + ventana),
            )
        else:
            window_size = w_n.value
            if window_size < 1:
                raise ValueError("n debe ser un entero positivo.")

            recording = manager.get_recording(w_registro.value)
            reduced_by_channel = manager.mean_per_window(window_size)
            reduced = np.column_stack(
                [
                    reduced_by_channel[name][w_registro.value]
                    for name in manager.channel_names
                ]
            )
            block_starts = np.arange(reduced.shape[0]) * window_size
            block_ends = np.minimum(
                block_starts + window_size,
                manager.recording_length,
            )
            reduced_time = (
                (block_starts + block_ends - 1) / 2
            ) * SAMPLE_INTERVAL

            raw_start = int(round(inicio / SAMPLE_INTERVAL))
            raw_end = int(round((inicio + ventana) / SAMPLE_INTERVAL))
            raw_time = np.arange(raw_start, raw_end) * SAMPLE_INTERVAL
            raw_zoom = recording[raw_start:raw_end]
            visible_blocks = (
                (reduced_time >= inicio) & (reduced_time <= inicio + ventana)
            )

            fig, axes = plt.subplots(
                manager.n_channels,
                1,
                sharex=True,
                figsize=(10, 2.5 * manager.n_channels),
            )
            axes = np.atleast_1d(axes)
            for channel_index, (ax, name) in enumerate(
                zip(axes, manager.channel_names)
            ):
                raw_values = raw_zoom[:, channel_index]
                reduced_values = reduced[visible_blocks, channel_index]
                raw_max = np.max(raw_values) if raw_values.size else np.nan
                reduced_max = (
                    np.max(reduced_values) if reduced_values.size else np.nan
                )
                ax.plot(
                    raw_time,
                    raw_values,
                    color="0.55",
                    linewidth=0.7,
                    alpha=0.65,
                    label=f"Original (Vmax={raw_max:.4g})",
                )
                ax.plot(
                    reduced_time[visible_blocks],
                    reduced_values,
                    marker="o",
                    markersize=3,
                    linewidth=1,
                    label=(
                        f"mean_per_window n={window_size} "
                        f"(Vmax={reduced_max:.4g})"
                    ),
                )
                if w_media.value:
                    ax.axhline(
                        manager.global_mean_max[name],
                        color="black",
                        linestyle="--",
                        linewidth=0.8,
                        label="Umbral global",
                    )
                ax.set_ylabel(name)
                ax.legend(loc="best", fontsize="small")
                ax.grid(True, alpha=0.3)

            axes[-1].set_xlabel("Tiempo (ms)")
            axes[-1].set_xlim(inicio, inicio + ventana)
            fig.suptitle(
                f"{manager.filename} - registro {w_registro.value} "
                f"(promedio de {window_size} muestras)"
            )
            fig.tight_layout()
        try:
            with BytesIO() as image_buffer:
                fig.savefig(image_buffer, format="png", bbox_inches="tight")
                grafica.value = image_buffer.getvalue()
        finally:
            plt.close(fig)

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
            estado["ventana"] = max(SAMPLE_INTERVAL, estado["ventana"] * factor)
            estado["inicio"] = int(centro - estado["ventana"] / 2)
            dibujar()

        return callback

    def al_cambiar_control(_):
        nonlocal actualizando
        if actualizando:
            return
        estado["inicio"] = w_inicio.value
        estado["ventana"] = max(SAMPLE_INTERVAL, w_ventana.value)
        dibujar()

    b_ini.on_click(ir_inicio)
    b_prev.on_click(mover(-1))
    b_prev2.on_click(mover(-0.5))
    b_next2.on_click(mover(0.5))
    b_next.on_click(mover(1))
    b_zin.on_click(cambiar_zoom(0.5))
    b_zout.on_click(cambiar_zoom(2))
    controls = [w_inicio, w_ventana, w_registro, w_media]
    if w_n is None:
        controls.append(w_evento)
    else:
        controls.append(w_n)
    for control in controls:
        control.observe(al_cambiar_control, names="value")

    fila_nav = widgets.HBox([b_ini, b_prev, b_prev2, b_next2, b_next, b_zin, b_zout])
    parametros = [w_registro, w_inicio, w_ventana]
    if w_n is None:
        parametros.append(w_evento)
    else:
        parametros.append(w_n)
    parametros.append(w_media)
    fila_parametros = widgets.HBox(parametros)
    interfaz = widgets.VBox([fila_nav, fila_parametros, grafica])
    display(interfaz)
    dibujar()
    return interfaz
