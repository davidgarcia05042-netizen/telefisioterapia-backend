# Decisiones técnicas — Plataforma de telefisioterapia integrada con el Servicio de Procesamiento

Documento común al backend
([`telefisioterapia-backend`](https://github.com/davidgarcia05042-netizen/telefisioterapia-backend))
y al panel del terapeuta
([`telefisioterapia-panel`](https://github.com/davidgarcia05042-netizen/telefisioterapia-panel)).
Reúne el contexto del proyecto, las decisiones de diseño con su justificación,
las reglas que la implementación respeta, el contrato con el Servicio de
Procesamiento y las convenciones de trabajo.

## Contexto

Trabajo de grado (Universidad Antonio Nariño, Ingeniería de Sistemas) de Juan
David García Rubio y Camila Sandoval Ruiz, dirigido por Ph.D. David Alberto
Herrera Álvarez, grupo de investigación LACSER/Bioingeniería.

La plataforma de telefisioterapia fue desarrollada por otro equipo. Estos
repositorios son una **copia propia**, hecha el 2026-10-15 porque no había
acceso al Firebase ni a la base de datos de la plataforma original. La copia se
despliega en Render y se conecta al **Servicio de Procesamiento** del trabajo
de grado, que calcula ángulos articulares con MediaPipe y los devuelve por
webhook.

**Planes de trabajo:** el trabajo se hizo por fases, en orden, cada una con su
criterio de aceptación:
- versión 1 de la integración (fases 0 a 8): implementada y desplegada en
  Render; etiqueta `v1-integracion` en los tres repositorios de trabajo;
- versión 2 (fases V1 a V4, decidida el 2026-10-16): todas las métricas salen
  del servicio y el navegador queda como previsualización. Es la vigente;
- integración de la app móvil del paciente (fases M0 a M5);
- mejoras de la interfaz del panel (fases F1, F2, F3 y V), sin cambios de lógica.

Los comentarios del código citan los códigos de esos planes (por ejemplo "M1"
o "fase 3.2"); su significado está en "Planes de trabajo y códigos citados en
el código", al final de este documento.

URLs en Render: backend `https://platform-api-4qtl.onrender.com`, panel
`https://platform-panel.onrender.com`, servicio `https://pose-api-77y6.onrender.com`.

**Criterio de diseño general:** el trabajo tenía plazo fijo (entrega final
antes del 2026-10-22). Ante dos soluciones válidas se eligió la que agregaba
menos piezas nuevas.

## Las piezas

| Pieza | Repositorio | Origen | Tecnología | Despliegue |
|---|---|---|---|---|
| Backend | [`telefisioterapia-backend`](https://github.com/davidgarcia05042-netizen/telefisioterapia-backend) | copia de `telefisioterapia-uan/telerehabilitation_API` | Django 6 + DRF, Postgres | Render Web Service (Docker), `oregon` |
| Panel del terapeuta | [`telefisioterapia-panel`](https://github.com/davidgarcia05042-netizen/telefisioterapia-panel) | copia de `santivarelaagent-cmd/telerehabilitacion_therapist_fe` | Vue 3 (Vue CLI), MediaPipe Tasks en el navegador | Render Static Site |
| App del paciente | [`telefisioterapia-app`](https://github.com/davidgarcia05042-netizen/telefisioterapia-app) | copia de `santivarelaagent-cmd/therapp` | Android, Kotlin, Jetpack Compose | APK |
| Servicio de Procesamiento | [`servicio-procesamiento-pose`](https://github.com/davidgarcia05042-netizen/servicio-procesamiento-pose) | propio | FastAPI + Celery + Redis, MediaPipe Heavy | Render |
| Videos | — | — | Supabase Storage, bucket `videos`; o Cloudflare R2 con `STORAGE_BACKEND=r2` | — |

Repositorios de investigación relacionados: [`entorno-pruebas`](https://github.com/davidgarcia05042-netizen/entorno-pruebas)
(comparación de modelos contra gold standard) y
[`pose-benchmark`](https://github.com/davidgarcia05042-netizen/pose-benchmark)
(tiempo, CPU y memoria por modelo).

**La app del paciente** quedó fuera del alcance de las versiones 1 y 2: la app
original nunca cerró el flujo (subía a Firebase y no avisaba al backend). Se
integró después (fases M0 a M5): el backend ganó el rol Paciente y sus
permisos (M1), y la app graba, envía el video por el mismo flujo que el panel y
muestra el resultado del servicio (M2 a M5). El detalle está en
`docs/DECISIONES_TECNICAS.md` del repositorio de la app.

## Flujo

1. El terapeuta (o el paciente desde la app) sube un video. El backend emite
   una URL firmada de Supabase y el cliente sube directo.
2. El cliente le pasa al backend la URL pública:
   - video de **referencia** → `POST /exercises/{id}/video/`
   - video del **paciente** → `POST /exercise_results`
3. El backend, en un hilo en segundo plano, descarga el video **una vez** y lo
   envía al servicio (`POST /video/`). En v1 era un job por articulación; en v2
   es **un solo job con todas las ternas**.
4. El servicio responde 200 de inmediato, corre MediaPipe **una vez** y, al
   terminar, llama al `resultsEndpoint` con el callback v2 (`joints` + `frames`
   con los 33 puntos).
5. El backend verifica la firma, guarda máximo y mínimo por articulación y el
   **movimiento completo**, y recalcula el estado del resultado.
6. El panel muestra gráficas, exportaciones y visor 3D **desde el movimiento del
   servidor**. El análisis en el navegador es solo previsualización.

## Reglas de diseño

- **Alcance de los cambios al Servicio de Procesamiento.** Durante la
  integración, el servicio se modificó solo dentro del alcance de
  la versión 2 (contrato v2), siempre en una rama y con PR. El merge
  a `main` lo hacía el equipo, porque cada merge a `main` se despliega solo.
  Un cambio fuera de ese alcance requería acuerdo previo del equipo.
- **La función del ángulo 2D validado (`_angle_deg` en `pose_analysis.py` del
  servicio) no se modifica.** Es la medición clínica contrastada contra gold
  standard. El ángulo 3D es una función aparte y se presenta como
  visualización, no como medición validada.
- **El contrato v1 sigue vigente:** una solicitud con una sola terna recibe
  exactamente el callback original. Las pruebas del servicio previas a la v2
  siguen pasando sin cambiar sus expectativas.
- **ID de `SkeletonPoint` = índice de landmark de MediaPipe (0–32).** El panel
  lo usa así (`landmark[point.id]`) y el servicio lo valida así.
- **Un ángulo = `[vecino1, vértice, vecino2]`**, medido en el vértice, en 2D.
  Vecino = el `left_point`/`right_point` personalizado del ejercicio y, si no
  hay, el del esqueleto. En el modelo, "left/right" significa "los dos
  vecinos", no el lado anatómico.
- **Referencia y paciente se miden con el mismo motor** (el servicio, MediaPipe
  Heavy, 2D). Por eso el botón "Enviar resultados" del panel está oculto: el
  análisis en el navegador es solo previsualización, nunca escribe ángulos y
  **nunca sobrescribe** el movimiento que viene del servidor.
- **HMAC idéntico al del servicio:** `X-Timestamp` + `X-Signature` =
  hex(HMAC-SHA256(secreto, `"<timestamp>." + cuerpo_crudo`)), ventana de 300 s,
  `hmac.compare_digest`. Al enviar, se prepara la solicitud y **después** se
  firma `prepared.body`. Al recibir, se lee `request.body` **antes** que
  `request.data`.
- **Callbacks:** idempotentes (`update_or_create`), ángulos `null` = error de la
  articulación, estado recalculado con `select_for_update` sobre la fila padre.
  **Un dato raro nunca produce un 500**, porque el servicio reintenta los 5xx
  durante 72 h. Respuestas: 401 firma inválida, 404 recurso inexistente, 400
  cuerpo ilegible.
- **El servicio responde HTTP 200 incluso cuando rechaza un video.** El éxito se
  confirma con `status == "ok"` y `job_id` en el JSON.
- **Solo se descargan URLs del bucket propio** (protección contra SSRF): el
  prefijo público de Supabase o `R2_PUBLIC_BASE_URL` + "/"
  (`storage/backend.py`). Durante la transición a R2 conviven los dos.
- **El movimiento (`movement` de Exercise y ExerciseResult) nunca se carga en
  listados ni en serializers anidados.** Pesa unos 20 MB por fila ya
  decodificado (60 s a 30 fps); con 600 ejercicios procesados, un listado que
  lo cargaba necesitaba unos 12 GB y el backend (512 MB) murió por memoria.
  - El manager por defecto lo aplaza (`therapy/models/managers.py`).
  - Solo lo leen `GET /exercises/{id}/movement/` y
    `GET /exercise_results/{id}/movement`.
  - Una FK directa (`resultado.exercise`) usa el manager base: hay que aplazar
    `exercise__movement` (`therapy/querysets.py`).
  - `therapy/test_listing_memory.py` falla si un listado vuelve a cargarlo o
    si sus consultas crecen con los ejercicios.
- **Si cambia `POSE_MODEL` en el servicio, hay que reprocesar los videos de
  referencia:** de lo contrario, referencia y paciente quedan medidos con
  modelos distintos.
- **Datos personales (Ley 1581):** videos de prueba sin datos de pacientes
  reales, porque el bucket es de lectura pública. Del nombre del archivo subido
  solo se conserva la extensión.
- **Ningún secreto en el código ni en `render.yaml`:** van como variables de
  entorno.

## Contrato con el Servicio de Procesamiento

`POST {PROCESSING_SERVICE_URL}/video/` — multipart firmado:

| Campo | Contenido |
|---|---|
| `video` | El archivo (≤ 50 MB; mp4, mov, webm, mkv, avi) |
| `points` | v1: una terna, ej. `"[11, 13, 15]"`. v2: lista de 1 a 12 ternas, ej. `"[[23, 25, 27], [24, 26, 28]]"` |
| `exercise` | Texto libre, ej. `"exercise:12"` |
| `resultsEndpoint` | URL https del callback (host en `CALLBACK_ALLOWED_HOSTS` del servicio) |

Respuesta inmediata: `{"status": "ok"|"error", "internal_status_code": ..., "job_id": ...}`.
Límite: 10 solicitudes por minuto.

Callbacks (firmados por el servicio), en v2 sin `point_tracked`:
- Paciente: `/exercise_results/{id}/results` (sin barra final)
- Referencia: `/exercises/{id}/video_results/` (con barra final)

Cuerpo v1: `results.points` es una lista de **frames**; `max_angle` y
`min_angle` van en el nivel superior.

Cuerpo v2 (`contract_version: 2`): `results.joints` (una entrada por terna, con
`points`, `vertex`, `max_angle`, `min_angle`) y `results.frames` (por frame:
`t`, los 33 `landmarks` `[x,y,z,visibilidad]`, los 33 `world_landmarks`
`[x,y,z]`, `angles_2d` y `angles_3d` en el orden de `joints`). El primer nivel
lleva `max_angle`/`min_angle` en `null`. El detalle completo está en el README
del Servicio de Procesamiento. La plataforma asocia cada `joint` a su
articulación **por la terna completa**.

## Bloqueadores resueltos durante la integración

Estaban listados como abiertos en la documentación del servicio antes de la
integración.

1. **Disparo de los flujos:** el backend dispara ambos (referencia y paciente).
   El flujo de referencia funcionaba en el navegador; el del paciente nunca
   había existido.
2. **Esquema HMAC:** acordado; es el del servicio, sin cambios.
3. **Semántica de `points`:** confirmada con el código del panel: 3 índices de
   MediaPipe (vecino, vértice, vecino).
4. **Modelo en producción:** MediaPipe Heavy, fijo por `POSE_MODEL`. YOLO26-nano
   no está implementado en el servicio.

## Lecciones de la puesta en marcha (fase 8 y V4)

- **Dos buckets de Supabase, creados a mano** (el blueprint no los crea):
  - `videos`, de la plataforma, **público**: si no, la URL pública responde
    `Bucket not found` y el backend no puede descargar el video para enviarlo
    al servicio. La prueba inicial verificó la escritura firmada, pero no la
    lectura pública.
  - `pose-videos`, del servicio, **privado**, con un límite de 50 MB. No
    existía en la V4 y el servicio respondía `Bucket not found`.
    `SUPABASE_SECRET_KEY` va en pose-api y pose-worker (en el Dashboard de
    Render, nunca en el repositorio).
- **Los videos del servicio van a Supabase Storage, no a `pose-db`** (V4): con
  `STORAGE_BACKEND=postgres`, un video de celular dejaba sin memoria a la base
  (basic-256mb). El reinicio de pose-api era solo una consecuencia: sin base,
  `/health` responde 503.
- **Manual Deploy no aplica las variables del blueprint del servicio**: un
  cambio en `render.yaml` solo llega con **Manual Sync**. Render tampoco pide
  las variables `sync: false` en un blueprint ya creado; se cargan a mano.
- **Encender:** primero `pose-db`, porque la migración previa al despliegue
  (`alembic upgrade head`) falla si no puede conectarse. Después pose-api,
  pose-worker y pose-beat; luego se comprueba que `/health` responde 200 y
  recién entonces se suben videos.
- **Suspender, al revés:** sin videos en procesamiento, primero pose-api,
  después pose-worker y pose-beat, y `pose-db` al final. La guía completa está
  en el README del Servicio de Procesamiento ("Operación: encender y suspender").
- El `HEAD /` con 401 en los registros de `pose-api` es la sonda interna de
  Render, no la plataforma.
- El superusuario necesita el grupo "Admin" para entrar al panel (corregido en
  `create_superuser.py`). La vista "Subir video del paciente" está en "Mis
  pacientes", que es solo para terapeutas.

## Almacenamiento de videos en Cloudflare R2

- **Por qué:** R2 no cobra la salida de datos y cada video se lee varias veces:
  el panel lo reproduce y el backend lo descarga para enviarlo al servicio.
  Supabase sigue disponible y es el valor por defecto.
- **`STORAGE_BACKEND`** elige dónde se suben los videos nuevos:
  - sin definir o `supabase`: todo sigue en Supabase, como antes;
  - `r2`: las subidas nuevas van a R2;
  - un valor desconocido detiene el arranque.

  Es una variable del backend; no confundir con la del servicio, que tiene el
  mismo nombre y otro uso.
- **`POST /storage/upload_url` responde igual con los dos**
  (`upload_url`, `public_url`, `path`). En R2 la URL de subida es un PUT
  prefirmado con boto3 (`storage/r2.py`), que:
  - firma el Content-Type: el PUT debe mandar el mismo tipo que validó el backend;
  - vale 2 horas, como la de Supabase;
  - usa la misma clave de objeto (`exercises/<uuid><ext>`, `results/<uuid><ext>`).
- **Variables** (en el Dashboard de Render; en `render.yaml` solo las claves,
  con `sync: false`):
  - `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET`;
  - `R2_PUBLIC_BASE_URL`, la base pública sin barra final, ej. `https://pub-xxxx.r2.dev`.

  En un blueprint ya creado, Render no las pide: se cargan a mano.
- **Migración:** `python manage.py migrate_videos_to_r2 --dry-run` cuenta los
  videos y sus MB. Sin `--dry-run`, cada video de `Exercise` y `ExerciseResult`
  que sigue en Supabase se copia a R2 con la misma clave:
  - la descarga va en streaming y la subida en bloques de 8 MB;
  - se verifica el tamaño y solo entonces se reescribe la URL;
  - es idempotente y no borra nada de Supabase;
  - las filas "en procesamiento" se saltan, porque su aviso pendiente trae la
    huella de la URL vieja. Basta con correrlo de nuevo cuando terminen.
- **CORS del bucket** (se configura en Cloudflare): PUT, GET y HEAD desde el
  origen del panel, con el encabezado `Content-Type`. Sin eso fallan la subida
  desde el navegador y el `<video crossorigin="anonymous">` del panel.
- **Tamaño:** R2 no tiene límite por bucket. El tope de 50 MB lo ponen el
  panel, la app y la descarga hacia el servicio.
- **Producción:** la URL pública `r2.dev` tiene límite de tráfico y Cloudflare
  la destina al desarrollo. Para producción se recomienda conectar un dominio
  propio al bucket y usarlo como `R2_PUBLIC_BASE_URL`.

## Hallazgos de la plataforma original

Lo que se encontró en la plataforma original al integrarla. Los comentarios
del código que dicen "hallazgo" se refieren a esta lista.

- **El flujo del paciente nunca estuvo cerrado:** la app subía el video a
  Firebase y no avisaba al backend, y el backend ya no llamaba a ningún
  procesador.
- **Seguridad del backend:**
  - todas las cuentas se creaban con la misma contraseña fija (B1). Hoy cada
    cuenta nueva recibe una contraseña temporal aleatoria que el panel muestra
    una sola vez (DM4);
  - DRF no tenía permisos por defecto y varias vistas tenían mal escrito el
    atributo de permisos (B2): un paciente con token podía leer o modificar
    terapias, rutinas y ejercicios. Hoy todo está cerrado por defecto y cada
    vista declara su rol (M1);
  - `/exercise_results`, `/training` y `/scheduled_training/diffs` no exigían
    autenticación, y los endpoints de resultados aceptaban escrituras de
    cualquiera. Hoy los ángulos solo los escribe el servicio, con firma HMAC;
  - el token de sesión no vencía ni se revocaba. Hoy vence a las
    `TOKEN_TTL_HOURS` y se renueva al iniciar sesión si le quedan menos de
    `TOKEN_RENEW_HOURS`;
  - `create_superuser.py` traía una contraseña por defecto. Hoy la toma solo
    de una variable de entorno.
- **`POST /scheduled_training`** no validaba el cuerpo (un campo faltante, una
  dificultad de otro ejercicio o una fecha mal formada daban 500) ni usaba
  transacción. Hoy valida todo antes de escribir y crea todo o nada.
- **Panel:** el inicio de sesión imprimía la contraseña en la consola del
  navegador, había mensajes de depuración con datos de pacientes (G1, G2) y
  eliminar una terapia o una rutina no pedía confirmación (T1).

## Limitaciones conocidas y trabajo futuro

- **Envío al servicio sin cola de trabajos:** corre en un hilo del proceso web.
  Si el contenedor se corta durante un envío, el resultado pasa a error a los
  15 minutos y hay que volver a enviarlo. Una cola (Celery o similar) lo
  evitaría.
- **YOLO26-nano** no está implementado en el servicio; queda pendiente decidir
  qué modelos exponer.
- **Para la tesis:**
  - MediaPipe Heavy tiene 7,85° de error angular medio, en la franja moderada
    (5 a 10°, Gajdosik y Bohannon, 1987): los ángulos son apoyo al terapeuta,
    no una medición clínica definitiva;
  - un solo job por video reduce el cómputo de N pasadas de MediaPipe a una;
  - guardar la serie por frame permite medir el "tiempo dentro del rango
    esperado", porque el promedio oculta el riesgo clínico;
  - el ángulo 3D y las coordenadas mundiales son visualización, no validadas.

## Planes de trabajo y códigos citados en el código

El trabajo se hizo por planes en etapas, cada una con su criterio de
aceptación. Los comentarios del código citan sus códigos; esta sección los
reemplaza.

**Versión 1 de la integración (fases 0 a 8):**

| Código | Qué fue |
|---|---|
| Fase 0 | Preparación manual: cuentas, Supabase y Render |
| Fase 1 (1.1 a 1.6) | Despliegue del backend: dependencias, Dockerfile, `entrypoint.sh`, `settings.py`, `/health` y el blueprint `render.yaml` (1.6) |
| Fase 2 (2.1 a 2.3) | Modelo y datos: migración de esquema, carga de los 33 puntos del esqueleto con los índices de MediaPipe y sus vecinos (2.2), grupos y permisos |
| Fase 3 (3.1 y 3.2) | Subida de videos: URL firmada de subida (3.1) y validación anti-SSRF de las URL de video (3.2) |
| Fase 4 (4.1 a 4.4) | Envío al servicio: firma HMAC (4.1), resolución de ternas (4.2), `client.py` (4.3) y disparos (4.4) |
| Fase 5 (5.1 a 5.4) | Callbacks: autenticación, cuerpo, lógica dentro de una transacción (5.3) y respuestas |
| Fase 6 | Panel: subir videos con la URL firmada y avisar al backend |
| Fase 7 | Servicio de Procesamiento: solo configuración |
| Fase 8 | Prueba de punta a punta |
| Sección 11 | Variables de entorno de la integración |
| Tema 5 | Referencia y paciente se miden con el mismo motor: el análisis del navegador es solo previsualización |

**Versión 2 (fases V1 a V4, vigente):** todas las métricas salen del servicio.

| Código | Qué fue |
|---|---|
| D1 | Un solo job por video, con todas las articulaciones rastreadas |
| D2 | Contrato compatible: una terna recibe el callback v1; una lista de ternas, el v2 |
| D3 | La medición clínica sigue siendo el ángulo 2D validado; el 3D es visualización |
| D4 | El movimiento se guarda con la estructura de `JSON_MOVIMIENTO_ESTRUCTURA.md` del panel y se sirve en un endpoint aparte |
| 3.1 y 3.2 | Solicitud y callback del contrato v2 (ver "Contrato con el Servicio de Procesamiento") |
| V1 | Servicio: contrato v2 |
| V2 | Backend: un job por video y callback v2 (V2.6: tope propio de 25 MB para el cuerpo del callback) |
| V3 | Panel: gráficas, exportaciones y visor 3D desde el movimiento del servidor |
| V4 | Despliegue y prueba conjunta |

**App del paciente (fases M0 a M5):**

| Código | Qué fue |
|---|---|
| M0 | Preparación manual |
| M1 | Backend: permisos cerrados por defecto y rol Paciente, que solo ve lo suyo (404 si no es suyo) |
| M2 a M4 | App: base, grabar y enviar, estado y resultados |
| M5 | Prueba de punta a punta en un teléfono y APK |
| B1 a B4 | Hallazgos del backend: contraseña fija, permisos abiertos, sin rol Paciente, subida solo para el panel |
| DM1 a DM8 | Decisiones de la app (detalle en `docs/DECISIONES_TECNICAS.md` de la app); DM4: contraseña temporal aleatoria |
| Sección 4 | Flujo del paciente, pasos 1 a 10 (detalle en la app) |

**Interfaz del panel (fases F1, F2, F3 y V):** F1 seguridad, F2 funcionalidad,
F3 ajustes por la app móvil (contraseña temporal, dificultad asignada) y V
visual. Sin librerías de UI nuevas y sin tocar la lógica de la versión 2.

| Código | Qué fue |
|---|---|
| G3 | Sesión vencida: ante un 401, limpiar la sesión y llevar al login |
| G4, G5 | Tipografía en `body`; foco visible con teclado |
| G8 | Estado de carga en las vistas de detalle |
| G11 | Un único formato de fechas (español de Colombia, hora de Bogotá) |
| G12 | Botón "Volver" en las vistas de detalle |
| N2, N4, N5 | Barra lateral con enlaces reales y fija; menú de usuario con clic |
| I1, I2 | Inicio con un resumen útil; login que identifica el producto |
| T1 | Confirmar antes de eliminar, en todas las listas |
| E1, E5, E7 | Alto máximo del reproductor; editar y borrar dificultades; configurador de ángulos usable a unos 800 px |
| P3, P4, P7 | Estados coherentes de rutina y resultado; un solo mensaje de error claro; acciones de tamaño uniforme |
| V1 | Nombres en español de los 33 puntos en el visor 3D |

## Convenciones

- Identificadores en inglés; docstrings y comentarios largos en español (el
  equipo sustenta en español). Se comenta el **porqué** de cada decisión no
  obvia.
- Entorno de desarrollo del equipo: Windows, PowerShell, VS Code, Python 3.12,
  venv por proyecto (`venv\Scripts\Activate.ps1`). Node 22 para el panel.
- Todo cambio se verifica con pruebas antes de entregarse
  (`python manage.py test` en el backend; `npm run build` en el panel). En las
  pruebas, el servicio HTTP, Supabase y R2 siempre se simulan.
- Commits pequeños, uno o varios por fase del plan.

## Referencias en el código

- Ángulos en el navegador: la previsualización de
  `src/components/exercises/ExerciseVideoPlayer.vue` (panel). El análisis de
  fondo, `backgroundAnalysisService.js`, se eliminó en la v2: todas las
  métricas vienen del servicio (sigue en la etiqueta `v1-integracion` de los
  repositorios de trabajo).
- Tabla de los 33 landmarks con `codename`: `JSON_MOVIMIENTO_ESTRUCTURA.md` (panel).
- HMAC y cliente de referencia: `app/utils/hmac_auth.py` y
  `app/tools/demo_send.py` (servicio).
- Encabezados de claves de Supabase: `app/platforms/render/supabase_storage.py` (servicio).
- Callbacks: `ExerciseResultResultsViewSet` y `ExerciseViewSet.video_results`
  (backend), que reemplazaron a los originales.
