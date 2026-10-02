# Diseño de experimentos de eventos

## Módulos y clases

- `SimpleEventConfig`: configuración de la extracción del evento simple (umbrales, ventana y stride). Es un objeto serializable que vive fuera del proceso y puede validarse antes de ejecutar el experimento.
- `EventConfig`: extensión con la configuración del experimento: `word_length`, `simple_event`, metadatos del origen de la señal y versión del esquema.
- `EventExperiment`: con una secuencia explícita encapsula una serie de eventos; con un `DataManager` encapsula un experimento multicanal con análisis por canal y métricas conjuntas. Calcula tablas de conteos de palabras de longitud `L+1`. A partir de ellas se derivan:
  - `P(w)` por marginalización,
  - `P(s | w)` por normalización,
  - `P(w' | w)` por encadenamiento,
  - entropías de bloque y entropía condicional,
  - métodos de consulta por valor puntual y por distribución completa;
  - información mutua entre contextos alineados de longitud `L` para cada pareja de canales.
- `mutual_information`: función para calcular `I(X; Y)` a partir de observaciones categóricas alineadas. Para dos canales, `EventExperiment` codifica cada contexto completo de longitud `L` como una categoría antes de estimarla.

## Flujo de datos

1. La señal cruda se discretiza según `SimpleEventConfig`: `window` y `thresholds` se aplican en `DataManager`, y `stride` selecciona chunks de salida. La discretización disponible es `spike_detection` sobre eventos binarios.
2. Para un `DataManager`, `EventExperiment` conserva todas las grabaciones y canales, construye estadísticas por canal y calcula la información mutua entre contextos alineados de longitud `L` para cada pareja de canales. Los n-gramas y contextos se cuentan por grabación y nunca cruzan límites entre ensayos.
3. Para una secuencia explícita, `EventExperiment` construye la tabla de conteos de n-gramas solapados de longitud `L+1`.
4. Las distribuciones y métricas de bloque se derivan de las tablas de conteo; la información mutua se estima con las frecuencias empíricas de los pares de contextos alineados.
5. Las secuencias explícitas se guardan mediante `save(path)`. La construcción con `manager=` guarda automáticamente en `data_dir/experiments`, con nombre basado en origen, ventana y longitud de palabra; colisiones se resuelven con sufijos `_(n)`. `load()` reconstruye las métricas a partir de los eventos guardados.

## Fórmulas y propiedades estadísticas

Sea `L = word_length`. Para cada grabación se cuentan, sin cruzar sus límites,
las ventanas solapadas `w = (x_t, ..., x_(t+L))` de longitud `L+1`. Si
`c_(L+1)(w)` es su frecuencia y `N` la suma de todas las frecuencias:

- Distribución empírica de bloques:

  $$
  \hat{p}(w) = \frac{c_{L+1}(w)}{N}
  $$
- Frecuencia marginal de un bloque de longitud `L`:

  $$
  c_L(u) = \sum_a c_{L+1}(u,a), \qquad \hat{p}(u) = \frac{c_L(u)}{N}
  $$
- Entropías de bloque (en bits):

  $$
  H_L = -\sum_u \hat{p}(u)\log_2\hat{p}(u), \qquad
  H_{L+1} = -\sum_w \hat{p}(w)\log_2\hat{p}(w)
  $$
- Entropía condicional empírica:

  $$
  H(X_{t+L}\mid X_t,\ldots,X_{t+L-1}) = H_{L+1}-H_L
  = -\sum_{u,a}\hat{p}(u,a)\log_2\hat{p}(a\mid u)
  $$
- Probabilidad de un bloque largo bajo el modelo de orden `L`:

  $$
  P(x_0,\ldots,x_{n-1}) = P(x_0,\ldots,x_{L-1})
  \prod_{t=L}^{n-1}P(x_t\mid x_{t-L},\ldots,x_{t-1})
  $$
- Información mutua entre contextos alineados de longitud `L`:

  $$
  X_t^{(L)} = (X_t,\ldots,X_{t+L-1}), \qquad
  Y_t^{(L)} = (Y_t,\ldots,Y_{t+L-1})
  $$

  $$
  I(X^{(L)};Y^{(L)}) = \sum_{u,v}\hat{p}(u,v)
  \log_2\frac{\hat{p}(u,v)}{\hat{p}(u)\hat{p}(v)}
  $$

  Los contextos se toman en las posiciones con una ventana completa de longitud `L+1`, de modo que comparten el soporte de los conteos de bloques.

Estas definiciones cumplen la regla de la cadena para entropía y probabilidad;
las distribuciones se normalizan a uno cuando `N > 0`. Para variables discretas,
 $0 \leq H(X) \leq \log_2|\operatorname{supp}(X)|$ y $H(X\mid Y) \geq 0$.
La información mutua de contextos cumple $I(X^{(L)};Y^{(L)})=H(X^{(L)})-H(X^{(L)}\mid Y^{(L)})$, es no
negativa, vale cero si y solo si las variables son independientes y no supera
$\min(H(X^{(L)}),H(Y^{(L)}))$; para dos copias del mismo contexto,
$I(X^{(L)};X^{(L)})=H(X^{(L)})$.
Las estimaciones son de frecuencia
(plug-in), no corregidas por sesgo de muestra; las ventanas solapadas tampoco
son observaciones independientes. Las probabilidades de eventos no observados
valen cero, la probabilidad del bloque vacío vale uno, y las entropías se fijan
a cero si no hay ventanas completas (`N = 0`).

La matriz de transición indexa los estados de longitud `L` observados como
prefijo o sufijo de alguna ventana. El orden de sus filas y columnas es
`transition_states`. Cada fila con transiciones salientes se normaliza por sus
conteos; un estado observado solo como destino puede tener una fila de ceros
porque no se observó su continuación.

## Observación de diseño

`DataManager` aporta las grabaciones analógicas y las convierte en eventos binarios por ventana; `EventExperiment` configura esa extracción y calcula las estadísticas probabilísticas.