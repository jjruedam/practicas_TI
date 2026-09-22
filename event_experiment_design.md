# Diseño de experimentos de eventos

## Módulos y clases

- `SimpleEventConfig`: configuración de la extracción del evento simple (umbrales, ventana de discretización, etiquetas, etc.). Es un objeto serializable que vive fuera del proceso y puede validarse antes de ejecutar el experimento.
- `EventConfig`: extensión con la configuración del experimento: `word_length`, `simple_event`, metadatos del origen de la señal y versión del esquema.
- `EventExperiment`: encapsula un canal/serie de eventos y calcula la tabla de conteos de palabras de longitud `L+1` como estructura persistida y mínima. A partir de ella se derivan:
  - `P(w)` por marginalización,
  - `P(s | w)` por normalización,
  - `P(w' | w)` por encadenamiento,
  - entropías de bloque y entropía condicional,
  - métodos de consulta por valor puntual y por distribución completa.
- `mutual_information`: función para calcular `I(X; Y)` entre dos canales del mismo origen usando la distribución conjunta de eventos alineados.

## Flujo de datos

1. La señal cruda se discretiza según `SimpleEventConfig`.
2. El `EventExperiment` toma la secuencia de eventos simples y construye la tabla de conteos de n-gramas solapados de longitud `L+1`.
3. Todas las distribuciones y métricas se derivan de esa tabla; no se persisten copias redundantes.
4. La configuración y las métricas se serializan en JSON para visualización y widgets.

## Observación de diseño

La clase `DataManager` del proyecto procesa grabaciones analógicas, no define un modelo probabilístico de eventos ni configura una ventana o contexto de Markov. 