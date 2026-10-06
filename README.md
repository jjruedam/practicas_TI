# Análisis de la información mutua entre las neuronas DV y LP del sistema nervioso estomatogástrico del cangrejo azul

## Resumen

En neurociencia, el estudio de los sistemas neuronales suele apoyarse en la teoría de la información [referencia pendiente] y, en particular, en el análisis de la información mutua entre las neuronas que los componen. En este informe presentamos el desarrollo experimental, el tratamiento y análisis de los datos empleados para estudiar la información mutua en el sistema nervioso estomatogástrico (STNS, por sus siglas en inglés) [referencia pendiente] del cangrejo azul (*Callinectes sapidus*).

El STNS es un generador central de patrones (CPG, por sus siglas en inglés) [referencia pendiente], un tipo de sistema neuronal ampliamente estudiado por la regularidad de su actividad global. Los CPG generan ritmos de señales eléctricas de manera autónoma, lo que permite estudiarlos fuera del organismo. Aprovechamos esta propiedad para analizar la información mutua entre las neuronas DV y LP y comparar su actividad en tres condiciones: espontánea, durante la aplicación reiterada de un estímulo y durante la recuperación posterior, sin estímulo.

## Marco teórico

> Pendiente: definir los temas que se desarrollarán en esta sección.

### Modelos Neuronales, STNS y CPG

### Teoría de la información

## Diseño experimental

> Pendiente: resumir las diapositivas de la presentación 0.

## Tratamiento de datos

El objetivo del tratamiento de los datos es transformar las señales, originalmente pseudocontinuas, en representaciones discretas que permitan calcular la entropía y la información mutua entre canales. Para ello, se aplican dos técnicas de discretización que extraen características distintas de las señales.

El primer método, la detección de eventos, parte de la hipótesis de que la información relevante está contenida en los spikes. En las secciones siguientes describimos cómo se identifican. De forma intuitiva, el método divide cada registro en ventanas de tiempo fijas y representa cada ventana con un valor binario: 1 si contiene un spike y 0 si no lo contiene.

> Pendiente: describir el segundo método de discretización.

La discretización permite construir n-gramas para calcular la entropía y la información mutua. El análisis se centra en la evolución de la entropía de bloque de cada canal y de la información mutua entre DV y LP, por separado para las tres fuentes de datos, a medida que aumenta la longitud de los n-gramas.

### Descripción de los datos

> Pendiente:


![Registro original de ejemplo](resources/raw_record.png)

### Umbral de detección de spikes

Como se observa en la imagen, los spikes corresponden a variaciones extremas del potencial eléctrico respecto de la actividad habitual del canal. Por tanto, es posible distinguirlos mediante un umbral que identifique los valores asociados a estos eventos. Un umbral adecuado debe considerar tanto el comportamiento general de la señal como sus valores extremos. Desde el punto de vista estadístico, esto puede expresarse como una combinación de una medida de tendencia central y otra de los valores extremos.

En las pruebas realizadas, el punto medio entre la media y el máximo de cada registro, denominado `mean_max` local, separa satisfactoriamente ambas regiones. En la figura, este umbral se muestra en naranja.


![Umbrales locales y globales `mean_max`](resources/mean_max_threshold.png)

Al calcular un umbral con todos los datos del canal, el máximo puede aumentar considerablemente al incorporar más registros, mientras que la media tiende a variar menos. Para limitar la influencia de valores extremos, definimos el umbral global como el punto medio entre la media de todos los datos y el menor de los máximos de cada registro:

$$
\operatorname{mean\_max}_{\mathrm{global}} = \frac{\operatorname{mean}(\mathrm{data\_source}) + \min_{i \in \mathrm{records}}\left(\max(\mathrm{record}_i)\right)}{2}.
$$

En los datos analizados, se observa que $\operatorname{mean\_max}_{\mathrm{global}} \leq \operatorname{mean\_max}_{i,\mathrm{local}}$ para casi todos los registros $i$; la línea negra de la figura muestra el umbral global. Esta relación no está garantizada en general, pues las medias locales pueden variar. Cuando se cumple, cualquier spike detectado con el umbral local también queda por encima del umbral global.

Con las siguientes tablas puedra hacer una comparación del resultado de esta redefinición del `global_mean_max`:

| Canal | Media | Máximo | `mean_max` |
|:--|--:|--:|--:|
| LP | -0.033591 | 1.540833 | 0.753621 |
| DV | 0.043612 | 0.369568 | 0.206590 |
:`Registro 24`

| Canal | Media | Máximo |`old_mean_max`| `mean_max` |
|:--|--:|--:|--:|--:|
| LP | -0.033512 | 1.665039 | 0.815764 | 0.666685 |
| DV | 0.043721 | 1.351624 | 0.697673 | 0.199626 |
:`Global`

**Nota.** `mean_max_global` es un vector con un umbral por canal de la fuente de datos. En este informe, el nombre puede referirse al vector o al umbral de un canal concreto; el contexto permite distinguir ambos casos.

### Ventana temporal para la discretización

> Pendiente: obtención de la ventana temporal optima.

### Registro de eventos (spikes)

Una vez definida la ventana temporal, cada registro se divide en ventanas y se etiqueta cada una con un valor binario. Sin embargo, que una ventana contenga muestras por encima del umbral no basta para declararla como spike: puede contener solo el flanco ascendente o descendente de un evento.

Para caracterizar un spike, el detector busca tres muestras de tal manera que dos de ellas igualen o superen el umbral y una muestra central estrictamente mayor que ambos. Si para el análisis concideramos la función de potencial como una función continua, estas condiciones nos garantizan que la funcion es concava en el sub-intervalo de las muestras, ademas garantiza que en el mismo intervalo deben existir valores simetricos y por el teorema de Rolle un maximo local, no necesariemente la muestra central. En otras palabras, para algún desplazamiento $d > 0$ y una posición central $m$, se requiere que

$$
x[m-d] \geq \theta, \qquad x[m+d] \geq \theta, \qquad
x[m] > \max\bigl(x[m-d], x[m+d]\bigr),
$$

donde $x$ es la señal y $\theta$ el umbral del canal. La muestra central representa la concavidad; las otras dos confirman que la señal aciende y deciende dentro de la ventana. Basta con que exista un triplete que cumpla estas condiciones para etiquetar la ventana como spike. Por ello, una subida que continúa hasta el final de la ventana no se acepta: todavía no hay un flanco de bajada que permita verificar el pico.

![Casos de detección de spikes en ventanas temporales](resources/spike_detection_cases.svg)

En el borde entre ventanas, el detector añade las dos primeras muestras de la ventana siguiente al análisis de la ventana actual. Este solapamiento se usa solo para decidir si hay un spike, no modifica el tamaño de las ventanas ni la secuencia binaria resultante. Permite verificar un pico cuyo flanco de bajada cae justo después del límite, evitando perderlo por el corte temporal.

Esta verificación es importante porque la discretización convierte la señal en una secuencia de presencia/ausencia de spikes. Si cualquier cruce del umbral se contara como evento, fragmentos de spikes sin pico producirían falsos positivos. Estos alterarían las frecuencias de los n-gramas y, por tanto, las estimaciones de entropía e información mutua.


### N-gramas e información mutua

El análisis de detección de eventos parte de la hipótesis de que el ritmo del generador central de patrones (CPG) se establece mediante la comunicación entre las neuronas del STNS, en la que participan DV y LP. Una representación binaria de cada ventana conserva si hubo o no un spike, pero no describe el orden de los eventos ni posibles dependencias temporales. Para estudiar esos patrones, agrupamos símbolos consecutivos en palabras de longitud $N$, también llamadas $N$-gramas.

Sea $X^N$ la palabra de DV y $Y^N$ la palabra de LP observadas en el mismo conjunto de $N$ ventanas consecutivas. Para una longitud fija $N$, estimamos sus probabilidades a partir de las frecuencias relativas en todas las posiciones válidas del registro. Si el registro discretizado tiene longitud $T$, hay $T-N+1$ palabras posibles por canal. Definimos el conteo conjunto como

$$
C_N(x_N,y_N) = \sum_{t=1}^{T-N+1}
  \mathbb{1}\!\left[x_N^{(t)}=x_N \,\wedge\, y_N^{(t)}=y_N\right],
$$

de modo que

$$
\hat{p}_N(x_N,y_N) = \frac{C_N(x_N,y_N)}{T-N+1}, \qquad
\hat{p}_N(x_N) = \sum_{y_N}\hat{p}_N(x_N,y_N), \qquad
\hat{p}_N(y_N) = \sum_{x_N}\hat{p}_N(x_N,y_N).
$$

Estas frecuencias relativas son estimaciones empíricas de las probabilidades; al sustituirlas en la definición de información mutua obtenemos el estimador de frecuencias:

Para cada canal, la entropía de las palabras de longitud $N$ es

$$
H_N(X) = -\sum_{x_N} \hat{p}_N(x_N)\log_2\hat{p}_N(x_N).
$$

En los resultados, `block_entropy` representa $H_N(X)$. Así, las etiquetas describen la longitud configurada `word_length = N`.

$$
\hat{I}_N(DV,LP) = \sum_{x_N,\,y_N}\hat{p}_N(x_N,y_N)\,
  \log_2\frac{\hat{p}_N(x_N,y_N)}{\hat{p}_N(x_N)\,\hat{p}_N(y_N)}
$$

La suma se toma sobre las palabras observadas con probabilidad conjunta positiva. Las palabras se construyen sincronizadamente en ambos canales:

$$
x_N^{(t)} = \left(x_t,\,x_{t+1},\,\dots,\,x_{t+N-1}\right), \qquad
y_N^{(t)} = \left(y_t,\,y_{t+1},\,\dots,\,y_{t+N-1}\right)
$$

Esta cantidad depende también de la ventana temporal, pues esta determina la discretización de los datos. Como usamos una misma ventana al comparar los experimentos, omitimos ese parámetro en la notación. Al estar alineadas las palabras, la información mutua es simétrica: $\hat{I}_N(DV,LP)=\hat{I}_N(LP,DV)$.

Como las entropías marginales y la información mutua se calculan a partir de las mismas palabras de longitud $N$, se cumple $0 \leq \hat{I}_N(DV,LP) \leq \min(H_N(DV), H_N(LP))$.

En términos de las distribuciones verdaderas, la información mutua de las palabras no disminuye al aumentar $N$. En efecto, $X^N$ y $Y^N$ se obtienen como prefijos de $X^{N+1}$ y $Y^{N+1}$; añadir variables a cualquiera de los dos vectores no puede reducir la información mutua. Por tanto,

$$
I_{N+1}(DV,LP) \geq I_N(DV,LP).
$$

La desigualdad puede ser una igualdad si los símbolos añadidos no aportan información nueva. Esta propiedad se refiere a las distribuciones verdaderas. En un registro finito, las frecuencias se vuelven a calcular para cada longitud y el número de posiciones válidas disminuye de $T-N+1$ a $T-N$; por variabilidad muestral, el estimador $\hat{I}_N$ no tiene por qué crecer en cada paso. Además, al aumentar $N$ crece rápidamente el número de palabras posibles, por lo que algunas aparecen pocas veces o no aparecen; las estimaciones para longitudes grandes deben interpretarse con cautela.

> Pendiente: Corregir el desarrollo de la normalización

La información mutua total suele crecer con la longitud porque cada palabra contiene más símbolos. Para comparar cuánto acoplamiento informativo corresponde, en promedio, a cada ventana, consideramos también la cantidad normalizada

$$
\frac{\hat{I}_N(DV,LP)}{N},
$$

expresada en bits por ventana. Esta normalización permite comparar palabras de distintas longitudes, pero no tiene por qué ser creciente: la información total puede aumentar mientras que su promedio por símbolo disminuye. Así, $\hat{I}_N$ mide la información conjunta acumulada en palabras de longitud $N$, mientras que $\hat{I}_N/N$ describe su rendimiento medio por ventana.

### Segunda discretización

### Segunda estimación de la información mutua

## Resultados