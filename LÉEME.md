# Gajo

Conversor de archivos sin conexión para Windows. Es una versión propia, inspirada
en cómo funciona Tangerine (Mac): no usa ni copia su código, su diseño ni su marca.

## Cómo se usa

1. Empieza a arrastrar uno o varios archivos (Explorador, escritorio, correo).
2. Sin soltar el mouse, toca una tecla:
   - **Shift**: convertir formato (JPG, PNG, PDF, DOCX, MP3, MP4…).
   - **Ctrl**: herramientas (editar, anotar, recortar, cortar, unir, normalizar…).
   - **Alt**: acciones (repisa flotante, copiar, mover, copiar ruta, terminal, mostrar, ZIP).
3. Suelta sobre la opción. Si sueltas en el centro, se cancela.

Las herramientas que necesitan decisiones abren una ventana con vista previa; el
trabajo pesado corre de fondo y avisa con una notificación cuando termina.

Mientras arrastras puedes tocar otra tecla para cambiar de anillo. El resultado
queda al lado del original, con un nombre nuevo; el original nunca se modifica.
Todo ocurre en este PC: nada se sube a internet.

## Qué convierte (288 combinaciones)

| Tipo | Entra | Sale |
|---|---|---|
| Imágenes | JPG, PNG, WebP, HEIC, TIFF, AVIF, BMP, GIF, SVG | JPG, PNG, WebP, HEIC, TIFF, AVIF, BMP, PDF (GIF → MP4) |
| Audio | MP3, M4A, WAV, FLAC, OGG, Opus, AIFF, WMA, AAC | MP3, M4A, WAV, FLAC, OGG, Opus, AIFF, WMA |
| Video | MP4, MOV, MKV, WebM, AVI, WMV | MP4, MOV, MKV, WebM, AVI, WMV, GIF, MP3 |
| PDF | PDF | DOCX, PPTX, XLSX (tablas), JPG/PNG a 300 dpi, EPUB, TXT, MD |
| Documentos | DOCX, DOC, ODT, RTF, TXT, MD, HTML, EPUB | PDF, DOCX, ODT, RTF, TXT, MD, HTML, EPUB |
| Presentaciones | PPTX, PPT, ODP | PDF, PPTX, JPG, PNG |
| Planillas | XLSX, XLS, ODS, CSV, TSV | XLSX, ODS, CSV, TSV, PDF |
| Subtítulos | SRT, VTT | SRT, VTT, TXT |
| Comprimidos | ZIP, TAR, TAR.GZ, RAR, 7Z | ZIP, TAR, TAR.GZ, o extraer |

## Captura de pantalla (reemplaza a Lightshot)

- **Ctrl+Impr Pant**: congela la pantalla; arrastra para elegir la zona (se ajusta con sus manillas).
  Herramientas: mover, lápiz, línea, flecha, rectángulo, marcador, texto y goma; color y grosor
  (la rueda del mouse cambia el grosor, o el tamaño de la goma).
- Cada dibujo es un objeto propio: **clic derecho y arrastrar** lo mueve con cualquier herramienta;
  con **Mover** (V) se elige y se estira desde sus puntas; **Supr** lo borra; doble clic edita un texto.
- La **goma** corta solo lo que toca: parte trazos y líneas en dos sin borrar el resto.
- **Grosor con la rueda**: mientras dibujas, la rueda cambia el grosor de ese trazo en vivo. Ya dibujado,
  **Ctrl+rueda** con el cursor encima cambia solo ese dibujo (en un texto, el tamaño de letra).
  Una vuelta seguida se deshace con un solo Ctrl+Z.
- **Shift** deja lápiz, marcador, difuminar, goma, líneas y flechas solo en horizontal o vertical, y
  los rectángulos cuadrados.
- **Difuminar (D)**: pincel que pixela lo que no se quiere mostrar (RUT, teléfonos); es un objeto más
  (se mueve, se borra, la goma lo corta).
- **Texto con fondo**: botón "Aa" con fondo en la barra; el texto va sobre un recuadro del color elegido.
- **Ctrl+C** copia y cierra; **Ctrl+S** guarda (recuerda la última carpeta); **Ctrl+Z / Ctrl+Y**; **Esc** sale.
- **Shift+Impr Pant**: guarda la pantalla completa.
- **Historial de capturas** (menú del ícono, o el botón con reloj de la barra de la captura / Ctrl+H,
  que cierra la captura guardándola si tenía dibujos): cada captura que se copia, guarda o de la que se copia
  texto queda guardada sola, con sus dibujos (también si se cierra con Esc después de dibujar).
  Desde ahí: copiar otra vez, **seguir editando** (los dibujos siguen siendo objetos movibles; al copiar
  se actualiza la misma entrada), guardar como, mostrar el archivo o borrar; doble clic copia y se puede
  arrastrar una miniatura a otra app. **Copiar última captura** en el menú la recupera de un clic.
  Se guarda en `%LOCALAPPDATA%\Gajo\historial` (imagen final, zona sin dibujos y los dibujos en JSON),
  60 días y máximo 500 (`historial_dias` / `historial_max` en config.json).
- Atajos: letras V, P, L, A, R, M, T, E para cambiar de herramienta. Los atajos globales se cambian
  desde el menú del ícono → **Cambiar atajos de captura…** (clic en el atajo y presionar la nueva
  combinación; si otro programa la usa, avisa y deja la anterior). Si otro programa los tiene tomados
  al partir, Gajo avisa y los toma apenas se liberan.

## Instalar en otros PC

`python crear_paquete.py` arma `dist/Gajo-<versión>.zip` (≈350 KB). En el otro PC: descomprimir todo y
doble clic en **Instalar Gajo.bat**. Instala lo que falte con winget (Python 3.12, ffmpeg, Pandoc y,
si se acepta, LibreOffice) y las librerías de `requirements.txt` (≈300 MB la primera vez), copia la
app a `%LOCALAPPDATA%\Programs\Gajo`, crea el acceso del menú Inicio y pregunta si parte con Windows.
**Desinstalar Gajo.bat** quita solo esa copia y sus accesos.

## Escanear documento desde foto

Foto de un documento (cédula, escritura, certificado) + **Ctrl → ESCANEAR**. Gajo encuentra los bordes
de la hoja (se ajustan arrastrando las esquinas, con lupa), la endereza calculando la proporción real
con el modelo de cámara de Zhang y He, quita sombras y la deja en **Color** (conserva timbres y firmas),
**Grises**, **Blanco y negro** u **Original**. Reconoce Carta, Oficio, A4 y cédula, o se elige a mano.
Varias fotos → un PDF con una página por foto; con **Texto buscable (OCR)** el PDF queda seleccionable.
Motor en `gajo/scanner.py`, ventana en `gajo/panels/scan_panel.py`, prueba en `tests/probar_escaner.py`.

## Leer texto (OCR)

Sin internet, con el motor Tesseract que trae PyMuPDF y el modelo de español de
`modelos/tessdata` (no hay que instalar Tesseract aparte).

- **Imagen → Shift → TEXTO**: guarda el texto en un `.txt` al lado.
- **PDF escaneado → Ctrl → OCR**: crea `nombre-ocr.pdf` con una capa de texto invisible; se ve y pesa
  casi igual, pero se puede seleccionar, copiar y buscar con Ctrl+F.
- **PDF → Shift → TXT**: si alguna página es escaneada, la lee con OCR.
- **Captura de pantalla → Copiar texto (Ctrl+T)**: lee la zona elegida y deja el texto en el portapapeles.

Lee muy bien texto impreso (≈98% de palabras en las pruebas); le cuestan símbolos chicos (°, ²) y la
letra manuscrita.

## Herramientas (Ctrl)

| Tipo | Con ventana | Directas |
|---|---|---|
| Imágenes | Editar (luz, color, detalle, B/N), Anotar (flecha, lápiz, texto, rectángulo, elipse, resaltador, números, lupa, imagen), Fondo (degradados, colores, imagen; margen, esquinas, sombra, proporción), Recortar (proporciones, píxeles exactos, girar, voltear), Ocultar (sólido o desenfoque; detecta caras y texto en el PC), Comprimir (calidad, peso objetivo o %; tamaño máximo), Metadatos (ver, editar o quitar fecha, cámara, autor, GPS), Collage (cuadrícula, fila, columna, destacada; orden) | Crear PDF |
| Video | Cortar, Encuadrar (con giro), Velocidad (0,25× a 4×, mantiene el tono), Fotogramas, Dividir (cortes o partes iguales), Ocultar por tramos, Unir (en orden) | Comprimir, Sin audio |
| Audio | Normalizar (LUFS, rango, pico; mide el volumen actual), Cortar (con silencios automáticos), Canales (mono/estéreo, volumen por lado), Pitido, Visualizador (onda animada o imagen fija a MP4), Unir | Comprimir |
| PDF | Organizar páginas (ordenar, girar, duplicar, quitar, mismo ancho), Unir (en orden) | Comprimir, Dividir, Extraer imágenes |

## Piezas

- `gajo.pyw`: arranque (doble clic, o el acceso "Gajo" del menú Inicio).
- `gajo/engines.py`: las conversiones (Pillow, PyMuPDF, ffmpeg, LibreOffice, Pandoc).
- `gajo/catalog.py`: qué opciones aparecen según el tipo de archivo.
- `gajo/wheel.py`: la rueda. `gajo/watcher.py`: detecta la tecla durante el arrastre.
- `gajo/shelf.py`: la repisa flotante. `gajo/app.py`: ícono junto al reloj y trabajos.
- `gajo/editing.py`: motores con parámetros de las ventanas (imagen, video, audio, PDF).
- `gajo/capture.py`: la captura de pantalla; `gajo/shapes.py`: los dibujos como objetos (mover,
  estirar, goma); `gajo/hotkey.py`: atajos globales de Windows.
- `tests/probar_captura.py`: prueba la captura con una pantalla de mentira (no muestra nada).
- `gajo/panels/`: las ventanas (`base`, `canvas`, `media`, `icons` y un archivo por tipo).
- `modelos/`: detector de caras YuNet (modelo oficial de OpenCV, funciona sin internet).
- `tests/probar_motores.py`: prueba todas las conversiones con archivos de muestra.
- `tests/probar_paneles.py`: abre cada ventana sin mostrarla, la usa y verifica el resultado.
- `tests/probar_flujo_panel.py`: recorrido rueda → ventana → trabajo de fondo.
- `tests/probar_rueda.py`: prueba de punta a punta con un arrastre real (mueve el mouse).

Teclas configurables en `%LOCALAPPDATA%\Gajo\config.json`. Registro de errores en
`%LOCALAPPDATA%\Gajo\gajo.log` (menú del ícono → "Ver registro").

Requiere en el PC: Python 3.12 con PySide6, Pillow, pillow-heif, PyMuPDF, pdf2docx,
python-pptx, openpyxl, numpy, opencv-python-headless y piexif; además ffmpeg,
LibreOffice y Pandoc.
